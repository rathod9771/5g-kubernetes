"""Independent verification of GitHub artifact attestations (Sigstore) on the target.

Cryptographic verification is delegated to an explicitly pinned GitHub CLI binary
(`gh attestation verify --bundle`), identified by version and SHA-256 and run with
an isolated, credential-free environment. Its JSON result is then checked again,
field by field, against the approved repository, branch, commit, workflow and run
attempt, so a permissive verifier invocation cannot widen what is accepted.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

HEX = re.compile(r'[0-9a-f]{64}')
STATEMENT = 'https://in-toto.io/Statement/v1'


class AttestationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class GhVerifier:
    """A pinned `gh` executable. Missing, replaced or differently versioned tooling is rejected."""

    def __init__(self, executable, version, sha256, workdir, timeout=120, max_output=4 << 20, run=subprocess.run):
        self.executable, self.version, self.sha256 = Path(executable), version, sha256
        self.workdir, self.timeout, self.max_output, self._run = Path(workdir), timeout, max_output, run
        self._binary = None     # private, hashed copy that is actually executed

    def _env(self):
        home = self.workdir / 'gh-home'
        for sub in ('', 'config', 'cache', 'state'):
            (home / sub).mkdir(mode=0o700, parents=True, exist_ok=True)
        # No GH_TOKEN/GITHUB_TOKEN: verification uses only the supplied bundle and Sigstore's trust root.
        return {'HOME': str(home), 'GH_CONFIG_DIR': str(home / 'config'), 'XDG_CONFIG_HOME': str(home / 'config'),
                'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_STATE_HOME': str(home / 'state'), 'PATH': '/usr/bin:/bin',
                'LANG': 'C.UTF-8', 'NO_COLOR': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'GH_PROMPT_DISABLED': '1',
                'GH_SPINNER_DISABLED': '1'}

    def _invoke(self, args, output):
        try:
            with open(output, 'wb') as stdout:
                result = self._run([str(self._binary), *args], stdout=stdout, stderr=subprocess.PIPE,
                                   env=self._env(), timeout=self.timeout, check=False, cwd=str(self.workdir))
        except subprocess.TimeoutExpired:
            raise AttestationError('VERIFIER_TIMEOUT', 'Attestation verifier timed out') from None
        except OSError:
            raise AttestationError('UNSUPPORTED_TOOLING', 'Attestation verifier could not be executed') from None
        if Path(output).stat().st_size > self.max_output:
            raise AttestationError('VERIFIER_OUTPUT', 'Attestation verifier output exceeds the limit')
        return result.returncode, (result.stderr or b'')[:4096].decode('utf-8', 'replace')

    def check_tool(self):
        try:
            info = os.lstat(self.executable)
        except OSError:
            raise AttestationError('UNSUPPORTED_TOOLING', 'Pinned attestation verifier is not installed') from None
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_uid not in (0, os.geteuid()):
            raise AttestationError('UNSUPPORTED_TOOLING', 'Attestation verifier must be a regular file writable only by its owner')
        # Execute a private copy, hashed after copying, so the binary cannot change between check and use.
        self.workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        binary = self.workdir / 'gh'
        if binary.exists():
            binary.chmod(0o700)
            binary.unlink()
        shutil.copyfile(self.executable, binary, follow_symlinks=False)
        binary.chmod(0o500)
        if _sha256_file(binary) != self.sha256:
            raise AttestationError('UNSUPPORTED_TOOLING', 'Attestation verifier binary differs from the pinned digest')
        self._binary = binary
        code, _ = self._invoke(['--version'], self.workdir / 'gh-version.out')
        first = (self.workdir / 'gh-version.out').read_bytes()[:200].decode('utf-8', 'replace').splitlines()[:1]
        if code != 0 or not first or not first[0].startswith('gh version ' + self.version + ' '):
            raise AttestationError('UNSUPPORTED_TOOLING', 'Attestation verifier version is not the pinned version')
        return {'tool': 'gh', 'version': self.version, 'sha256': self.sha256}

    def verify(self, subject_path, bundles, policy, identity, trusted_root=None):
        """Run Sigstore verification for one artifact file; returns the parsed verifier results."""
        if self._binary is None:
            raise AttestationError('UNSUPPORTED_TOOLING', 'Attestation verifier was not checked')
        bundle_path = self.workdir / 'bundles.jsonl'
        bundle_path.write_text(''.join(json.dumps(b, sort_keys=True) + '\n' for b in bundles))
        repository, ref = policy['repository'], 'refs/heads/' + policy['branch']
        args = ['attestation', 'verify', str(subject_path), '--bundle', str(bundle_path), '--repo', repository,
                '--signer-workflow', repository + '/' + policy['workflow_path'], '--source-ref', ref,
                '--source-digest', identity['commit'], '--signer-digest', identity['commit'],
                '--cert-oidc-issuer', policy['oidc_issuer'], '--predicate-type', policy['predicate_type'],
                '--deny-self-hosted-runners', '--format', 'json']
        if trusted_root:
            args += ['--custom-trusted-root', str(trusted_root)]
        output = self.workdir / 'verify.json'
        code, stderr = self._invoke(args, output)
        if code != 0:
            reason = next((line.strip() for line in stderr.splitlines() if line.strip()), 'no detail')[:200]
            raise AttestationError('ATTESTATION_INVALID', 'Sigstore verification failed: ' + reason)
        try:
            return json.loads(output.read_bytes().decode('utf-8'),
                              object_pairs_hook=_unique, parse_constant=_no_constant)
        except (UnicodeDecodeError, ValueError):
            raise AttestationError('ATTESTATION_MALFORMED', 'Verifier output is not valid JSON') from None


def _unique(items):
    keys = [k for k, _ in items]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(items)


def _no_constant(name):
    raise ValueError('non-finite number')


def binding(results, subject_sha256, policy, identity):
    """Independently re-check one verified attestation; returns the proof for release_manifest.verify()."""
    if not isinstance(results, list) or not results:
        raise AttestationError('ATTESTATION_MISSING', 'No verified attestation for the artifact')
    if len(results) != 1:
        raise AttestationError('ATTESTATION_AMBIGUOUS', 'More than one verified attestation for the artifact')
    try:
        result = results[0]['verificationResult']
        certificate = result['signature']['certificate']
        statement = result['statement']
        subjects = statement['subject']
        timestamps = result.get('verifiedTimestamps')
    except (KeyError, TypeError):
        raise AttestationError('ATTESTATION_MALFORMED', 'Verifier result lacks certificate or statement') from None
    repository, ref = policy['repository'], 'refs/heads/' + policy['branch']
    signer = 'https://github.com/' + repository + '/' + policy['workflow_path'] + '@' + ref
    run = 'https://github.com/{}/actions/runs/{}/attempts/{}'.format(repository, identity['run_id'], identity['run_attempt'])
    expected = {
        'issuer': policy['oidc_issuer'],
        'sourceRepositoryURI': 'https://github.com/' + repository,
        'sourceRepositoryIdentifier': str(policy['repository_id']),
        'sourceRepositoryOwnerIdentifier': str(policy['owner_id']),
        'sourceRepositoryRef': ref,
        'sourceRepositoryDigest': identity['commit'],
        'buildSignerURI': signer,
        'buildSignerDigest': identity['commit'],
        'subjectAlternativeName': signer,
        'runnerEnvironment': 'github-hosted',
        'buildTrigger': policy['event'],
        'runInvocationURI': run,
    }
    for field, value in expected.items():
        if certificate.get(field) != value:
            raise AttestationError('ATTESTATION_IDENTITY', 'Attestation ' + field + ' does not match the approved release')
    if statement.get('_type') != STATEMENT or statement.get('predicateType') != policy['predicate_type']:
        raise AttestationError('ATTESTATION_MALFORMED', 'Unexpected attestation statement type')
    if not isinstance(timestamps, list) or not timestamps:
        raise AttestationError('ATTESTATION_MALFORMED', 'Attestation has no verified timestamp')
    if not isinstance(subjects, list) or not subjects:
        raise AttestationError('ATTESTATION_MALFORMED', 'Attestation has no subjects')
    named = {}
    for item in subjects:
        name = item.get('name') if isinstance(item, dict) else None
        digest = (item.get('digest') or {}).get('sha256') if isinstance(item, dict) else None
        if not isinstance(name, str) or not isinstance(digest, str) or not HEX.fullmatch(digest) or name in named:
            raise AttestationError('ATTESTATION_MALFORMED', 'Attestation subjects are malformed or ambiguous')
        named[name] = digest
    if len(set(named.values())) != len(named):
        raise AttestationError('ATTESTATION_MALFORMED', 'Attestation subjects repeat a digest')
    if subject_sha256 not in named.values():
        raise AttestationError('ATTESTATION_SUBJECT', 'Attestation does not cover these exact bytes')
    return {'verified': True, 'subject_sha256': subject_sha256, 'repository': repository, 'commit': identity['commit'],
            'ref': ref, 'workflow_path': policy['workflow_path'], 'runner_environment': 'github-hosted',
            'run_id': identity['run_id'], 'run_attempt': identity['run_attempt'], 'signer': signer,
            'subjects': named, 'timestamps': [t.get('timestamp') for t in timestamps if isinstance(t, dict)]}
