"""Synthetic, offline Rel-15/16 assurance admission regression tests."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assurance.rel15_16 import METRICS, qualify_counter_windows, production_readiness

NOW = 2_000_000_000

def sample(start, a0, a1, s0, s1, **changes):
    row = dict(metric_id='amf_registration_success', cluster_uid='cluster',
               ns_id='ns', nf_uid='amf', source_id='prometheus-amf',
               window_start=start, window_end=start + 60, observed_at=start + 60,
               attempts_start=a0, attempts_end=a1, success_start=s0, success_end=s1,
               semantic_mapping_verified=True, source_coverage_verified=True,
               target_up=True, mapping_review_id='change-42')
    row.update(changes)
    return row


def pair():
    return [sample(NOW - 120, 0, 100, 0, 99), sample(NOW - 60, 100, 200, 99, 198)]


class Release1516Tests(unittest.TestCase):
    def test_healthy_candidate(self):
        result = qualify_counter_windows('amf_registration_success', pair(), now=NOW)
        self.assertEqual(result['status'], 'NORMAL_CANDIDATE')
        self.assertFalse(result['3gpp_conformant'])
        self.assertFalse(result['production_execution_allowed'])

    def test_consecutive_degradation_candidate(self):
        rows = [sample(NOW - 120, 0, 100, 0, 90), sample(NOW - 60, 100, 200, 90, 175)]
        result = qualify_counter_windows('amf_registration_success', rows, now=NOW)
        self.assertEqual(result['status'], 'DEGRADED_CANDIDATE')
        self.assertEqual(result['action'], 'observe_notify')

    def test_one_bad_window_does_not_trigger(self):
        rows = [sample(NOW - 120, 0, 100, 0, 90), sample(NOW - 60, 100, 200, 90, 189)]
        self.assertEqual(qualify_counter_windows('amf_registration_success', rows, now=NOW)['status'], 'NORMAL_CANDIDATE')

    def test_fail_closed(self):
        changes = (
            {'mapping_review_id': ''}, {'semantic_mapping_verified': False},
            {'source_coverage_verified': None}, {'target_up': False},
            {'nf_uid': 'other'}, {'metric_id': 'different'},
            {'attempts_end': 10}, {'success_end': 250},
            {'observed_at': NOW - 200}, {'observed_at': NOW + 5},
            {'window_start': NOW - 90}, {'attempts_start': 201},
            {'success_end': 201}, {'attempts_end': 2.2},
            {'attempts_end': True}, {'window_end': float('nan')},
            {'source_id': 'invalid\nidentity'},
        )
        for change in changes:
            with self.subTest(change=change):
                rows = pair()
                rows[1].update(change)
                self.assertEqual(qualify_counter_windows('amf_registration_success', rows, now=NOW)['status'], 'UNAVAILABLE')

    def test_minimum_sample_not_zero_filled(self):
        rows = [sample(NOW - 120, 0, 5, 0, 4), sample(NOW - 60, 5, 10, 4, 8)]
        self.assertEqual(qualify_counter_windows('amf_registration_success', rows, now=NOW)['status'], 'INSUFFICIENT')

    def test_unsupported_or_invalid_policy(self):
        self.assertEqual(qualify_counter_windows('unknown', pair(), now=NOW)['status'], 'UNAVAILABLE')
        self.assertEqual(qualify_counter_windows('amf_registration_success', pair(), now=NOW,
                                                 failure_threshold=1)['status'], 'UNAVAILABLE')
        self.assertEqual(qualify_counter_windows('amf_registration_success', pair(), now=NOW,
                                                 min_attempts=True)['status'], 'UNAVAILABLE')

    def test_only_two_or_more_independent_windows(self):
        self.assertEqual(qualify_counter_windows('amf_registration_success', pair()[:1], now=NOW)['status'], 'UNAVAILABLE')
        self.assertEqual(qualify_counter_windows('amf_registration_success', pair() * 5, now=NOW)['status'], 'UNAVAILABLE')

    def test_report_never_enables_execution(self):
        healthy = qualify_counter_windows('amf_registration_success', pair(), now=NOW)
        reports = ({k: healthy for k in METRICS}, {}, {'bad': healthy})
        for report in reports:
            out = production_readiness(report)
            self.assertFalse(out['production_autonomous_remediation_ready'])
            self.assertFalse(out['release_15_16_conformance_certified'])
            self.assertEqual(out['mode'], 'OBSERVE_ONLY')

if __name__ == '__main__':
    unittest.main()
