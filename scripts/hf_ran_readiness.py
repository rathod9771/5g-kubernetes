#!/usr/bin/env python3
"""Read-only H-CRAN/F-RAN evidence inventory gate (NOT production approval).

Verifies evidence-file integrity and check coverage, not claims inside logs,
3GPP conformance or permission to deploy.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

SCENARIOS = ('hcran-oai', 'hcran-srsran', 'fran-oai', 'fran-srsran')
COMMON = (
    'ngap_amf', 'f1_control', 'f1_user_plane', 'ue_registration',
    'pdu_session', 'user_plane_traffic', 'resource_capacity',
    'multi_node_transport', 'monitoring_alerts', 'security_review',
    'image_provenance', 'osm_lifecycle_rollback', 'controlled_fault_recovery',
    'soak_and_load',
)
HCRAN = (
    'macro_f1_identity', 'small_f1_identity', 'simultaneous_du_service',
    'macro_ue_traffic', 'small_ue_traffic', 'inter_cell_mobility',
    'interference_coordination_scope',
)
FRAN = (
    'fog_processing', 'fog_edge_placement', 'fog_path_latency',
    'fog_workload_isolation', 'local_breakout_scope',
)
MAX_EVIDENCE_SIZE = 8 * 1024 * 1024
SHA256 = re.compile(r'^[a-f0-9]{64}$')
GIT_SHA = re.compile(r'^[a-f0-9]{40}$')


def required_checks(scenario):
    if scenario not in SCENARIOS:
        raise ValueError('Unknown scenario')
    return COMMON + (HCRAN if scenario.startswith('hcran-') else FRAN)


def _artifact_valid(root, artifact):
    """Return a constant error code; never print private evidence contents."""
    if not isinstance(artifact, dict):
        return 'BAD_ARTIFACT_RECORD'
    raw = artifact.get('path')
    digest = artifact.get('sha256')
    if not isinstance(raw, str) or not raw or '\\' in raw or ':' in raw:
        return 'INVALID_RELATIVE_PATH'
    path = PurePosixPath(raw)
    if path.is_absolute() or any(p in ('..', '.') for p in raw.split('/')):
        return 'INVALID_RELATIVE_PATH'
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        return 'INVALID_SHA256'
    try:
        root = root.resolve(strict=True)
        target = (root / path).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            return 'OUTSIDE_EVIDENCE_ROOT'
        size = target.stat().st_size
        if not 0 < size <= MAX_EVIDENCE_SIZE:
            return 'EMPTY_OR_OVERSIZE'
        hasher = hashlib.sha256()
        with target.open('rb') as handle:
            while chunk := handle.read(65536):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            return 'HASH_MISMATCH'
    except OSError:
        return 'EVIDENCE_UNREADABLE'
    return None


def assess(manifest, evidence_root, scenario, expected_commit):
    """Return a non-authorizing evidence inventory result."""
    required = required_checks(scenario)
    errors = []
    if not isinstance(manifest, dict):
        manifest = {}
        errors.append('MALFORMED_MANIFEST')
    if manifest.get('schema') != '5g-kubernetes/hf-ran-evidence-v1':
        errors.append('SCHEMA_MISMATCH')
    if manifest.get('scenario') != scenario:
        errors.append('SCENARIO_MISMATCH')
    declared_commit = manifest.get('source_commit')
    if not (isinstance(declared_commit, str) and GIT_SHA.fullmatch(declared_commit)
            and isinstance(expected_commit, str) and GIT_SHA.fullmatch(expected_commit)
            and declared_commit == expected_commit):
        errors.append('SOURCE_COMMIT_UNVERIFIED')
    artifacts = manifest.get('artifacts')
    checks = manifest.get('checks')
    if not isinstance(artifacts, dict):
        artifacts = {}
        errors.append('ARTIFACTS_NOT_OBJECT')
    if not isinstance(checks, dict):
        checks = {}
        errors.append('CHECKS_NOT_OBJECT')
    if len(artifacts) > 200 or len(checks) > 100:
        errors.append('INVENTORY_TOO_LARGE')
        artifacts = dict(list(artifacts.items())[:200])
        checks = dict(list(checks.items())[:100])
    integrity = {}
    for key, value in artifacts.items():
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', key):
            errors.append('BAD_ARTIFACT_ID')
            continue
        integrity[key] = _artifact_valid(Path(evidence_root), value)
    covered = []
    missing = []
    for check in required:
        refs = checks.get(check)
        if not isinstance(refs, list) or not refs or len(refs) > 20 or not all(
                isinstance(ref, str) for ref in refs):
            missing.append(check)
            continue
        if all(ref in integrity and integrity[ref] is None for ref in refs):
            covered.append(check)
        else:
            missing.append(check)
    invalid = {k: code for k, code in integrity.items() if code is not None}
    status = ('EVIDENCE_INVENTORY_COMPLETE' if not errors and not missing
              and not invalid else 'BLOCKED')
    return {
        'schema': '5g-kubernetes/hf-ran-readiness-result-v1',
        'scenario': scenario,
        'status': status,
        'checks_inventoried': len(covered),
        'checks_required': len(required),
        'missing_checks': missing,
        'invalid_artifacts': invalid,
        'blockers': sorted(set(errors)),
        'manual_technical_review_required': True,
        '3gpp_conformance_verified': False,
        'production_authorized': False,
        'cluster_mutations_performed': False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario', choices=SCENARIOS, required=True)
    p.add_argument('--list-checks', action='store_true',
                   help='Print required evidence categories; do not read any files')
    p.add_argument('--manifest', type=Path)
    p.add_argument('--evidence-root', type=Path)
    p.add_argument('--expected-commit', help='Exact independently verified Git SHA')
    a = p.parse_args(argv)
    if a.list_checks:
        print(json.dumps({'scenario': a.scenario, 'required_checks': required_checks(a.scenario)}, indent=2))
        return 0
    if not a.manifest or not a.evidence_root or not a.expected_commit:
        p.error('--manifest, --evidence-root and --expected-commit are required for assessment')
    try:
        with a.manifest.open('r', encoding='utf-8') as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        manifest = None
    result = assess(manifest, a.evidence_root, a.scenario, a.expected_commit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'EVIDENCE_INVENTORY_COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
