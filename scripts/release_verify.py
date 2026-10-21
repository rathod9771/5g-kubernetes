#!/usr/bin/env python3
"""P3: target-side, read-only verification of a GitHub release candidate.

Establishes independently whether a release candidate built by
.github/workflows/release-candidate.yml is trustworthy: the GitHub run and attempt,
the exact artifact bytes, a Sigstore-verified GitHub artifact attestation bound to
those bytes, and a release policy recomputed from evidence rather than taken from
the manifest's own eligibility claims.

Verification is not authorization. A VERIFIED result is a publication candidate
whose identity is recorded for later lifecycle processing; it never deploys, never
touches OSM, Kubernetes, the dashboard or the active RAN record, and always reports
deployment_authorized=false.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time

import lifecycle
from local_safety import LockTimeout, atomic_write, exclusive_lock
from release_attestation import AttestationError, GhVerifier, binding
from release_fetch import FetchError, Fetcher, read_archive, read_directory, read_token
from release_images import verify_registry
from release_manifest import MANIFEST_NAME, ManifestError, lifecycle_release, verify as verify_manifest
from scenario_registry import RegistryError, load_registry

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = 'config/release-verification.json'
POLICY_SCHEMA = '5g-kubernetes/release-verification-policy'
RECORD_SCHEMA = '5g-kubernetes/verified-releases'
RECORDS_NAME = 'verified-releases.json'
STORE_NAME = 'verified-releases'
LOCK_NAME = '.release-verify.lock'
SUMS = 'SHA256SUMS'
SHA = re.compile(r'[0-9a-f]{40}')
HEX = re.compile(r'[0-9a-f]{64}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
LAYOUT = re.compile(r'(release-manifest\.json|SHA256SUMS|packages/[a-z][a-z0-9_-]*\.tar\.gz|'
                    r'provenance/[a-z0-9-]+\.provenance\.json)')
LOCAL_REFERENCE = re.compile(r'(localhost|127\.\d+\.\d+\.\d+|\[::1\])([:/]|$)')


class VerificationRejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise VerificationRejected(code, message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _strict(data, what):
    def pairs(items):
        keys = [k for k, _ in items]
        _require(len(keys) == len(set(keys)), 'MALFORMED', 'Duplicate key in ' + what)
        return dict(items)

    def constant(name):
        raise VerificationRejected('MALFORMED', 'Non-finite number in ' + what)
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise VerificationRejected('MALFORMED', 'Malformed JSON in ' + what) from None


def load_policy(path=None):
    policy = _strict(Path(path or ROOT / POLICY_PATH).read_bytes(), 'verification policy')
    _require(isinstance(policy, dict) and policy.get('schema') == POLICY_SCHEMA and policy.get('schema_version') == 1,
             'POLICY', 'Unsupported verification policy')
    text = ('repository', 'branch', 'workflow_path', 'event', 'oidc_issuer', 'predicate_type')
    _require(all(isinstance(policy.get(k), str) and policy[k] for k in text), 'POLICY', 'Incomplete verification policy')
    _require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', policy['repository']) is not None, 'POLICY', 'Bad repository')
    _require(all(type(policy.get(k)) is int and policy[k] > 0 for k in ('repository_id', 'owner_id')), 'POLICY',
             'Repository and owner ids must be pinned')
    _require(policy['event'] == 'push', 'POLICY', 'Only push-built releases can be publication candidates')
    target, verifier, limits = policy.get('target'), policy.get('verifier'), policy.get('limits')
    _require(isinstance(target, dict) and set(target) == {'environment', 'architecture'}, 'POLICY', 'Bad target')
    _require(isinstance(verifier, dict) and verifier.get('tool') == 'gh' and isinstance(verifier.get('version'), str)
             and isinstance(verifier.get('sha256'), str) and HEX.fullmatch(verifier['sha256']), 'POLICY',
             'Attestation verifier must be pinned by version and SHA-256')
    _require(isinstance(limits, dict) and all(type(v) is int and v > 0 for v in limits.values()), 'POLICY', 'Bad limits')
    _require(isinstance(policy.get('required_jobs'), list) and policy['required_jobs'], 'POLICY', 'Bad required jobs')
    _require(type(policy.get('recheck_registry')) is bool, 'POLICY', 'Bad registry recheck flag')
    return policy


def check_run(fetcher, policy, run_id, attempt):
    """GitHub's record of the run attempt, its jobs and its single release artifact."""
    repository = policy['repository']
    base = '/repos/{}/actions/runs/{}'.format(repository, run_id)
    run = fetcher.api_json(base + '/attempts/' + str(attempt))
    _require(isinstance(run, dict) and run.get('id') == run_id and run.get('run_attempt') == attempt, 'WRONG_ATTEMPT',
             'GitHub returned a different run or attempt')
    for key in ('repository', 'head_repository'):
        record = run.get(key) if isinstance(run.get(key), dict) else {}
        _require(record.get('full_name') == repository and record.get('id') == policy['repository_id'] and
                 (record.get('owner') or {}).get('id') == policy['owner_id'], 'WRONG_REPOSITORY',
                 'Run does not belong to the approved repository (' + key + ')')
    _require(run.get('path') == policy['workflow_path'], 'WRONG_WORKFLOW', 'Run is not the release-candidate workflow')
    _require(run.get('event') == policy['event'], 'NOT_RELEASE_CHANNEL',
             'Run event ' + str(run.get('event')) + ' is not an approved release trigger')
    _require(run.get('head_branch') == policy['branch'], 'WRONG_BRANCH',
             'Run branch ' + str(run.get('head_branch')) + ' is not the approved release branch')
    commit = run.get('head_sha')
    _require(isinstance(commit, str) and SHA.fullmatch(commit) is not None, 'WRONG_COMMIT', 'Run has no full commit SHA')
    _require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'CI_FAILED',
             'Run attempt did not complete successfully')
    jobs = fetcher.api_json(base + '/attempts/' + str(attempt) + '/jobs?per_page=100')
    jobs = jobs.get('jobs') if isinstance(jobs, dict) else None
    _require(isinstance(jobs, list) and 0 < len(jobs) < 100, 'CI_FAILED', 'Run job list is unavailable')
    seen = {}
    for job in jobs:
        _require(isinstance(job, dict) and job.get('name') not in seen, 'CI_FAILED', 'Ambiguous run jobs')
        _require(job.get('run_id') == run_id and job.get('run_attempt') == attempt, 'WRONG_ATTEMPT',
                 'Job belongs to another run attempt')
        labels = job.get('labels') if isinstance(job.get('labels'), list) else []
        _require('self-hosted' not in labels and job.get('runner_group_name') in (None, 'GitHub Actions'),
                 'SELF_HOSTED', 'Release jobs must run on GitHub-hosted runners')
        _require(job.get('status') == 'completed' and job.get('conclusion') == 'success', 'CI_FAILED',
                 'Job ' + str(job.get('name')) + ' did not succeed')
        seen[job.get('name')] = job
    _require(all(name in seen for name in policy['required_jobs']), 'CI_FAILED', 'Required build/attest jobs are missing')
    name = 'release-candidate-{}-{}'.format(commit, attempt)
    listing = fetcher.api_json(base + '/artifacts?per_page=100&name=' + name)
    found = [a for a in (listing.get('artifacts') or []) if isinstance(a, dict) and a.get('name') == name] \
        if isinstance(listing, dict) else []
    _require(len(found) == 1, 'ARTIFACT_MISSING', 'Expected exactly one release artifact named ' + name)
    artifact = found[0]
    origin = artifact.get('workflow_run') if isinstance(artifact.get('workflow_run'), dict) else {}
    _require(origin.get('id') == run_id and origin.get('head_sha') == commit and
             origin.get('repository_id') == policy['repository_id'], 'ARTIFACT_MISSING',
             'Release artifact was not produced by this run')
    _require(artifact.get('expired') is False and type(artifact.get('id')) is int, 'ARTIFACT_MISSING',
             'Release artifact expired or has no id')
    _require(isinstance(artifact.get('digest'), str) and DIGEST.fullmatch(artifact['digest']) is not None,
             'ARTIFACT_MISSING', 'Release artifact has no recorded digest')
    _require(type(artifact.get('size_in_bytes')) is int and artifact['size_in_bytes'] <= policy['limits']['archive_bytes'],
             'ARTIFACT_LIMIT', 'Release artifact exceeds the size limit')
    return {'repository': repository, 'repository_id': policy['repository_id'], 'branch': policy['branch'],
            'ref': 'refs/heads/' + policy['branch'], 'commit': commit, 'workflow_path': policy['workflow_path'],
            'event': run['event'], 'run_id': run_id, 'run_attempt': attempt,
            'artifact': {'name': name, 'id': artifact['id'], 'digest': artifact['digest'][len('sha256:'):]}}


