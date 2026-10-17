"""Token-rotation safety: revocation, reissue, no leakage, no collateral mutation."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from telecom.research_setup import ensure_sandbox, SANDBOX_ACTORS, SANDBOX_TENANT
from telecom.security import authenticate, Denied
from telecom import rotate_identities


class RotateIdentitiesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'telecom-research'
        self.paths, self.created = ensure_sandbox(self.directory, ROOT)
        self.assertEqual(set(self.created), {'researcher', 'reviewer', 'operator'})
        self.auth_path = Path(self.paths['TELECOM_AUTH_FILE'])
        self.runtime_dir = Path(self.paths['TELECOM_RUNTIME_DIR'])
        self.policy_path = Path(self.paths['TELECOM_EXECUTION_POLICY'])
        self.old_tokens = dict(self.created)
        self.old_principals = {}
        for subject, token in self.old_tokens.items():
            principal = authenticate('Bearer ' + token, path=str(self.auth_path))
            self.old_principals[subject] = (principal.subject, principal.tenant, frozenset(principal.roles))

    def test_old_tokens_authenticate_with_expected_roles_before_rotation(self):
        for subject, token in self.old_tokens.items():
            principal = authenticate('Bearer ' + token, path=str(self.auth_path))
            self.assertEqual(principal.tenant, SANDBOX_TENANT)
            self.assertEqual(set(principal.roles), set(SANDBOX_ACTORS[subject]))

    def test_rotation_prints_nothing_and_leaks_no_token(self):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            handoff = rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        captured = buf_out.getvalue() + buf_err.getvalue()
        self.assertEqual(captured, '')
        for token in self.old_tokens.values():
            self.assertNotIn(token, captured)
        new_tokens = json.loads(handoff.read_text())
        for token in new_tokens.values():
            self.assertNotIn(token, captured)

    def test_new_tokens_written_only_to_fresh_owner_only_sidecar(self):
        handoff = rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        self.assertEqual(handoff.parent, self.auth_path.parent)
        self.assertEqual(handoff.stat().st_mode & 0o777, 0o600)
        new_tokens = json.loads(handoff.read_text())
        self.assertEqual(set(new_tokens), {'researcher', 'reviewer', 'operator'})

    def test_old_tokens_revoked_after_rotation(self):
        rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        for subject, token in self.old_tokens.items():
            with self.assertRaises(Denied):
                authenticate('Bearer ' + token, path=str(self.auth_path))

    def test_new_tokens_authenticate_with_unchanged_subject_tenant_roles(self):
        handoff = rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        new_tokens = json.loads(handoff.read_text())
        for subject, token in new_tokens.items():
            principal = authenticate('Bearer ' + token, path=str(self.auth_path))
            self.assertEqual((principal.subject, principal.tenant, frozenset(principal.roles)),
                              self.old_principals[subject])

    def test_cross_role_and_tenant_authorization_after_rotation(self):
        handoff = rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        new_tokens = json.loads(handoff.read_text())
        principals = {subject: authenticate('Bearer ' + token, path=str(self.auth_path))
                      for subject, token in new_tokens.items()}
        # Every rotated principal keeps the exact tenant it had before rotation.
        for principal in principals.values():
            self.assertEqual(principal.tenant, SANDBOX_TENANT)
        # A role a subject never had stays denied after rotation too --
        # rotation must not accidentally widen authorization.
        with self.assertRaises(Denied):
            principals['researcher'].require('operator')
        with self.assertRaises(Denied):
            principals['researcher'].require('reviewer')
        with self.assertRaises(Denied):
            principals['reviewer'].require('operator')
        with self.assertRaises(Denied):
            principals['reviewer'].require('builder')
        with self.assertRaises(Denied):
            principals['operator'].require('builder')
        # Roles each subject does hold still work.
        principals['researcher'].require('builder')
        principals['reviewer'].require('reviewer')
        principals['operator'].require('operator')
        principals['operator'].require('publisher')
        # Shared "reader" role survives rotation for everyone that had it.
        for subject in ('researcher', 'reviewer', 'operator'):
            principals[subject].require('reader')

    def test_identities_file_remains_owner_only_mode_after_rotation(self):
        rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        self.assertEqual(self.auth_path.stat().st_mode & 0o777, 0o600)

    def test_runtime_and_policy_untouched_by_rotation(self):
        marker = self.runtime_dir / 'marker.txt'
        marker.write_text('untouched')
        runtime_mtime_before = marker.stat().st_mtime_ns
        policy_before = self.policy_path.read_bytes()
        policy_mtime_before = self.policy_path.stat().st_mtime_ns

        rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))

        self.assertEqual(marker.stat().st_mtime_ns, runtime_mtime_before)
        self.assertEqual(self.policy_path.read_bytes(), policy_before)
        self.assertEqual(self.policy_path.stat().st_mtime_ns, policy_mtime_before)

    def test_refuses_to_rotate_mismatched_subject_set(self):
        bogus = Path(self.tmp.name) / 'bogus.json'
        bogus.write_bytes(self.auth_path.read_bytes())
        os.chmod(bogus, 0o600)
        with self.assertRaisesRegex(ValueError, 'Unexpected subject set'):
            rotate_identities.rotate(bogus, expected_subjects=['researcher'])

    def test_refuses_non_owner_only_file(self):
        os.chmod(self.auth_path, 0o644)
        try:
            with self.assertRaisesRegex(ValueError, 'owner-only'):
                rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        finally:
            os.chmod(self.auth_path, 0o600)

    def test_refuses_group_or_other_writable_parent_directory(self):
        os.chmod(self.auth_path.parent, 0o777)
        try:
            with self.assertRaisesRegex(ValueError, 'parent directory'):
                rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        finally:
            os.chmod(self.auth_path.parent, 0o700)

    def test_refuses_symlinked_parent_directory(self):
        real_dir = Path(self.tmp.name) / 'real-sandbox'
        real_dir.mkdir(mode=0o700)
        (real_dir / 'identities.json').write_bytes(self.auth_path.read_bytes())
        os.chmod(real_dir / 'identities.json', 0o600)
        link_dir = Path(self.tmp.name) / 'linked-sandbox'
        link_dir.symlink_to(real_dir, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            rotate_identities.rotate(link_dir / 'identities.json', expected_subjects=list(SANDBOX_ACTORS))

    def test_no_new_authentication_file_created_besides_sidecar(self):
        before = {p.name for p in self.auth_path.parent.iterdir()}
        handoff = rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        after = {p.name for p in self.auth_path.parent.iterdir()}
        self.assertEqual(after - before, {handoff.name})
        self.assertTrue(self.auth_path.exists())

    def test_sidecar_write_failure_leaves_identities_and_old_tokens_untouched(self):
        auth_bytes_before = self.auth_path.read_bytes()
        real_open = os.open

        def flaky_open(path, flags, mode=0o777):
            if 'reissued-tokens' in str(path):
                raise OSError('simulated disk-full writing the token sidecar')
            return real_open(path, flags, mode)

        with mock.patch('telecom.rotate_identities.os.open', side_effect=flaky_open):
            with self.assertRaises(OSError):
                rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))

        self.assertEqual(self.auth_path.read_bytes(), auth_bytes_before)
        sidecars = list(self.auth_path.parent.glob('reissued-tokens.*.json'))
        self.assertEqual(sidecars, [])
        for subject, token in self.old_tokens.items():
            principal = authenticate('Bearer ' + token, path=str(self.auth_path))
            self.assertEqual(set(principal.roles), set(SANDBOX_ACTORS[subject]))

    def test_existing_sidecar_never_overwritten(self):
        with mock.patch('telecom.rotate_identities.time.time', return_value=1234567890.0):
            handoff_path = self.auth_path.parent / 'reissued-tokens.1234567890.json'
            handoff_path.write_text('{"preexisting": "marker"}')
            os.chmod(handoff_path, 0o600)
            auth_bytes_before = self.auth_path.read_bytes()

            with self.assertRaises(FileExistsError):
                rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))

            self.assertEqual(handoff_path.read_text(), '{"preexisting": "marker"}')
            self.assertEqual(self.auth_path.read_bytes(), auth_bytes_before)
            for subject, token in self.old_tokens.items():
                authenticate('Bearer ' + token, path=str(self.auth_path))

    def test_identities_replacement_failure_leaves_old_tokens_valid_and_recoverable(self):
        auth_bytes_before = self.auth_path.read_bytes()
        with mock.patch('telecom.rotate_identities.atomic_write',
                         side_effect=OSError('simulated failure replacing identities.json')):
            with self.assertRaises(OSError):
                rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))

        # identities.json must be exactly as before -- the write never ran.
        self.assertEqual(self.auth_path.read_bytes(), auth_bytes_before)
        for subject, token in self.old_tokens.items():
            principal = authenticate('Bearer ' + token, path=str(self.auth_path))
            self.assertEqual(set(principal.roles), set(SANDBOX_ACTORS[subject]))

        # The sidecar from the step before the failure still holds the new
        # tokens -- nothing generated was lost, a retry can proceed.
        sidecars = list(self.auth_path.parent.glob('reissued-tokens.*.json'))
        self.assertEqual(len(sidecars), 1)
        new_tokens = json.loads(sidecars[0].read_text())
        self.assertEqual(set(new_tokens), {'researcher', 'reviewer', 'operator'})
        # Those tokens are not yet active, since identities.json was never
        # swapped -- confirms the failure window never activated them.
        for token in new_tokens.values():
            with self.assertRaises(Denied):
                authenticate('Bearer ' + token, path=str(self.auth_path))

    def test_script_never_reads_environment_for_auth_path(self):
        # rotate() must only ever operate on the auth_path its caller passed
        # explicitly -- never fall back to reading TELECOM_AUTH_FILE (or any
        # other variable) from the environment itself.
        source = Path(rotate_identities.__file__).read_text()
        self.assertNotIn('os.environ', source)
        self.assertNotIn('getenv', source)

    def test_rotation_unaffected_by_unset_auth_file_env_var(self):
        os.environ.pop('TELECOM_AUTH_FILE', None)
        handoff = rotate_identities.rotate(self.auth_path, expected_subjects=list(SANDBOX_ACTORS))
        new_tokens = json.loads(handoff.read_text())
        for subject, token in new_tokens.items():
            principal = authenticate('Bearer ' + token, path=str(self.auth_path))
            self.assertEqual(set(principal.roles), set(SANDBOX_ACTORS[subject]))


if __name__ == '__main__':
    unittest.main()
