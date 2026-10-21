"""P3 trusted release verification, offline.

Real P2 builds (throwaway repositories) supply the release bytes. GitHub's API and
blob storage are an in-memory fake, and Sigstore is modelled by a fake `gh`
executable that checks a fixture signature and enforces the same identity flags as
`gh attestation verify`; a deliberately lenient variant proves the target-side
re-checks do not depend on the verifier's flag handling.
"""
import base64
import copy
import email.message
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).resolve().parent)]

import build_release  # noqa: E402
import lifecycle  # noqa: E402
import release_fetch  # noqa: E402
import release_attestation  # noqa: E402
import release_images  # noqa: E402
import release_verify  # noqa: E402
from release_attestation import AttestationError, GhVerifier  # noqa: E402
from test_release_pipeline import BRANCH, REGISTRY, REPOSITORY, WORKFLOW, FixtureRepo, ci_env, commit, git, \
    registry_edit  # noqa: E402

POLICY = release_verify.load_policy()
REPO_ID, OWNER_ID = POLICY['repository_id'], POLICY['owner_id']
KEY = b'fixture-sigstore-trust-root'
BLOB = 'https://tmaproduction.blob.core.windows.net/attestations/'
VERSION = POLICY['verifier']['version']

FAKE_GH = r'''
import hashlib, hmac, json, os, sys
MODE = %(mode)r
KEY = %(key)r
if any(k in os.environ for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN')):
    sys.exit(97)
args = sys.argv[1:]
if args == ['--version']:
    print('gh version %(version)s (2026-09-15)')
    sys.exit(0)
assert args[:2] == ['attestation', 'verify'], args
subject, options, i = args[2], {}, 3
while i < len(args):
    if args[i] == '--deny-self-hosted-runners':
        options[args[i]] = True; i += 1
    else:
        options[args[i]] = args[i + 1]; i += 2
digest = hashlib.sha256(open(subject, 'rb').read()).hexdigest()
results = []
for line in open(options['--bundle']):
    bundle = json.loads(line)
    body = bundle.get('fixture') or {}
    canonical = json.dumps(body, sort_keys=True).encode()
    if not hmac.compare_digest(bundle.get('signature', ''), hmac.new(KEY, canonical, 'sha256').hexdigest()):
        continue
    if body.get('expired'):
        continue
    cert, statement = body['certificate'], body['statement']
    if digest not in [s['digest']['sha256'] for s in statement['subject']]:
        continue
    if MODE != 'lenient':
        checks = [cert['sourceRepositoryURI'] == 'https://github.com/' + options['--repo'],
                  cert['buildSignerURI'].startswith('https://github.com/' + options['--signer-workflow'] + '@'),
                  cert['sourceRepositoryRef'] == options['--source-ref'],
                  cert['sourceRepositoryDigest'] == options['--source-digest'],
                  cert['buildSignerDigest'] == options['--signer-digest'],
                  cert['issuer'] == options['--cert-oidc-issuer'],
                  statement['predicateType'] == options['--predicate-type'],
                  cert['runnerEnvironment'] == 'github-hosted' or '--deny-self-hosted-runners' not in options]
        if not all(checks):
            continue
    result = {'attestation': {'bundle': bundle}, 'verificationResult': {
        'mediaType': 'application/vnd.dev.sigstore.verificationresult+json;version=0.1',
        'signature': {'certificate': cert}, 'statement': statement,
        'verifiedTimestamps': [] if MODE == 'notimestamp' else
        [{'type': 'Tlog', 'uri': 'https://rekor.sigstore.dev', 'timestamp': '2026-10-21T00:00:00Z'}]}}
    results.append(result)
if not results:
    sys.stderr.write('Error: verifying with issuer "sigstore.dev"\n')
    sys.exit(1)
if MODE == 'garbage':
    print('not json'); sys.exit(0)
if MODE == 'double':
    results = results * 2
json.dump(results, sys.stdout)
'''


def fake_gh(directory, mode='strict'):
    path = Path(directory) / ('gh-' + mode)
    path.write_text('#!' + sys.executable + '\n' + FAKE_GH % {'mode': mode, 'key': KEY, 'version': VERSION})
    path.chmod(0o700)
    return path


def snappy_literal(data):
    """Valid raw Snappy using literal blocks only."""
    out, size = bytearray(), len(data)
    while True:
        byte = size & 0x7f
        size >>= 7
        out.append(byte | (0x80 if size else 0))
        if not size:
            break
    for start in range(0, len(data), 65536):
        chunk = data[start:start + 65536]
        out += bytes([61 << 2]) + (len(chunk) - 1).to_bytes(2, 'little') + chunk
    return bytes(out)


def certificate(commit_sha, run_id, attempt, **overrides):
    signer = 'https://github.com/{}/{}@refs/heads/{}'.format(REPOSITORY, WORKFLOW, BRANCH)
    cert = {'issuer': POLICY['oidc_issuer'], 'sourceRepositoryURI': 'https://github.com/' + REPOSITORY,
            'sourceRepositoryIdentifier': str(REPO_ID), 'sourceRepositoryOwnerIdentifier': str(OWNER_ID),
            'sourceRepositoryRef': 'refs/heads/' + BRANCH, 'sourceRepositoryDigest': commit_sha,
            'buildSignerURI': signer, 'buildSignerDigest': commit_sha, 'subjectAlternativeName': signer,
            'runnerEnvironment': 'github-hosted', 'buildTrigger': 'push',
            'runInvocationURI': 'https://github.com/{}/actions/runs/{}/attempts/{}'.format(REPOSITORY, run_id, attempt)}
    cert.update(overrides)
    return cert


def sign(subjects, cert, key=KEY, expired=False, predicate=None):
    body = {'certificate': cert, 'expired': expired, 'statement': {
        '_type': 'https://in-toto.io/Statement/v1', 'predicateType': predicate or POLICY['predicate_type'],
        'subject': [{'name': n, 'digest': {'sha256': d}} for n, d in sorted(subjects.items())], 'predicate': {}}}
    return {'mediaType': 'application/vnd.dev.sigstore.bundle.v0.3+json', 'fixture': body,
            'signature': hmac.new(key, json.dumps(body, sort_keys=True).encode(), 'sha256').hexdigest()}


def sums(files):
    names = sorted(n for n in files if n != 'SHA256SUMS')
    return ''.join(hashlib.sha256(files[n]).hexdigest() + '  ' + n + '\n' for n in names).encode()


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        for name in sorted(files):
            zf.writestr(name, files[name])
    return buffer.getvalue()


def subjects_of(files):
    return {Path(n).name: hashlib.sha256(c).hexdigest() for n, c in files.items() if n != 'SHA256SUMS'}