def acquire(source, identity, fetcher, limits):
    kind, value = source
    bounds = {'max_files': limits['files'], 'max_bytes': limits['extracted_bytes']}
    if kind == 'directory':
        return read_directory(value, **bounds), False
    if kind == 'zip':
        path = Path(value)
        _require(path.is_file() and not path.is_symlink(), 'ARTIFACT_MISSING', 'Release archive is missing')
        _require(path.stat().st_size <= limits['archive_bytes'], 'ARTIFACT_LIMIT', 'Release archive exceeds the size limit')
        data = path.read_bytes()
    elif kind == 'download':
        data = fetcher.artifact_archive(identity['artifact']['id'])
    else:
        raise VerificationRejected('USAGE', 'Unknown artifact source')
    _require(sha256(data) == identity['artifact']['digest'], 'ARTIFACT_ALTERED',
             'Release archive differs from the digest GitHub recorded for this run')
    return read_archive(data, **bounds), True


def check_layout(files):
    _require(files and all(LAYOUT.fullmatch(name) for name in files), 'ARTIFACT_LAYOUT',
             'Release artifact contains unexpected files')
    _require(MANIFEST_NAME in files and SUMS in files, 'ARTIFACT_LAYOUT', 'Release manifest or checksums missing')
    listed = {}
    for line in files[SUMS].decode('ascii', 'replace').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (\S+)', line)
        _require(match is not None and match.group(2) not in listed, 'ARTIFACT_ALTERED', 'Malformed SHA256SUMS')
        listed[match.group(2)] = match.group(1)
    _require(set(listed) == set(files) - {SUMS}, 'ARTIFACT_ALTERED', 'SHA256SUMS does not cover exactly the artifact files')
    for name, digest in listed.items():
        _require(sha256(files[name]) == digest, 'ARTIFACT_ALTERED', 'Artifact bytes altered: ' + name)


