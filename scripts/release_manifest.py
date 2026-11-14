"""Release manifest schema, eligibility and consumer-side verification.

A manifest is a claim. Byte digests and structure are checked here offline; the
binding of the manifest to an actual GitHub workflow execution comes only from a
verified GitHub artifact attestation (Sigstore), supplied by an injected verifier.
Without that verification provenance is UNVERIFIED and nothing is eligible.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA = '5g-kubernetes/release-manifest'
SCHEMA_VERSION = 1
MANIFEST_NAME = 'release-manifest.json'
WORKFLOW_PATH = '.github/workflows/release-candidate.yml'
SHA = re.compile(r'[0-9a-f]{40}')
HEX = re.compile(r'[0-9a-f]{64}')
ARTIFACT = re.compile(r'(packages/[a-z][a-z0-9_-]*\.tar\.gz|provenance/[a-z0-9-]+\.provenance\.json)')
CHANNELS = ('release', 'development', 'pull-request', 'manual', 'local')
SENSITIVE_KEY = re.compile(r'(password|passwd|secret|token|apikey|api_key|private_key|privatekey|credential|kubeconfig'
                           r'|client-key-data|client-certificate-data|authorization)', re.I)
SENSITIVE_VALUE = re.compile(r'(-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)|client-key-data:|client-certificate-data:'
                             r'|\bBearer\s+[A-Za-z0-9._-]{8,}|OSM_PASSWORD\s*=)', re.I)


class ManifestError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def channel(event, ref, release_branches):
    if event == 'push' and ref.startswith('refs/heads/'):
        return 'release' if ref[len('refs/heads/'):] in release_branches else 'development'
    if event in ('pull_request', 'pull_request_target'):
        return 'pull-request'
    if event == 'workflow_dispatch':
        return 'manual'
    return 'local'


def eligibility(manifest, images_complete):
    blockers = []
    if manifest['channel'] != 'release':
        blockers.append('channel ' + manifest['channel'] + ' is not an approved release branch push')
    if manifest['ci_validation']['status'] != 'passed':
        blockers.append('CI validation did not pass')
    if not manifest['packages']:
        blockers.append('no packages were generated')
    for key, reason in sorted(manifest['change_impact']['blocked'].items()):
        blockers.append('affected scenario ' + key + ' is blocked: ' + str(reason))
    if not images_complete:
        blockers.append('image identity evidence incomplete (missing/local-only/unverified digests)')
    return {'publication_allowed': not blockers, 'automatic_deployment_allowed': False,
            'automatic_deployment_reason': 'P2 produces candidates only; deployment requires P3+ agent policy',
            'provenance': 'UNVERIFIED',
            'provenance_requirement': 'consumer must verify the GitHub artifact attestation for this manifest '
                                      '(signer workflow, repository, source ref and commit) before any use',
            'blockers': blockers}


def credential_findings(value, where='$'):
    """Credential-like keys or values anywhere in a manifest (paths may mention words like 'token')."""
    findings = []
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)):
                findings.append(where + '.' + str(key))
            findings += credential_findings(item, where + '.' + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings += credential_findings(item, where + '[' + str(index) + ']')
    elif isinstance(value, str) and SENSITIVE_VALUE.search(value):
        findings.append(where)
    return findings


def render(manifest):
    if credential_findings(manifest):
        raise ManifestError('Refusing to render credential-like manifest content')
    return (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()


def _strict_json(data):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise ManifestError('Duplicate manifest key')
        return dict(items)
    def constant(name):
        raise ManifestError('Non-finite number in manifest')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ManifestError('Malformed manifest JSON') from None


def _require(condition, message):
    if not condition:
        raise ManifestError(message)


def verify(manifest_bytes, read_artifact, expected, registry, attestation=None):
    """Consumer verification. Returns a verdict; raises ManifestError on any mismatch.

    expected: repository, branches (approved release branches), commit, workflow_path.
    attestation(manifest_bytes, expected) -> dict proving the workflow binding, or None.
    """
    manifest = _strict_json(manifest_bytes)
    _require(isinstance(manifest, dict) and not credential_findings(manifest), 'Manifest contains credential-like material')
    _require(manifest.get('schema') == SCHEMA and manifest.get('schema_version') == SCHEMA_VERSION,
             'Unsupported manifest schema')
    _require(manifest.get('repository') == expected['repository'], 'Manifest repository is not the approved repository')
    source, workflow = manifest.get('source', {}), manifest.get('workflow', {})
    _require(isinstance(source.get('commit'), str) and SHA.fullmatch(source['commit']), 'Manifest commit is not a full SHA')
    _require(source['commit'] == expected['commit'], 'Manifest source commit mismatch')
    _require(source.get('ref') == 'refs/heads/' + str(source.get('branch')), 'Manifest ref/branch mismatch')
    _require(source.get('branch') in expected['branches'], 'Manifest branch is not an approved release branch')
    _require(workflow.get('path') == expected['workflow_path'] and
             workflow.get('ref') == '{}/{}@{}'.format(expected['repository'], expected['workflow_path'], source['ref']),
             'Manifest workflow identity mismatch')
    _require(type(workflow.get('run_id')) is int and type(workflow.get('run_attempt')) is int and
             workflow.get('runner_environment') == 'github-hosted', 'Manifest workflow execution identity incomplete')
    _require(manifest.get('channel') == 'release', 'Manifest is not a release-channel build')
    entries = {e['key']: e for e in registry['scenarios'] + registry.get('installer_components', [])}
    impact = manifest.get('change_impact', {})
    packages = manifest.get('packages')
    _require(isinstance(packages, list) and packages, 'Manifest has no packages')
    by_key = {p.get('scenario'): p for p in packages}
    _require(len(by_key) == len(packages), 'Duplicate scenario package entry')
    _require(sorted(by_key) == sorted(impact.get('buildable', [])), 'Packages do not cover every affected buildable scenario')
    listed = {}
    for key, package in by_key.items():
        entry = entries.get(key)
        _require(entry is not None and entry['generation_status'] == 'ready', 'Unknown or unready scenario: ' + str(key))
        _require(package.get('kind') == ('core' if key in {e['key'] for e in registry.get('installer_components', [])} else 'ran'),
                 'Wrong package kind: ' + key)
        for role, field in (('knf', 'knf_package'), ('nsd', 'nsd_package')):
            item = package.get(role, {})
            _require(item.get('descriptor_id') == entry[field] and item.get('artifact') == 'packages/' + entry[field] + '.tar.gz',
                     'Package identity mismatch: ' + key + '/' + role)
            listed[item['artifact']] = item.get('sha256')
        listed[package.get('provenance', {}).get('artifact')] = package.get('provenance', {}).get('sha256')
    artifacts = manifest.get('artifacts')
    _require(isinstance(artifacts, dict) and artifacts == listed, 'Artifact index differs from package entries')
    for name, digest in artifacts.items():
        _require(isinstance(name, str) and ARTIFACT.fullmatch(name) and isinstance(digest, str) and HEX.fullmatch(digest),
                 'Invalid artifact entry: ' + str(name))
        data = read_artifact(name)
        _require(isinstance(data, bytes) and sha256(data) == digest, 'Artifact bytes altered or missing: ' + name)
    images = manifest.get('images', {})
    for key in by_key:
        evidence = images.get('scenarios', {}).get(key)
        _require(isinstance(evidence, dict), 'Missing image evidence for ' + key)
        for name in evidence.get('components', []):
            _require(name in images.get('components', {}), 'Incomplete image identity evidence: ' + name)
    _require(manifest.get('ci_validation', {}).get('status') == 'passed', 'CI validation did not pass')
    claimed = manifest.get('eligibility', {})
    _require(claimed.get('automatic_deployment_allowed') is False, 'P2 manifests never allow automatic deployment')
    if claimed.get('publication_allowed') is True:
        # Never trust the claim: recompute image completeness from the evidence.
        records = list(images.get('components', {}).values()) + [
            i for s in images.get('scenarios', {}).values() for i in s.get('chart_images', [])]
        _require(records and all(r.get('status') == 'PINNED_DIGEST' and r.get('registry_verification') == 'VERIFIED'
                                 for r in records), 'Publication claimed without complete verified image identities')
    verdict = {'manifest_sha256': sha256(manifest_bytes), 'commit': source['commit'], 'branch': source['branch'],
               'scenarios': sorted(by_key), 'provenance': 'UNVERIFIED', 'publication_eligible': False,
               'deployment_eligible': False}
    if attestation is None:
        return verdict
    proof = attestation(manifest_bytes, expected)
    _require(isinstance(proof, dict) and proof.get('verified') is True, 'Attestation verification failed')
    _require(proof.get('subject_sha256') == sha256(manifest_bytes) and proof.get('repository') == expected['repository']
             and proof.get('commit') == source['commit'] and proof.get('ref') == source['ref']
             and proof.get('workflow_path') == expected['workflow_path'] and proof.get('runner_environment') == 'github-hosted',
             'Attestation does not bind this manifest to the expected workflow execution')
    verdict['provenance'] = 'VERIFIED'
    verdict['publication_eligible'] = claimed.get('publication_allowed') is True and not claimed.get('blockers')
    return verdict


def lifecycle_release(manifest_bytes, verdict, read_artifact, target):
    """Translate a VERIFIED, publication-eligible manifest into the P1 controller input."""
    if verdict.get('provenance') != 'VERIFIED' or not verdict.get('publication_eligible'):
        raise ManifestError('Only verified, publication-eligible releases may reach the lifecycle controller')
    if verdict.get('manifest_sha256') != sha256(manifest_bytes):
        raise ManifestError('Verdict was issued for a different manifest')
    manifest = _strict_json(manifest_bytes)
    scenarios, blobs = {}, {}
    for package in manifest['packages']:
        artifacts = {}
        for role in ('knf', 'nsd'):
            name = package[role]['artifact'][len('packages/'):]
            blobs[name] = read_artifact(package[role]['artifact'])
            artifacts[name] = package[role]['sha256']
        scenarios[package['scenario']] = {'artifacts': artifacts}
    release = {'schema': 1, 'commit': manifest['source']['commit'], 'branch': manifest['source']['branch'],
               'target': target, 'ci_status': 'success' if manifest['ci_validation']['status'] == 'passed' else 'failure',
               'scenarios': scenarios}
    return release, blobs


def _images_complete(images):
    records = list(images['components'].values()) + [i for s in images['scenarios'].values() for i in s['chart_images']]
    return bool(records) and all(r.get('status') == 'PINNED_DIGEST' and r.get('registry_verification') == 'VERIFIED'
                                 for r in records)


def publication_decision(manifest_bytes, read_artifact, expected, registry):
    """CI gate before attestation: may these exact bytes be attested as a publication candidate?

    Fails closed. The manifest's own eligibility claim is necessary but never sufficient:
    the candidate is verified as a consumer would and eligibility is recomputed from evidence.
    """
    try:
        verify(manifest_bytes, read_artifact, expected, registry)
        manifest = _strict_json(manifest_bytes)
        claimed = manifest['eligibility']
        blockers = list(eligibility(manifest, _images_complete(manifest['images']))['blockers'])
    except (ManifestError, KeyError, TypeError, AttributeError) as error:
        return {'publication_allowed': False, 'blockers': ['candidate verification failed: ' + str(error)]}
    if not isinstance(claimed, dict) or claimed.get('publication_allowed') is not True or claimed.get('blockers') != []:
        blockers.append('manifest does not record an unblocked publication decision')
    if not isinstance(claimed, dict) or claimed.get('automatic_deployment_allowed') is not False:
        blockers.append('manifest does not forbid automatic deployment')
    return {'publication_allowed': not blockers, 'blockers': blockers}


def _read_release_file(root):
    def read(name):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ManifestError('Release file missing or not a regular file: ' + name)
        return path.read_bytes()
    return read


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pre-attestation publication decision for a built candidate.')
    parser.add_argument('command', choices=['publication-decision'])
    parser.add_argument('--release', type=Path, required=True, help='candidate directory written by build_release.py')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--branch', required=True)
    args = parser.parse_args(argv)
    try:
        from scenario_registry import load_registry
        manifest_bytes = _read_release_file(args.release)(MANIFEST_NAME)
        decision = publication_decision(manifest_bytes, _read_release_file(args.release),
                                        {'repository': args.repository, 'branches': (args.branch,),
                                         'commit': args.commit, 'workflow_path': WORKFLOW_PATH}, load_registry())
    except Exception as error:      # any failure is a refusal, never an approval
        decision = {'publication_allowed': False, 'blockers': ['decision failed: ' + type(error).__name__]}
    print(json.dumps(decision, indent=2, sort_keys=True))
    # Exit 0 only for an approved candidate; the workflow treats anything else as "do not attest".
    return 0 if decision['publication_allowed'] is True else 3


if __name__ == '__main__':
    sys.exit(main())