class Response:
    def __init__(self, body, headers=None):
        self.status, self._body = 200, io.BytesIO(body)
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self, n=-1):
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGitHub:
    """GitHub REST API, attestation blob storage and artifact storage for one or more runs."""

    def __init__(self):
        self.routes, self.requests, self.fail = {}, [], None

    def json(self, path, data):
        self.routes['https://api.github.com' + path] = json.dumps(data).encode()

    def raw(self, url, body):
        self.routes[url] = body

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, dict(request.header_items())))
        if self.fail:
            raise self.fail
        if url in self.routes:
            body = self.routes[url]
            if isinstance(body, tuple):          # redirect
                headers = email.message.Message()
                headers['Location'] = body[1]
                raise urllib.error.HTTPError(url, body[0], 'Found', headers, io.BytesIO(b''))
            return Response(body)
        raise urllib.error.HTTPError(url, 404, 'Not Found', email.message.Message(), io.BytesIO(b''))


def publish(github, files, run_id, attempt, commit_sha, bundles=None, run=None, jobs=None, artifact=None,
            attestation_repo=REPO_ID):
    """Serve one run of the release-candidate workflow and its attestation."""
    data = archive(files)
    name = 'release-candidate-{}-{}'.format(commit_sha, attempt)
    owner = {'id': OWNER_ID, 'login': 'rathod9771'}
    repo = {'full_name': REPOSITORY, 'id': REPO_ID, 'owner': owner}
    record = {'id': run_id, 'run_attempt': attempt, 'head_sha': commit_sha, 'head_branch': BRANCH, 'event': 'push',
              'status': 'completed', 'conclusion': 'success', 'path': WORKFLOW, 'repository': dict(repo),
              'head_repository': dict(repo)}
    record.update(run or {})
    base = '/repos/{}/actions/runs/{}'.format(REPOSITORY, run_id)
    github.json(base + '/attempts/' + str(attempt), record)
    job = {'run_id': run_id, 'run_attempt': attempt, 'status': 'completed', 'conclusion': 'success',
           'labels': ['ubuntu-latest'], 'runner_group_name': 'GitHub Actions'}
    github.json(base + '/attempts/' + str(attempt) + '/jobs?per_page=100',
                {'jobs': jobs if jobs is not None else [dict(job, name='build'), dict(job, name='attest')]})
    item = {'id': run_id * 10 + attempt, 'name': name, 'size_in_bytes': len(data), 'expired': False,
            'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
            'workflow_run': {'id': run_id, 'head_sha': commit_sha, 'repository_id': REPO_ID, 'head_repository_id': REPO_ID}}
    item.update(artifact or {})
    github.json(base + '/artifacts?per_page=100&name=' + name, {'artifacts': [item]})
    blob = 'https://pipelinesghubeus1.actions.githubusercontent.com/artifact/{}.zip?sig=SIGNED'.format(item['id'])
    github.raw('https://api.github.com/repos/{}/actions/artifacts/{}/zip'.format(REPOSITORY, item['id']), (302, blob))
    github.raw(blob, data)
    if bundles is None:
        bundles = [sign(subjects_of(files), certificate(commit_sha, run_id, attempt))]
    manifest_digest = hashlib.sha256(files['release-manifest.json']).hexdigest()
    entries = []
    for index, bundle in enumerate(bundles):
        url = BLOB + '{}/{}-{}.json.sn'.format(REPO_ID, manifest_digest[:12], index)
        github.raw(url, snappy_literal(json.dumps(bundle).encode()))
        entries.append({'repository_id': attestation_repo, 'bundle': None, 'bundle_url': url})
    if entries:
        github.json('/repos/{}/attestations/sha256:{}?per_page=6'.format(REPOSITORY, manifest_digest),
                    {'attestations': entries})
    return data


def evidence(root, entries, reader):
    # Future state: every bound image pinned by digest and registry-verified in CI.
    real = release_images.assess(root, entries, reader)
    for record in real['components'].values():
        record.update(status='PINNED_DIGEST', digest='sha256:' + '1' * 64, portable=True, registry_verification='VERIFIED')
    return real


def load(output):
    output = Path(output)
    return {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob('*') if p.is_file()}


def remanifest(files, change):
    """Builder-side variant: edit the manifest and keep the artifact internally consistent."""
    files = dict(files)
    manifest = json.loads(files['release-manifest.json'])
    change(manifest)
    files['release-manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    files['SHA256SUMS'] = sums(files)
    return files


class VerificationFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='release-verify-')
        directory = Path(cls.temp.name)
        repo, _ = FixtureRepo.clone(directory)
        cls.releases = {}
        builds = (('first', 'cran-oai', 424242, 'push', BRANCH), ('second', 'vcran-oai', 424250, 'push', BRANCH),
                  ('manual', 'cran-oai', 424260, 'workflow_dispatch', 'feature/p3-trial'))
        for label, key, run_id, event, branch in builds:
            previous = git(repo, 'rev-parse', 'HEAD')
            head = commit(repo, {'config/scenarios.json': registry_edit(repo, key, display_name=key + ' ' + label)})
            env = ci_env(head, branch=branch, event=event, GITHUB_RUN_ID=str(run_id))
            with mock.patch.object(build_release, 'assess', side_effect=evidence):
                manifest = build_release.build_release(repo, previous, head, directory / label, env,
                                                       ci_validation='passed', clock=lambda: 0)
            cls.releases[label] = (head, run_id, load(directory / label), manifest)
        cls.policy = copy.deepcopy(POLICY)
        cls.policy['recheck_registry'] = False

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='p3-case-')
        self.addCleanup(self.work.cleanup)
        self.dir = Path(self.work.name)
        self.state = self.dir / 'state'
        self.state.mkdir(mode=0o700)
        self.github = FakeGitHub()

    def verifier(self, mode='strict', **kwargs):
        gh = fake_gh(self.dir, mode)
        options = dict(version=VERSION, sha256=hashlib.sha256(gh.read_bytes()).hexdigest())
        options.update(kwargs)
        return GhVerifier(gh, options['version'], options['sha256'], self.dir / ('gh-work-' + mode))

    def verify(self, label='first', files=None, publish_kwargs=None, source='zip', policy=None, verifier=None,
               run_id=None, attempt=1, state=None, token=None, **kwargs):
        head, default_run, original, _ = self.releases[label]
        files = original if files is None else files
        run_id = run_id or default_run
        data = publish(self.github, files, run_id, attempt, head, **(publish_kwargs or {}))
        if source == 'zip':
            path = self.dir / 'artifact.zip'
            path.write_bytes(data)
            chosen = ('zip', path)
        elif source == 'directory':
            target = self.dir / 'artifact'
            shutil.rmtree(target, ignore_errors=True)
            for name, content in files.items():
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                (target / name).write_bytes(content)
            chosen = ('directory', target)
        else:
            chosen = ('download', None)
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=self.github, token=token)
        return release_verify.verify_candidate(policy or self.policy, run_id, attempt, chosen, state or self.state,
                                               fetcher, verifier or self.verifier(), **kwargs)

    def rejected(self, code, result):
        self.assertEqual((result['status'], result.get('code')), ('REJECTED', code), result)
        self.assertFalse(result['publication_candidate'])
        self.assertFalse(result['deployment_authorized'])
        self.assertFalse((self.state / release_verify.RECORDS_NAME).exists())