def expected_subjects(files):
    """Subjects the P2 attest job signs: every artifact file except SHA256SUMS, named by basename
    (actions/attest names path subjects by basename and creates one statement for all of them)."""
    return {PurePosixPath(name).name: sha256(content) for name, content in files.items() if name != SUMS}


def check_identity(manifest, identity, policy):
    """The manifest must describe exactly the run GitHub reported."""
    source = manifest.get('source') if isinstance(manifest.get('source'), dict) else {}
    workflow = manifest.get('workflow') if isinstance(manifest.get('workflow'), dict) else {}
    _require(manifest.get('repository') == identity['repository'], 'WRONG_REPOSITORY', 'Manifest names another repository')
    _require(source.get('commit') == identity['commit'], 'WRONG_COMMIT', 'Manifest commit differs from the run commit')
    _require(source.get('branch') == identity['branch'] and source.get('ref') == identity['ref'], 'WRONG_BRANCH',
             'Manifest branch differs from the approved release branch')
    _require(workflow.get('path') == identity['workflow_path'], 'WRONG_WORKFLOW', 'Manifest names another workflow')
    _require(workflow.get('run_id') == identity['run_id'] and workflow.get('run_attempt') == identity['run_attempt'],
             'WRONG_ATTEMPT', 'Manifest belongs to another run or attempt')
    _require(workflow.get('runner_environment') == 'github-hosted', 'SELF_HOSTED', 'Manifest was not built on GitHub')
    _require(source.get('event') == policy['event'] and manifest.get('channel') == 'release', 'NOT_RELEASE_CHANNEL',
             'Only push builds of the release branch are publication candidates')


def _image(record, where, recheck, opener):
    _require(isinstance(record, dict), 'IMAGE_UNVERIFIED', 'Missing image evidence: ' + where)
    reference = str(record.get('repository') or '')
    _require(record.get('status') != 'LOCAL_ONLY' and record.get('acquisition') != 'local' and
             not LOCAL_REFERENCE.match(reference) and record.get('portable') is not False, 'LOCAL_IMAGE',
             'Local-only image reference: ' + where)
    _require(record.get('status') == 'PINNED_DIGEST' and isinstance(record.get('digest'), str) and
             DIGEST.fullmatch(record['digest']) is not None, 'IMAGE_DIGEST_MISSING', 'Image is not pinned by digest: ' + where)
    _require(record.get('registry_verification') == 'VERIFIED', 'IMAGE_UNVERIFIED', 'Image digest was not verified: ' + where)
    if recheck:
        _require(verify_registry(record, opener=opener) == 'VERIFIED', 'IMAGE_UNVERIFIED',
                 'Target-side registry check failed: ' + where)


