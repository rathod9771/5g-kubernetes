"""Offline, no-cluster regression tests for experimental RAN evidence inventory."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hf_ran_readiness as gate

COMMIT = 'a' * 40


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, scenario):
        evidence = self.root / 'capture.txt'
        evidence.write_text('synthetic evidence - NOT radio acceptance\n')
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        return {
            'schema': '5g-kubernetes/hf-ran-evidence-v1',
            'scenario': scenario,
            'source_commit': COMMIT,
            'artifacts': {'capture': {'path': 'capture.txt', 'sha256': digest}},
            'checks': {check: ['capture'] for check in gate.required_checks(scenario)},
        }

    def test_missing_all_evidence_fails_closed(self):
        result = gate.assess({}, self.root, 'hcran-oai', COMMIT)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['checks_inventoried'], 0)
        self.assertFalse(result['production_authorized'])
        self.assertTrue(result['manual_technical_review_required'])

    def test_complete_inventory_still_never_authorizes_production(self):
        for scenario in gate.SCENARIOS:
            with self.subTest(scenario=scenario):
                result = gate.assess(self.fixture(scenario), self.root, scenario, COMMIT)
                self.assertEqual(result['status'], 'EVIDENCE_INVENTORY_COMPLETE')
                self.assertFalse(result['production_authorized'])
                self.assertFalse(result['3gpp_conformance_verified'])
                self.assertEqual(result['checks_inventoried'], len(gate.required_checks(scenario)))

    def test_tampering_blocks_coverage(self):
        manifest = self.fixture('fran-oai')
        (self.root / 'capture.txt').write_text('tampered\n')
        result = gate.assess(manifest, self.root, 'fran-oai', COMMIT)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['invalid_artifacts'], {'capture': 'HASH_MISMATCH'})

    def test_path_traversal_and_symlink_escape_fail_closed(self):
        manifest = self.fixture('hcran-srsran')
        for path in ('../private.txt', '/etc/passwd', r'c:\\private', './capture.txt'):
            with self.subTest(path=path):
                manifest['artifacts']['capture']['path'] = path
                result = gate.assess(manifest, self.root, 'hcran-srsran', COMMIT)
                self.assertEqual(result['status'], 'BLOCKED')
        outside = self.root.parent / 'private-outside-evidence.txt'
        (self.root / 'outside').symlink_to(outside)
        manifest['artifacts']['capture']['path'] = 'outside'
        self.assertEqual(gate.assess(manifest, self.root, 'hcran-srsran', COMMIT)['status'], 'BLOCKED')

    def test_cross_scenario_and_commit_mismatch(self):
        manifest = self.fixture('fran-srsran')
        self.assertIn('SCENARIO_MISMATCH', gate.assess(manifest, self.root, 'fran-oai', COMMIT)['blockers'])
        self.assertIn('SOURCE_COMMIT_UNVERIFIED', gate.assess(manifest, self.root, 'fran-srsran', 'b' * 40)['blockers'])

    def test_required_hcran_and_fran_checks_differ(self):
        self.assertIn('simultaneous_du_service', gate.required_checks('hcran-oai'))
        self.assertIn('small_ue_traffic', gate.required_checks('hcran-srsran'))
        self.assertNotIn('small_ue_traffic', gate.required_checks('fran-oai'))
        self.assertIn('fog_processing', gate.required_checks('fran-oai'))
        self.assertIn('local_breakout_scope', gate.required_checks('fran-srsran'))

    def test_broken_reference_cannot_count_as_covered(self):
        manifest = self.fixture('hcran-oai')
        manifest['checks']['macro_ue_traffic'] = ['missing-id']
        report = gate.assess(manifest, self.root, 'hcran-oai', COMMIT)
        self.assertIn('macro_ue_traffic', report['missing_checks'])
        self.assertFalse(report['production_authorized'])


if __name__ == '__main__':
    unittest.main()