class TrustedReleaseTests(VerificationFixture):
    def test_valid_attestation_is_verified_recorded_and_never_authorized(self):
        result = self.verify()
        head, run_id, files, _ = self.releases['first']
        self.assertEqual(result['status'], 'VERIFIED', result)
        self.assertEqual((result['provenance'], result['publication_candidate'], result['deployment_authorized']),
                         ('VERIFIED', True, False))
        self.assertEqual((result['commit'], result['run_id'], result['run_attempt'], result['branch']),
                         (head, run_id, 1, BRANCH))
        self.assertEqual(result['scenarios'], ['cran-oai'])
        self.assertTrue(result['artifact']['archive_digest_checked'])
        self.assertEqual(result['verifier'], {'tool': 'gh', 'version': VERSION,
                                              'sha256': result['verifier']['sha256']})
        # Only verification state is written: no active-RAN file, no lifecycle lock, no ledger.
        self.assertEqual(sorted(p.name for p in self.state.iterdir()),
                         ['.release-verify.lock', 'verified-releases', 'verified-releases.json'])
        record = json.loads((self.state / release_verify.RECORDS_NAME).read_text())['releases']
        self.assertEqual([(r['manifest_sha256'], r['deployment_authorized']) for r in record],
                         [(result['manifest_sha256'], False)])
        # The preserved identity re-hashes and converts into P1 lifecycle input.
        stored, release, blobs = release_verify.load_verified(self.state, result['manifest_sha256'], REGISTRY, self.policy)
        self.assertEqual((release['commit'], release['branch'], release['target'], release['ci_status']),
                         (head, BRANCH, 'hp-lab', 'success'))
        self.assertEqual(lifecycle.verify_release(release, blobs, REGISTRY, {'branch': BRANCH, 'target': 'hp-lab'})[0][0],
                         'cran-oai')
        self.assertEqual(stored['run_id'], run_id)

    def test_directory_source_relies_on_attested_bytes(self):
        result = self.verify(source='directory')
        self.assertEqual(result['status'], 'VERIFIED', result)
        self.assertFalse(result['artifact']['archive_digest_checked'])

    def test_download_sends_token_only_to_the_api(self):
        result = self.verify(source='download', token='ghp_' + 'A' * 36)
        self.assertEqual(result['status'], 'VERIFIED', result)
        for url, headers in self.github.requests:
            authorized = any(k.lower() == 'authorization' for k in headers)
            self.assertEqual(authorized, url.endswith('/zip') and url.startswith('https://api.github.com/'), url)
        self.assertNotIn('AAAAAAAA', json.dumps(result))

    def test_missing_attestation(self):
        self.rejected('ATTESTATION_MISSING', self.verify(publish_kwargs={'bundles': []}))

    def test_altered_manifest(self):
        _, _, files, _ = self.releases['first']
        altered = dict(files, **{'release-manifest.json': files['release-manifest.json'] + b' '})
        altered['SHA256SUMS'] = sums(altered)
        # The genuine attestation is replayed for the altered bytes: the subject digest no longer matches.
        genuine = [sign(subjects_of(files), certificate(self.releases['first'][0], self.releases['first'][1], 1))]
        self.rejected('ATTESTATION_INVALID', self.verify(files=altered, publish_kwargs={'bundles': genuine},
                                                         source='directory'))
        # Without re-signing, the archive no longer matches the digest GitHub recorded.
        path_files = dict(files, **{'release-manifest.json': files['release-manifest.json'] + b' '})
        self.github = FakeGitHub()
        head, run_id, _, _ = self.releases['first']
        publish(self.github, files, run_id, 1, head)
        (self.dir / 'artifact.zip').write_bytes(archive(path_files))
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=self.github)
        result = release_verify.verify_candidate(self.policy, run_id, 1, ('zip', self.dir / 'artifact.zip'), self.state,
                                                 fetcher, self.verifier())
        self.rejected('ARTIFACT_ALTERED', result)

    def test_altered_package(self):
        _, _, files, _ = self.releases['first']
        name = next(n for n in files if n.startswith('packages/'))
        tampered = dict(files, **{name: files[name] + b'\0'})
        self.rejected('ARTIFACT_ALTERED', self.verify(files=tampered, source='directory',
                                                      publish_kwargs={'bundles': [sign(subjects_of(files), certificate(
                                                          self.releases['first'][0], self.releases['first'][1], 1))]}))
        tampered['SHA256SUMS'] = sums(tampered)        # consistent checksums do not help
        genuine = [sign(subjects_of(files), certificate(self.releases['first'][0], self.releases['first'][1], 1))]
        self.rejected('MANIFEST_REJECTED', self.verify(files=tampered, source='directory',
                                                       publish_kwargs={'bundles': genuine}))

    def test_archive_must_be_the_exact_bytes_github_recorded(self):
        head, run_id, files, _ = self.releases['first']
        publish(self.github, files, run_id, 1, head)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:     # same files, other archive
            for name in sorted(files, reverse=True):
                zf.writestr(name, files[name])
        (self.dir / 'artifact.zip').write_bytes(buffer.getvalue())
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=self.github)
        self.rejected('ARTIFACT_ALTERED', release_verify.verify_candidate(
            self.policy, run_id, 1, ('zip', self.dir / 'artifact.zip'), self.state, fetcher, self.verifier()))

    def test_binding_rechecks_every_identity_field(self):
        head, run_id, files, _ = self.releases['first']
        manifest = hashlib.sha256(files['release-manifest.json']).hexdigest()
        identity = {'commit': head, 'run_id': run_id, 'run_attempt': 1}

        def result(cert, subjects=None):
            bundle = sign(subjects or subjects_of(files), cert)['fixture']
            return [{'verificationResult': {'signature': {'certificate': bundle['certificate']},
                                            'statement': bundle['statement'],
                                            'verifiedTimestamps': [{'timestamp': '2026-10-21T00:00:00Z'}]}}]
        self.assertTrue(release_attestation.binding(result(certificate(head, run_id, 1)), manifest, POLICY, identity)['verified'])
        for field, value in (('sourceRepositoryIdentifier', '99'), ('sourceRepositoryURI', 'https://github.com/x/y'),
                             ('sourceRepositoryOwnerIdentifier', '98'), ('issuer', 'https://example.com'),
                             ('buildTrigger', 'workflow_dispatch'), ('runnerEnvironment', 'self-hosted'),
                             ('sourceRepositoryRef', 'refs/heads/main'), ('buildSignerDigest', '0' * 40)):
            with self.subTest(field=field), self.assertRaises(AttestationError) as caught:
                release_attestation.binding(result(certificate(head, run_id, 1, **{field: value})), manifest, POLICY, identity)
            self.assertEqual(caught.exception.code, 'ATTESTATION_IDENTITY')
        others = {n: d for n, d in subjects_of(files).items() if n != 'release-manifest.json'}
        with self.assertRaises(AttestationError) as caught:
            release_attestation.binding(result(certificate(head, run_id, 1), others), manifest, POLICY, identity)
        self.assertEqual(caught.exception.code, 'ATTESTATION_SUBJECT')

    def test_attestation_lookup_is_bounded_before_download(self):
        head, run_id, files, _ = self.releases['first']
        publish(self.github, files, run_id, 1, head, bundles=[sign(subjects_of(files), certificate(head, run_id, 1))] * 6)
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=self.github)
        digest = hashlib.sha256(files['release-manifest.json']).hexdigest()
        with self.assertRaises(release_fetch.FetchError) as caught:
            fetcher.attestation_bundles(digest, REPO_ID, 5)
        self.assertEqual(caught.exception.code, 'ATTESTATION_AMBIGUOUS')
        self.assertFalse(any('blob.core.windows.net' in url for url, _ in self.github.requests))

    def test_attested_subjects_must_equal_the_artifacts(self):
        head, run_id, files, _ = self.releases['first']
        subjects = subjects_of(files)
        dropped = dict(subjects)
        dropped.pop(next(n for n in subjects if n.endswith('.tar.gz')))
        self.rejected('ATTESTATION_SUBJECT', self.verify(publish_kwargs={
            'bundles': [sign(dropped, certificate(head, run_id, 1))]}))

    def test_wrong_repository(self):
        other = {'full_name': 'someone/5g-kubernetes', 'id': 99, 'owner': {'id': 98}}
        self.rejected('WRONG_REPOSITORY', self.verify(publish_kwargs={'run': {'head_repository': other}}))
        self.github = FakeGitHub()
        self.rejected('ATTESTATION_IDENTITY', self.verify(publish_kwargs={'attestation_repo': 99}))
        self.github = FakeGitHub()
        head, run_id, files, _ = self.releases['first']
        forged = certificate(head, run_id, 1, sourceRepositoryURI='https://github.com/someone/5g-kubernetes',
                             sourceRepositoryIdentifier='99')
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={'bundles': [sign(subjects_of(files), forged)]}))
        self.github = FakeGitHub()
        self.rejected('ATTESTATION_IDENTITY', self.verify(publish_kwargs={'bundles': [sign(subjects_of(files), forged)]},
                                                          verifier=self.verifier('lenient')))

    def test_wrong_branch(self):
        self.rejected('WRONG_BRANCH', self.verify(publish_kwargs={'run': {'head_branch': 'feature/p3-trial'}}))
        self.github = FakeGitHub()
        head, run_id, files, _ = self.releases['first']
        forged = certificate(head, run_id, 1, sourceRepositoryRef='refs/heads/feature/x')
        self.rejected('ATTESTATION_IDENTITY', self.verify(publish_kwargs={'bundles': [sign(subjects_of(files), forged)]},
                                                          verifier=self.verifier('lenient')))

    def test_wrong_workflow(self):
        self.rejected('WRONG_WORKFLOW', self.verify(publish_kwargs={'run': {'path': '.github/workflows/other.yml'}}))
        head, run_id, files, _ = self.releases['first']
        signer = 'https://github.com/{}/.github/workflows/other.yml@refs/heads/{}'.format(REPOSITORY, BRANCH)
        forged = certificate(head, run_id, 1, buildSignerURI=signer, subjectAlternativeName=signer)
        for mode, code in (('strict', 'ATTESTATION_INVALID'), ('lenient', 'ATTESTATION_IDENTITY')):
            self.github = FakeGitHub()
            self.rejected(code, self.verify(publish_kwargs={'bundles': [sign(subjects_of(files), forged)]},
                                            verifier=self.verifier(mode)))

    def test_wrong_commit(self):
        head, run_id, files, _ = self.releases['first']
        other = self.releases['second'][0]
        # GitHub reports another commit for the run: the artifact for that commit is this one's bytes.
        publish(self.github, files, run_id, 1, other)
        (self.dir / 'artifact.zip').write_bytes(archive(files))
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=self.github)
        self.rejected('WRONG_COMMIT', release_verify.verify_candidate(
            self.policy, run_id, 1, ('zip', self.dir / 'artifact.zip'), self.state, fetcher, self.verifier()))
        self.github = FakeGitHub()
        forged = certificate(other, run_id, 1)
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={'bundles': [sign(subjects_of(files), forged)]}))

    def test_wrong_attempt(self):
        head, run_id, files, _ = self.releases['first']
        # Attempt 2 of the same run serving attempt 1's artifact and attestation.
        self.rejected('WRONG_ATTEMPT', self.verify(attempt=2, publish_kwargs={
            'bundles': [sign(subjects_of(files), certificate(head, run_id, 1))]}))
        self.github = FakeGitHub()
        stale = remanifest(files, lambda m: m['workflow'].update(run_attempt=2))
        self.rejected('ATTESTATION_IDENTITY', self.verify(files=stale, attempt=2, verifier=self.verifier('lenient'),
                                                          publish_kwargs={'bundles': [sign(subjects_of(stale),
                                                                                           certificate(head, run_id, 1))]}))
        self.github = FakeGitHub()
        self.rejected('WRONG_ATTEMPT', self.verify(publish_kwargs={'run': {'run_attempt': 2}}))

    def test_failed_ci_result(self):
        self.rejected('CI_FAILED', self.verify(publish_kwargs={'run': {'conclusion': 'failure'}}))
        self.github = FakeGitHub()
        self.rejected('CI_FAILED', self.verify(publish_kwargs={'run': {'status': 'in_progress', 'conclusion': None}}))
        self.github = FakeGitHub()
        job = {'run_id': 424242, 'run_attempt': 1, 'status': 'completed', 'labels': ['ubuntu-latest'],
               'runner_group_name': 'GitHub Actions'}
        self.rejected('CI_FAILED', self.verify(publish_kwargs={'jobs': [dict(job, name='build', conclusion='success'),
                                                                        dict(job, name='attest', conclusion='skipped')]}))
        self.github = FakeGitHub()
        self.rejected('CI_FAILED', self.verify(publish_kwargs={'jobs': [dict(job, name='build', conclusion='success')]}))
        self.github = FakeGitHub()
        failed = remanifest(self.releases['first'][2], lambda m: m['ci_validation'].update(status='not-run'))
        self.rejected('MANIFEST_REJECTED', self.verify(files=failed))

    def test_self_hosted_runner(self):
        job = {'run_id': 424242, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
               'labels': ['self-hosted', 'Linux', 'X64'], 'runner_group_name': 'Default'}
        self.rejected('SELF_HOSTED', self.verify(publish_kwargs={'jobs': [dict(job, name='build'), dict(job, name='attest')]}))
        self.github = FakeGitHub()
        head, run_id, files, _ = self.releases['first']
        forged = certificate(head, run_id, 1, runnerEnvironment='self-hosted')
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={'bundles': [sign(subjects_of(files), forged)]}))

    def test_unsupported_signer(self):
        head, run_id, files, _ = self.releases['first']
        untrusted = sign(subjects_of(files), certificate(head, run_id, 1), key=b'another-root')
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={'bundles': [untrusted]}))
        self.github = FakeGitHub()
        foreign_issuer = certificate(head, run_id, 1, issuer='https://accounts.example.com')
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={
            'bundles': [sign(subjects_of(files), foreign_issuer)]}))
        self.github = FakeGitHub()
        other_predicate = sign(subjects_of(files), certificate(head, run_id, 1), predicate='https://example.com/custom')
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={'bundles': [other_predicate]}))

    def test_expired_or_invalid_verification_evidence(self):
        head, run_id, files, _ = self.releases['first']
        expired = sign(subjects_of(files), certificate(head, run_id, 1), expired=True)
        self.rejected('ATTESTATION_INVALID', self.verify(publish_kwargs={'bundles': [expired]}))
        for mode, code in (('garbage', 'ATTESTATION_MALFORMED'), ('notimestamp', 'ATTESTATION_MALFORMED'),
                           ('double', 'ATTESTATION_AMBIGUOUS')):
            self.github = FakeGitHub()
            self.rejected(code, self.verify(verifier=self.verifier(mode)))
        self.github = FakeGitHub()
        self.rejected('ATTESTATION_AMBIGUOUS', self.verify(publish_kwargs={'bundles': [
            sign(subjects_of(files), certificate(head, run_id, 1))] * 6}))

    def test_unsupported_or_missing_tooling(self):
        self.rejected('UNSUPPORTED_TOOLING', self.verify(verifier=GhVerifier(self.dir / 'absent-gh', VERSION, '0' * 64,
                                                                             self.dir / 'w')))
        self.rejected('UNSUPPORTED_TOOLING', self.verify(verifier=self.verifier(sha256='0' * 64)))
        self.rejected('UNSUPPORTED_TOOLING', self.verify(verifier=self.verifier(version='2.45.0')))
        writable = self.verifier()
        writable.executable.chmod(0o777)
        self.rejected('UNSUPPORTED_TOOLING', self.verify(verifier=writable))
        self.assertEqual(self.github.requests, [], 'tooling is checked before any network access')

    def test_verifier_runs_without_github_credentials(self):
        with mock.patch.dict(os.environ, {'GH_TOKEN': 'ghp_' + 'B' * 36, 'GITHUB_TOKEN': 'x' * 40}):
            self.assertEqual(self.verify()['status'], 'VERIFIED')    # the fake gh exits 97 if it sees a token

    def test_unverified_or_local_images_and_missing_digests(self):
        _, _, files, _ = self.releases['first']
        cases = (('IMAGE_UNVERIFIED', lambda c: c.update(registry_verification='NOT_PERFORMED')),
                 ('IMAGE_DIGEST_MISSING', lambda c: c.update(status='MISSING_DIGEST', digest=None)),
                 ('LOCAL_IMAGE', lambda c: c.update(repository='localhost/5g-kubernetes/srsran')),
                 ('LOCAL_IMAGE', lambda c: c.update(status='LOCAL_ONLY', acquisition='local')),
                 ('IMAGE_DIGEST_MISSING', lambda c: c.update(status='MUTABLE_TAG', digest=None)))
        for code, change in cases:
            with self.subTest(code=code):
                variant = remanifest(files, lambda m: change(m['images']['components']['oai-du']))
                self.github = FakeGitHub()
                self.rejected(code, self.verify(files=variant))

    def test_target_side_registry_recheck(self):
        policy = dict(self.policy, recheck_registry=True)
        with mock.patch.object(release_verify, 'verify_registry', return_value='DIGEST_MISMATCH') as check:
            self.rejected('IMAGE_UNVERIFIED', self.verify(policy=policy))
        self.assertTrue(check.called)
        self.github = FakeGitHub()
        with mock.patch.object(release_verify, 'verify_registry', return_value='VERIFIED') as check:
            self.assertEqual(self.verify(policy=policy)['status'], 'VERIFIED')
        self.assertEqual(check.call_count, 2)          # oai-cu and oai-du, each re-checked on the target

    def test_blocked_scenario(self):
        _, _, files, _ = self.releases['first']
        blocked = remanifest(files, lambda m: m['change_impact'].update(blocked={'oran-srsran': 'chart mismatch'}))
        self.rejected('BLOCKED_SCENARIO', self.verify(files=blocked))
        self.github = FakeGitHub()
        renamed = remanifest(files, lambda m: m['packages'][0].update(scenario='oran-srsran'))
        self.rejected('BLOCKED_SCENARIO', self.verify(files=renamed))

    def test_unapproved_target(self):
        _, _, files, _ = self.releases['first']
        self.rejected('WRONG_TARGET', self.verify(files=remanifest(files, lambda m: m['target'].update(environment='prod'))))
        self.github = FakeGitHub()
        self.rejected('WRONG_TARGET', self.verify(files=remanifest(files, lambda m: m['target'].update(
            architecture='linux/arm64'))))

    def test_manifest_eligibility_claims_are_not_trusted(self):
        _, _, files, _ = self.releases['first']
        claim = remanifest(files, lambda m: m['eligibility'].update(automatic_deployment_allowed=True))
        self.rejected('MANIFEST_CLAIM', self.verify(files=claim))
        self.github = FakeGitHub()
        modest = remanifest(files, lambda m: m['eligibility'].update(publication_allowed=False, blockers=['x']))
        self.rejected('MANIFEST_DISAGREES', self.verify(files=modest))
        self.github = FakeGitHub()
        # A manifest claiming eligibility while its evidence says otherwise is rejected on the evidence.
        bold = remanifest(files, lambda m: (m['images']['components']['oai-cu'].update(status='MISSING_DIGEST', digest=None),
                                            m['eligibility'].update(publication_allowed=True, blockers=[])))
        self.rejected('IMAGE_DIGEST_MISSING', self.verify(files=bold))

    def test_development_and_manual_releases_are_rejected(self):
        head, run_id, files, manifest = self.releases['manual']
        self.assertEqual(manifest['channel'], 'manual')
        # GitHub's record says workflow_dispatch on a feature branch.
        self.rejected('NOT_RELEASE_CHANNEL', self.verify('manual', publish_kwargs={'run': {
            'event': 'workflow_dispatch', 'head_branch': 'feature/p3-trial'}}))
        self.github = FakeGitHub()
        self.rejected('WRONG_BRANCH', self.verify('manual', publish_kwargs={'run': {'head_branch': 'feature/p3-trial'}}))
        # Even if the API claimed a release push, the manifest's own identity is the manual build.
        self.github = FakeGitHub()
        self.rejected('WRONG_BRANCH', self.verify('manual'))
        self.github = FakeGitHub()
        development = remanifest(self.releases['first'][2], lambda m: m.update(channel='development'))
        self.rejected('NOT_RELEASE_CHANNEL', self.verify(files=development))

    def test_empty_package_set(self):
        _, _, files, _ = self.releases['first']
        empty = {n: c for n, c in files.items() if n in ('release-manifest.json', 'SHA256SUMS')}
        empty = remanifest(empty, lambda m: m.update(packages=[], artifacts={}))
        self.rejected('EMPTY_RELEASE', self.verify(files=empty))

    def test_unexpected_artifact_content(self):
        _, _, files, _ = self.releases['first']
        extra = dict(files, **{'packages/extra.sh': b'#!/bin/sh\n'})
        extra['SHA256SUMS'] = sums(extra)
        self.rejected('ARTIFACT_LAYOUT', self.verify(files=extra, source='directory'))
        for bad in ('../escape', '/abs/path', 'packages/../x.tar.gz'):
            with self.subTest(name=bad):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, 'w') as zf:
                    zf.writestr(bad, b'x')
                with self.assertRaises(release_fetch.FetchError):
                    release_fetch.read_archive(buffer.getvalue())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            link = zipfile.ZipInfo('release-manifest.json')
            link.external_attr = (0o120777 << 16)
            zf.writestr(link, '/etc/passwd')
        with self.assertRaises(release_fetch.FetchError):
            release_fetch.read_archive(buffer.getvalue())
        with self.assertRaises(release_fetch.FetchError):
            release_fetch.read_archive(archive({'a': b'0' * 1000}), max_bytes=100)

    def test_duplicate_and_replayed_releases(self):
        first = self.verify()
        self.assertEqual(first['status'], 'VERIFIED', first)
        self.github = FakeGitHub()
        again = self.verify()
        self.assertEqual((again['status'], again['code']), ('REJECTED', 'DUPLICATE_RELEASE'))
        self.github = FakeGitHub()
        second = self.verify('second')
        self.assertEqual(second['status'], 'VERIFIED', second)
        # A fresh host that already accepted the newer release refuses the older one.
        other = self.dir / 'other-state'
        other.mkdir(mode=0o700)
        self.github = FakeGitHub()
        self.assertEqual(self.verify('second', state=other)['status'], 'VERIFIED')
        self.github = FakeGitHub()
        replay = self.verify(state=other)
        self.assertEqual((replay['status'], replay['code']), ('REJECTED', 'REPLAYED_RELEASE'))
        records = json.loads((other / release_verify.RECORDS_NAME).read_text())['releases']
        self.assertEqual([r['run_id'] for r in records], [self.releases['second'][1]])

    def test_network_failures_fail_closed(self):
        for error in (urllib.error.URLError('unreachable'), TimeoutError('timed out'), ConnectionResetError()):
            with self.subTest(error=type(error).__name__):
                self.github = FakeGitHub()
                self.github.fail = error
                self.rejected('NETWORK_ERROR', self.verify())
        self.github = FakeGitHub()
        head, run_id, files, _ = self.releases['first']
        publish(self.github, files, run_id, 1, head)
        digest = hashlib.sha256(files['release-manifest.json']).hexdigest()
        self.github.json('/repos/{}/attestations/sha256:{}?per_page=6'.format(REPOSITORY, digest), {'attestations': [
            {'repository_id': REPO_ID, 'bundle': None, 'bundle_url': 'https://evil.example.com/bundle.json.sn'}]})
        (self.dir / 'artifact.zip').write_bytes(archive(files))
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=self.github)
        result = release_verify.verify_candidate(self.policy, run_id, 1, ('zip', self.dir / 'artifact.zip'), self.state,
                                                 fetcher, self.verifier())
        self.rejected('FETCH_FAILED', result)
        self.assertFalse(any('evil.example.com' in url for url, _ in self.github.requests))

    def test_bounded_responses(self):
        github = FakeGitHub()
        github.json('/big', {'x': 'y' * 5000})
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=github, limits={'api_bytes': 1000})
        with self.assertRaisesRegex(release_fetch.FetchError, 'size limit'):
            fetcher.api_json('/big')
        with self.assertRaises(release_fetch.FetchError):
            release_fetch.snappy_decompress(snappy_literal(b'x' * 5000), 1000)
        for broken in (b'\x05\x10ab', b'\x80\x80\x80\x80\x80\x01', b'\x03\x09\x00', b'\x08\x00a\x09\x05'):
            with self.assertRaises(release_fetch.FetchError):
                release_fetch.snappy_decompress(broken, 1000)
        # literal "abc" followed by a 6-byte copy at offset 3
        self.assertEqual(release_fetch.snappy_decompress(b'\x09\x08abc\x09\x03', 100), b'abcabcabc')

    def test_concurrent_verification_records_once(self):
        results = []
        template = self.verifier()

        def run(index):
            github = FakeGitHub()
            head, run_id, files, _ = self.releases['first']
            data = publish(github, files, run_id, 1, head)
            path = self.dir / 'artifact-{}.zip'.format(index)
            path.write_bytes(data)
            verifier = GhVerifier(template.executable, VERSION, template.sha256, self.dir / 'gh-work-{}'.format(index))
            fetcher = release_fetch.Fetcher(REPOSITORY, opener=github)
            results.append(release_verify.verify_candidate(self.policy, run_id, 1, ('zip', path), self.state, fetcher,
                                                           verifier))
        threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual(sorted(r['status'] if r['status'] == 'VERIFIED' else r['code'] for r in results),
                         ['DUPLICATE_RELEASE', 'DUPLICATE_RELEASE', 'VERIFIED'])
        records = json.loads((self.state / release_verify.RECORDS_NAME).read_text())['releases']
        self.assertEqual(len(records), 1)
        self.assertEqual(sorted(p.name for p in (self.state / release_verify.STORE_NAME).iterdir()),
                         [records[0]['manifest_sha256']])

    def test_stored_release_is_rehashed_before_lifecycle_use(self):
        result = self.verify()
        store = self.state / result['store']
        package = next((store / 'packages').iterdir())
        package.chmod(0o600)
        package.write_bytes(package.read_bytes() + b'\0')
        with self.assertRaises(release_verify.VerificationRejected) as caught:
            release_verify.load_verified(self.state, result['manifest_sha256'], REGISTRY, self.policy)
        self.assertEqual(caught.exception.code, 'ARTIFACT_ALTERED')
        with self.assertRaises(release_verify.VerificationRejected):
            release_verify.load_verified(self.state, '0' * 64, REGISTRY, self.policy)


FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'release_verify'
WORKFLOW_FILE = Path(__file__).resolve().parents[1] / WORKFLOW


class GitHubCompatibilityTests(VerificationFixture):
    """P3 against the real P2 workflow definition and recorded GitHub responses."""

    workflow = __import__('yaml').safe_load(WORKFLOW_FILE.read_text())

    def test_artifact_layout_and_name_match_the_workflow(self):
        build = self.workflow['jobs']['build']
        script = next(s['run'] for s in build['steps'] if s.get('id') == 'release')
        self.assertIn('--output "$RUNNER_TEMP/release"', script)
        upload = next(s for s in build['steps'] if s.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['with']['path'], '${{ runner.temp }}/release/')     # zip root == build output root
        self.assertEqual(upload['with']['name'], 'release-candidate-${{ github.sha }}-${{ github.run_attempt }}')
        download = next(s for s in self.workflow['jobs']['attest']['steps']
                        if s.get('uses', '').startswith('actions/download-artifact@'))
        self.assertEqual(download['with']['name'], upload['with']['name'])   # attest signs this attempt's artifact only
        _, _, files, _ = self.releases['first']
        release_verify.check_layout(files)

    def test_attested_subjects_match_the_attest_step(self):
        """Emulate actions/attest v2.4.0 (path subjects: glob, basename name, sha256, one statement)."""
        step = next(s for s in self.workflow['jobs']['attest']['steps']
                    if s.get('uses', '').startswith('actions/attest-build-provenance@'))
        self.assertTrue(step['uses'].endswith('e8998f949152b193b063cb0ec769d69d929409be'))
        patterns = [line.strip() for line in step['with']['subject-path'].splitlines() if line.strip()]
        _, _, files, _ = self.releases['first']
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder) / 'release'                 # download-artifact path: release
            for name, content in files.items():
                (release / name).parent.mkdir(parents=True, exist_ok=True)
                (release / name).write_bytes(content)
            signed = {}
            for pattern in patterns:
                for path in sorted(Path(folder).glob(pattern)):
                    signed[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(signed, release_verify.expected_subjects(files))
        self.assertNotIn('SHA256SUMS', signed)
        self.assertEqual(len(signed), len(files) - 1)

    def test_recorded_p2_run_responses_parse_and_reject(self):
        github = FakeGitHub()
        run_id, name = 39557877523, 'release-candidate-aa12365de40da4d2e9750fe736796df2f0fc8a16-1'
        base = '/repos/{}/actions/runs/{}'.format(REPOSITORY, run_id)
        run = json.loads((FIXTURES / 'p2-manual-run-attempt.json').read_text())
        jobs = json.loads((FIXTURES / 'p2-manual-run-jobs.json').read_text())
        artifacts = json.loads((FIXTURES / 'p2-manual-run-artifacts.json').read_text())
        github.json(base + '/attempts/1', run)
        github.json(base + '/attempts/1/jobs?per_page=100', jobs)
        github.json(base + '/artifacts?per_page=100&name=' + name, artifacts)
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=github)
        with self.assertRaises(release_verify.VerificationRejected) as caught:
            release_verify.check_run(fetcher, POLICY, run_id, 1)
        self.assertEqual(caught.exception.code, 'NOT_RELEASE_CHANNEL')
        # The same real shapes, as a release push would produce them, resolve to the recorded artifact.
        run.update(event='push', head_branch=BRANCH)
        for job in jobs['jobs']:
            job.update(conclusion='success')
        github.json(base + '/attempts/1', run)
        github.json(base + '/attempts/1/jobs?per_page=100', jobs)
        identity = release_verify.check_run(fetcher, POLICY, run_id, 1)
        self.assertEqual(identity['artifact'], {'name': name, 'id': artifacts['artifacts'][0]['id'],
                                                'digest': artifacts['artifacts'][0]['digest'][len('sha256:'):]})
        self.assertEqual(identity['commit'], 'aa12365de40da4d2e9750fe736796df2f0fc8a16')
        # The real manual run skipped attest: that alone rejects it.
        jobs['jobs'][1].update(conclusion='skipped')
        github.json(base + '/attempts/1/jobs?per_page=100', jobs)
        with self.assertRaises(release_verify.VerificationRejected) as caught:
            release_verify.check_run(fetcher, POLICY, run_id, 1)
        self.assertEqual(caught.exception.code, 'CI_FAILED')

    def test_recorded_attestation_api_and_bundle(self):
        listing = json.loads((FIXTURES / 'cli-cli-attestations.json').read_text())
        verified = json.loads((FIXTURES / 'cli-cli-gh-2.101.0-verify.json').read_text())
        subject = verified[0]['verificationResult']['statement']['subject']
        digest = next(s['digest']['sha256'] for s in subject if s['name'] == 'gh_2.101.0_linux_amd64.tar.gz')
        github = FakeGitHub()
        github.json('/repos/cli/cli/attestations/sha256:{}?per_page=6'.format(digest), listing)
        for index, item in enumerate(listing['attestations']):
            github.raw(item['bundle_url'], (FIXTURES / 'cli-cli-bundle-{}.json.sn'.format(index)).read_bytes())
        bundles = release_fetch.Fetcher('cli/cli', opener=github).attestation_bundles(digest, 212613049)
        self.assertEqual(len(bundles), len(listing['attestations']))
        self.assertTrue(all(b['mediaType'] == 'application/vnd.dev.sigstore.bundle.v0.3+json' for b in bundles))
        # GitHub returns every attestation for the digest (here a release attestation and SLSA provenance);
        # gh, restricted to the SLSA predicate, verified exactly one of the decoded bundles.
        types = sorted(json.loads(base64.b64decode(b['dsseEnvelope']['payload']))['predicateType'] for b in bundles)
        self.assertEqual(types, ['https://in-toto.io/attestation/release/v0.2', 'https://slsa.dev/provenance/v1'])
        self.assertEqual(len(verified), 1)
        self.assertIn(verified[0]['attestation']['bundle'], bundles)
        with self.assertRaises(release_fetch.FetchError) as caught:
            release_fetch.Fetcher('cli/cli', opener=github).attestation_bundles(digest, REPO_ID)
        self.assertEqual(caught.exception.code, 'ATTESTATION_IDENTITY')

    def test_real_verifier_output_shape(self):
        verified = json.loads((FIXTURES / 'cli-cli-gh-2.101.0-verify.json').read_text())
        subject = verified[0]['verificationResult']['statement']['subject']
        digest = next(s['digest']['sha256'] for s in subject if s['name'] == 'gh_2.101.0_linux_amd64.tar.gz')
        policy = dict(POLICY, repository='cli/cli', repository_id=212613049, owner_id=59704711, branch='trunk',
                      workflow_path='.github/workflows/deployment.yml', event='workflow_dispatch')
        identity = {'commit': '0cf1092493af067646fc5f3db9421c6a6ec9c938', 'run_id': 34980146643, 'run_attempt': 1}
        proof = release_attestation.binding(verified, digest, policy, identity)
        self.assertEqual((proof['verified'], len(proof['subjects'])), (True, len(subject)))
        for change, code in (({'run_attempt': 2}, 'ATTESTATION_IDENTITY'), ({'run_id': 34980146644}, 'ATTESTATION_IDENTITY'),
                             ({'commit': '1' * 40}, 'ATTESTATION_IDENTITY')):
            with self.assertRaises(AttestationError) as caught:
                release_attestation.binding(verified, digest, policy, dict(identity, **change))
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(AttestationError) as caught:     # the same evidence never satisfies this repository
            release_attestation.binding(verified, digest, POLICY, identity)
        self.assertEqual(caught.exception.code, 'ATTESTATION_IDENTITY')

    def test_rerun_attempts_are_distinct_and_ordered(self):
        head, run_id, files, _ = self.releases['first']
        # Attempt 2's record served with attempt 1's signed artifact: rejected.
        self.rejected('WRONG_ATTEMPT', self.verify(attempt=2, publish_kwargs={
            'bundles': [sign(subjects_of(files), certificate(head, run_id, 1))]}))
        # A genuine attempt-2 build verifies after attempt 1; going back to attempt 1 is a replay.
        attempt2 = remanifest(files, lambda m: m['workflow'].update(run_attempt=2))
        self.github = FakeGitHub()
        self.assertEqual(self.verify()['status'], 'VERIFIED')
        self.github = FakeGitHub()
        second = self.verify(files=attempt2, attempt=2, publish_kwargs={
            'bundles': [sign(subjects_of(attempt2), certificate(head, run_id, 2))]})
        self.assertEqual(second['status'], 'VERIFIED', second)
        other = self.dir / 'restart-state'
        other.mkdir(mode=0o700)
        self.github = FakeGitHub()
        self.assertEqual(self.verify(files=attempt2, attempt=2, state=other, publish_kwargs={
            'bundles': [sign(subjects_of(attempt2), certificate(head, run_id, 2))]})['status'], 'VERIFIED')
        self.github = FakeGitHub()
        replay = self.verify(state=other)
        self.assertEqual((replay['status'], replay['code']), ('REJECTED', 'REPLAYED_RELEASE'))

    def test_replay_protection_survives_process_restart(self):
        result = self.verify()
        self.assertEqual(result['status'], 'VERIFIED', result)
        older = dict(result, manifest_sha256='f' * 64, run_id=result['run_id'] - 1)
        code = (
            'import json, sys\n'
            'sys.path.insert(0, {scripts!r})\n'
            'import release_verify\n'
            'record = json.loads(sys.argv[1])\n'
            'try:\n'
            '    release_verify.record_release({state!r}, record, {{}}, {{}}, 500)\n'
            '    print("RECORDED")\n'
            'except release_verify.VerificationRejected as error:\n'
            '    print(error.code)\n').format(scripts=str(Path(release_verify.__file__).parent), state=str(self.state))
        for record, expected in ((older, 'REPLAYED_RELEASE'), (result, 'DUPLICATE_RELEASE')):
            output = __import__('subprocess').run([sys.executable, '-c', code, json.dumps(record)], capture_output=True,
                                                  text=True, timeout=60)
            self.assertEqual(output.stdout.strip(), expected, output.stderr)
        self.assertEqual(len(json.loads((self.state / release_verify.RECORDS_NAME).read_text())['releases']), 1)