def assess(manifest, registry, policy, opener):
    """Publication policy recomputed from evidence; eligibility fields in the manifest are never trusted."""
    packages = manifest.get('packages')
    _require(isinstance(packages, list) and packages, 'EMPTY_RELEASE', 'Release contains no packages')
    impact = manifest.get('change_impact') if isinstance(manifest.get('change_impact'), dict) else {}
    _require(not impact.get('blocked'), 'BLOCKED_SCENARIO', 'Release includes blocked scenarios: ' +
             ', '.join(sorted(impact.get('blocked') or {})))
    entries = {e['key']: e for e in registry['scenarios'] + registry.get('installer_components', [])}
    for package in packages:
        key = package.get('scenario') if isinstance(package, dict) else None
        _require(key in entries and entries[key]['generation_status'] == 'ready', 'BLOCKED_SCENARIO',
                 'Package from an unready or unknown scenario: ' + str(key))
    _require(manifest.get('target') == policy['target'], 'WRONG_TARGET', 'Release targets an unapproved environment')
    images = manifest.get('images') if isinstance(manifest.get('images'), dict) else {}
    components, scenarios = images.get('components') or {}, images.get('scenarios') or {}
    for package in packages:
        evidence = scenarios.get(package['scenario'])
        _require(isinstance(evidence, dict), 'IMAGE_UNVERIFIED', 'No image evidence for ' + package['scenario'])
        records = [(components.get(n), n) for n in evidence.get('components') or []] + \
                  [(r, package['scenario'] + ' chart image') for r in evidence.get('chart_images') or []]
        _require(records, 'IMAGE_UNVERIFIED', 'No image evidence for ' + package['scenario'])
        for record, where in records:
            _image(record, where, policy['recheck_registry'], opener)
    claimed = manifest.get('eligibility') if isinstance(manifest.get('eligibility'), dict) else {}
    _require(claimed.get('automatic_deployment_allowed') is False, 'MANIFEST_CLAIM',
             'Manifest claims automatic deployment; P2/P3 never allow it')
    _require(claimed.get('publication_allowed') is True and not claimed.get('blockers'), 'MANIFEST_DISAGREES',
             'Builder recorded blockers for this release')


def _store(state, manifest_sha, files, evidence):
    """Content-addressed, private copy of exactly the verified bytes."""
    store = state / STORE_NAME
    store.mkdir(mode=0o700, exist_ok=True)
    target = store / manifest_sha
    staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=store))
    try:
        for name, content in list(files.items()) + list(evidence.items()):
            path = staging / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o400)
        if target.exists():          # unrecorded leftover of an interrupted run; the record is authoritative
            shutil.rmtree(target)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _records(state):
    path = state / RECORDS_NAME
    try:
        data = _strict(path.read_bytes(), RECORDS_NAME)
    except FileNotFoundError:
        return {'schema': RECORD_SCHEMA, 'schema_version': 1, 'releases': []}
    _require(isinstance(data, dict) and data.get('schema') == RECORD_SCHEMA and isinstance(data.get('releases'), list),
             'STATE', 'Malformed verified-release records; reconcile explicitly')
    return data


def record_release(state_dir, record, files, evidence, limit, lock_timeout=60):
    """Duplicate/replay checks and recording happen under one lock; verification work does not."""
    state = Path(state_dir)
    with exclusive_lock(state / LOCK_NAME, timeout=lock_timeout):
        data = _records(state)
        releases = data['releases']
        _require(not any(r.get('manifest_sha256') == record['manifest_sha256'] for r in releases), 'DUPLICATE_RELEASE',
                 'This release was already verified and recorded')
        key = (record['run_id'], record['run_attempt'])
        _require(not any((r.get('run_id'), r.get('run_attempt')) == key for r in releases), 'REPLAYED_RELEASE',
                 'Another manifest was already recorded for this run attempt')
        latest = max(((r['run_id'], r['run_attempt']) for r in releases), default=None)
        _require(latest is None or key > latest, 'REPLAYED_RELEASE',
                 'Release is older than the latest verified release; replays are not recorded')
        location = _store(state, record['manifest_sha256'], files, evidence)
        releases.append(dict(record, store=str(location.relative_to(state))))
        data['releases'] = releases[-limit:]
        atomic_write(state / RECORDS_NAME, json.dumps(data, indent=2, sort_keys=True).encode())
    return location


