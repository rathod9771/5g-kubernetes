"""Release 15/16-oriented *candidate* 5G management KPI admission.

This validates collection evidence; it does not certify 3GPP KPI semantics,
query a cluster, create an alarm, or authorize any production action.
"""
import math
import time

# Specification mappings are at the subject level, not a claim that a vendor's
# counter implements every measurement definition in that specification.
METRICS = {
    'amf_registration_success': {'function': 'AMF', 'subject': 'registration',
                                 'spec': '3GPP TS 28.552 (Release 16)'},
    'smf_pdu_session_setup_success': {'function': 'SMF', 'subject': 'PDU session establishment',
                                      'spec': '3GPP TS 28.552 (Release 16)'},
    'ngran_rrc_setup_success': {'function': 'NG-RAN', 'subject': 'RRC setup',
                                'spec': '3GPP TS 28.552 (Release 16)'},
}
REQUIRED = ('metric_id', 'cluster_uid', 'ns_id', 'nf_uid', 'source_id',
            'window_start', 'window_end', 'observed_at', 'attempts_start',
            'attempts_end', 'success_start', 'success_end',
            'semantic_mapping_verified', 'source_coverage_verified',
            'target_up', 'mapping_review_id')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _identity(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(
        32 <= ord(c) <= 126 for c in value)


def _blocked(reason):
    return {'status': 'UNAVAILABLE', 'reason': reason, 'action': 'observe_notify',
            'production_execution_allowed': False, '3gpp_conformant': False}


def qualify_counter_windows(metric_id, windows, *, now=None, min_attempts=20,
                            failure_threshold=0.05, max_age=120):
    """Check two or more consecutive, scoped, independently sampled counter windows.

    A caller must separately establish vendor-to-3GPP semantic correctness.
    Missing, duplicate, stale, reset, contradictory and unscoped evidence fails
    closed. Threshold is a LOCAL policy value, never a 3GPP-defined BLER/SLA.
    """
    if metric_id not in METRICS:
        return _blocked('unsupported_metric')
    if (type(min_attempts) is not int or not 1 <= min_attempts <= 1000000 or
            not _finite(failure_threshold) or not 0 <= failure_threshold < 1 or
            not _finite(max_age) or not 0 < max_age <= 120):
        return _blocked('invalid_policy')
    now = time.time() if now is None else now
    if not _finite(now) or not isinstance(windows, (list, tuple)) or not 2 <= len(windows) <= 8:
        return _blocked('invalid_collection')
    scopes = set()
    previous_end = None
    previous_counts = None
    attempts_total = 0
    successes_total = 0
    ratios = []
    for sample in windows:
        if not isinstance(sample, dict) or any(key not in sample for key in REQUIRED):
            return _blocked('missing_required_evidence')
        if sample['metric_id'] != metric_id:
            return _blocked('metric_identity_mismatch')
        scope = tuple(sample[key] for key in ('cluster_uid', 'ns_id', 'nf_uid', 'source_id'))
        if not all(_identity(value) for value in scope) or not _identity(sample['mapping_review_id']):
            return _blocked('unknown_or_unsafe_identity')
        scopes.add(scope + (sample['mapping_review_id'],))
        if (sample['semantic_mapping_verified'] is not True or
                sample['source_coverage_verified'] is not True or sample['target_up'] is not True):
            return _blocked('unverified_semantics_or_source')
        keys = ('window_start', 'window_end', 'observed_at', 'attempts_start',
                'attempts_end', 'success_start', 'success_end')
        if not all(_finite(sample[k]) for k in keys):
            return _blocked('invalid_numeric_evidence')
        start, end, observed = (sample[k] for k in ('window_start', 'window_end', 'observed_at'))
        if not (0 < start < end <= observed <= now + 1 and
                30 <= end - start <= 600 and 0 <= now - observed <= max_age):
            return _blocked('stale_future_or_invalid_window')
        if previous_end is not None and (start < previous_end or start - previous_end > 90):
            return _blocked('overlapping_or_discontinuous_windows')
        previous_end = end
        counts = tuple(sample[k] for k in ('attempts_start', 'attempts_end',
                                           'success_start', 'success_end'))
        if any(type(count) is not int or count < 0 or count > 2**63 - 1 for count in counts):
            return _blocked('invalid_counter')
        a0, a1, s0, s1 = counts
        if previous_counts is not None and (a0 < previous_counts[0] or s0 < previous_counts[1]):
            return _blocked('counter_reset_between_windows')
        previous_counts = (a1, s1)
        if a1 < a0 or s1 < s0:
            return _blocked('counter_reset')
        attempts, success = a1 - a0, s1 - s0
        if success > attempts:
            return _blocked('counter_semantics_inconsistent')
        if attempts == 0:
            return _blocked('empty_window')
        attempts_total += attempts
        successes_total += success
        ratios.append((attempts - success) / attempts)
    if len(scopes) != 1:
        return _blocked('scope_or_mapping_changed')
    if any((w['attempts_end'] - w['attempts_start']) < min_attempts for w in windows):
        return {'status': 'INSUFFICIENT', 'reason': 'minimum_attempts_per_window',
                'action': 'observe_notify', 'production_execution_allowed': False,
                '3gpp_conformant': False}
    degraded = all(ratio > failure_threshold for ratio in ratios)
    return {'status': 'DEGRADED_CANDIDATE' if degraded else 'NORMAL_CANDIDATE',
            'reason': 'consecutive_window_policy' if degraded else 'no_consecutive_breach',
            'metric_id': metric_id, 'standard_subject': METRICS[metric_id]['spec'],
            'attempts': attempts_total, 'successes': successes_total,
            'failure_ratio': (attempts_total - successes_total) / attempts_total,
            'windows': len(windows), 'action': 'observe_notify',
            'production_execution_allowed': False, '3gpp_conformant': False}


def production_readiness(assessments):
    """A self-contained fail-closed report, not a live operational attestation."""
    valid = (isinstance(assessments, dict) and set(assessments) == set(METRICS) and
             all(isinstance(value, dict) and value.get('status') in
                 ('NORMAL_CANDIDATE', 'DEGRADED_CANDIDATE') for value in assessments.values()))
    return {'kpi_candidates_qualified': bool(valid),
            'release_15_16_conformance_certified': False,
            'production_autonomous_remediation_ready': False,
            'reasons': ['3GPP MnS/KPI semantic conformance not independently certified',
                        'profile-scoped recovery, OSM lifecycle coordination and rollback not live-tested',
                        'production executor intentionally disabled'],
            'mode': 'OBSERVE_ONLY'}