class PolicyAndTokenTests(unittest.TestCase):
    def test_policy_pins_identity_and_verifier(self):
        self.assertEqual((POLICY['repository'], POLICY['branch'], POLICY['workflow_path'], POLICY['event']),
                         (REPOSITORY, BRANCH, WORKFLOW, 'push'))
        self.assertEqual(POLICY['target'], {'environment': 'hp-lab', 'architecture': 'linux/amd64'})
        self.assertRegex(POLICY['verifier']['sha256'], r'^[0-9a-f]{64}$')
        with tempfile.TemporaryDirectory() as folder:
            for change in ({'event': 'workflow_dispatch'}, {'repository_id': 0}, {'verifier': {'tool': 'gh'}}):
                path = Path(folder) / 'policy.json'
                path.write_text(json.dumps(dict(POLICY, **change)))
                with self.assertRaises(release_verify.VerificationRejected):
                    release_verify.load_policy(path)

    def test_token_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'token'
            path.write_text('ghp_' + 'C' * 36 + '\n')
            path.chmod(0o644)
            with self.assertRaises(release_fetch.FetchError):
                release_fetch.read_token(path)
            path.chmod(0o600)
            self.assertEqual(release_fetch.read_token(path), 'ghp_' + 'C' * 36)
            link = Path(folder) / 'link'
            link.symlink_to(path)
            with self.assertRaises(release_fetch.FetchError):
                release_fetch.read_token(link)

    def test_credentials_never_leave_the_api_host(self):
        fetcher = release_fetch.Fetcher(REPOSITORY, opener=FakeGitHub(), token='ghp_' + 'D' * 36)
        with self.assertRaisesRegex(release_fetch.FetchError, 'only sent'):
            fetcher._get('https://example.blob.core.windows.net/x', 10, lambda h: True, auth=True)
        for url in ('http://api.github.com/x', 'https://api.github.com:8443/x', 'https://user:pw@api.github.com/x'):
            with self.assertRaises(release_fetch.FetchError):
                fetcher._get(url, 10, lambda h: True)

    def test_no_deployment_or_active_state_paths_in_p3(self):
        source = '\n'.join((Path(release_verify.__file__).parent / n).read_text()
                           for n in ('release_verify.py', 'release_attestation.py', 'release_fetch.py'))
        for forbidden in ('active-ran', 'process_release', 'publish(', 'kdu_action', 'helm', 'kubectl', '.lifecycle.lock',
                          'nslcm', 'confirm_active', 'systemctl', 'crontab', 'vnfpkgm', 'nsd/v1', 'ns_instances'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