def verify_candidate(policy, run_id, attempt, source, state_dir, fetcher, verifier, root=ROOT, opener=None,
                     trusted_root=None, record=True, clock=time.time):
    identity = {'run_id': run_id, 'run_attempt': attempt}
    workdir = None
    try:
        _require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, 'USAGE', 'Bad run identity')
        state = Path(state_dir)
        _require(state.is_dir() and not state.is_symlink(), 'STATE', 'State directory is missing or a symlink')
        workdir = Path(tempfile.mkdtemp(prefix='.release-verify-', dir=state))
        verifier_identity = verifier.check_tool()
        registry = load_registry(root)
        identity = check_run(fetcher, policy, run_id, attempt)
        files, archive_checked = acquire(source, identity, fetcher, policy['limits'])
        check_layout(files)
        manifest_bytes = files[MANIFEST_NAME]
        manifest = _strict(manifest_bytes, MANIFEST_NAME)
        _require(isinstance(manifest, dict), 'MALFORMED', 'Manifest is not an object')
        check_identity(manifest, identity, policy)
        assess(manifest, registry, policy, opener)
        evidence = {}

        def attestation(data, expected):
            subject = workdir / 'subject' / MANIFEST_NAME
            subject.parent.mkdir(mode=0o700, exist_ok=True)
            subject.write_bytes(data)
            bundles = fetcher.attestation_bundles(sha256(data), policy['repository_id'],
                                                  policy['limits']['attestations_per_artifact'])
            results = verifier.verify(subject, bundles, policy, identity, trusted_root)
            proof = binding(results, sha256(data), policy, identity)
            evidence['attestation/bundles.jsonl'] = ''.join(json.dumps(b, sort_keys=True) + '\n' for b in bundles).encode()
            evidence['attestation/verification.json'] = json.dumps(results, sort_keys=True).encode()
            evidence['proof'] = proof
            return proof

        expected = {'repository': policy['repository'], 'branches': (policy['branch'],), 'commit': identity['commit'],
                    'workflow_path': policy['workflow_path']}
        read = files.__getitem__
        verdict = verify_manifest(manifest_bytes, read, expected, registry, attestation)
        proof = evidence.pop('proof')
        _require(proof['subjects'] == expected_subjects(files), 'ATTESTATION_SUBJECT',
                 'Attested subjects differ from the downloaded manifest and package bytes')
        _require(verdict['provenance'] == 'VERIFIED' and verdict['publication_eligible'], 'MANIFEST_DISAGREES',
                 'Release is not publication-eligible')
        target = policy['target']['environment']
        release, blobs = lifecycle_release(manifest_bytes, verdict, read, target)
        lifecycle.verify_release(release, blobs, registry, {'branch': policy['branch'], 'target': target})
        result = {
            'status': 'VERIFIED', 'provenance': 'VERIFIED', 'publication_candidate': True,
            'deployment_authorized': False,
            'authorization': 'verification only; deployment needs a separate, explicit lifecycle decision',
            'manifest_sha256': sha256(manifest_bytes), 'repository': identity['repository'],
            'repository_id': identity['repository_id'], 'branch': identity['branch'], 'ref': identity['ref'],
            'commit': identity['commit'], 'workflow_path': identity['workflow_path'], 'event': identity['event'],
            'run_id': run_id, 'run_attempt': attempt,
            'artifact': dict(identity['artifact'], archive_digest_checked=archive_checked),
            'target': policy['target'], 'scenarios': sorted(release['scenarios']),
            'packages': [{'scenario': p['scenario'], 'kind': p['kind'],
                          'knf': {k: p['knf'][k] for k in ('descriptor_id', 'sha256')},
                          'nsd': {k: p['nsd'][k] for k in ('descriptor_id', 'sha256')}} for p in manifest['packages']],
            'verifier': verifier_identity,
            'attestation': {'signer': proof['signer'], 'timestamps': proof['timestamps'],
                            'subjects': len(proof['subjects'])},
            'verified_at': datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        if record:
            result['store'] = str(record_release(state, result, files, evidence, policy['limits']['records']))
        return result
    except VerificationRejected as error:
        return _rejected(error.code, str(error), identity)
    except (AttestationError, FetchError) as error:
        return _rejected(error.code, str(error), identity)
    except ManifestError as error:
        return _rejected('MANIFEST_REJECTED', str(error), identity)
    except lifecycle.ReleaseRejected as error:
        return _rejected('LIFECYCLE_CONTRACT', str(error), identity)
    except LockTimeout:
        return _rejected('CONCURRENT_VERIFICATION', 'Another verification holds the record lock', identity)
    except (RegistryError, OSError) as error:
        return _rejected('VERIFICATION_ERROR', type(error).__name__ + ' during verification', identity)
    except Exception as error:   # fail closed on anything unexpected
        return _rejected('VERIFICATION_ERROR', 'Unexpected ' + type(error).__name__, identity)
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def _rejected(code, reason, identity):
    keep = ('repository', 'branch', 'commit', 'workflow_path', 'event', 'run_id', 'run_attempt')
    return {'status': 'REJECTED', 'code': code, 'reason': reason[:300], 'provenance': 'UNVERIFIED',
            'publication_candidate': False, 'deployment_authorized': False,
            'identity': {k: identity[k] for k in keep if k in identity}}


def load_verified(state_dir, manifest_sha256, registry, policy):
    """A recorded release, re-hashed from the private store, as P1 lifecycle input. Never deploys."""
    state = Path(state_dir)
    _require(isinstance(manifest_sha256, str) and HEX.fullmatch(manifest_sha256) is not None, 'USAGE', 'Bad manifest digest')
    matches = [r for r in _records(state)['releases'] if r.get('manifest_sha256') == manifest_sha256]
    _require(len(matches) == 1, 'NOT_VERIFIED', 'Release has not been verified on this host')
    record = matches[0]
    store = state / STORE_NAME / manifest_sha256
    _require(not store.is_symlink() and store.is_dir(), 'STATE', 'Verified release store is missing')
    files = {p.relative_to(store).as_posix(): p.read_bytes() for p in store.rglob('*')
             if p.is_file() and not p.is_symlink() and not p.relative_to(store).as_posix().startswith('attestation/')}
    check_layout(files)
    _require(sha256(files[MANIFEST_NAME]) == manifest_sha256, 'ARTIFACT_ALTERED', 'Stored manifest was altered')
    verdict = {'manifest_sha256': manifest_sha256, 'provenance': 'VERIFIED', 'publication_eligible': True}
    target = policy['target']['environment']
    release, blobs = lifecycle_release(files[MANIFEST_NAME], verdict, files.__getitem__, target)
    lifecycle.verify_release(release, blobs, registry, {'branch': policy['branch'], 'target': target})
    return record, release, blobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--artifact-zip', type=Path, help='Archive downloaded from the run (digest-checked)')
    source.add_argument('--artifact-dir', type=Path, help='Directory from `gh run download` (attestation-checked)')
    source.add_argument('--download', action='store_true', help='Download the archive with --github-token-file')
    parser.add_argument('--github-token-file', type=Path, help='Private file with a read-only token (download only)')
    parser.add_argument('--state-dir', type=Path, required=True, help='Private directory for verified-release records')
    parser.add_argument('--gh', type=Path, required=True, help='Pinned gh binary; never looked up on PATH')
    parser.add_argument('--policy', type=Path, default=ROOT / POLICY_PATH)
    parser.add_argument('--trusted-root', type=Path, help='Optional Sigstore trusted_root.jsonl for offline use')
    parser.add_argument('--no-record', action='store_true', help='Verify without recording the release')
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    if args.download and not args.github_token_file:
        parser.error('--download requires --github-token-file')
    token = read_token(args.github_token_file) if args.download else None
    limits = {k: policy['limits'][k] for k in ('api_bytes', 'bundle_bytes', 'bundle_decoded_bytes', 'archive_bytes')}
    fetcher = Fetcher(policy['repository'], timeout=policy['limits']['http_timeout_seconds'], token=token, limits=limits)
    chosen = ('zip', args.artifact_zip) if args.artifact_zip else ('directory', args.artifact_dir) \
        if args.artifact_dir else ('download', None)
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gh-', dir=args.state_dir) as workdir:
        verifier = GhVerifier(args.gh, policy['verifier']['version'], policy['verifier']['sha256'], workdir,
                              timeout=policy['limits']['verifier_timeout_seconds'])
        result = verify_candidate(policy, args.run_id, args.attempt, chosen, args.state_dir, fetcher, verifier,
                                  trusted_root=args.trusted_root, record=not args.no_record)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'VERIFIED' else 2


if __name__ == '__main__':
    sys.exit(main())
