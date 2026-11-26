"""P4 evidence archive: creation refuses unsafe input, verification detects every change."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts/experiments/p4')]

import p4_archive as archive  # noqa: E402


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name)
        evidence = self.root / 'p4-run/evidence/teardown-check-20261022T141618Z'
        (evidence / 'teardown-check').mkdir(parents=True)
        files = {'teardown-check/ns-operations.json': json.dumps([{'id': 'a0eb4f65', 'type': 'instantiate'}]),
                 'teardown-check/other-ns.json': '[]'}
        for name, text in files.items():
            (evidence / name).write_text(text)
        (evidence / 'summary.json').write_text(json.dumps({'evidence_sha256': {
            name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}}))
        helm = self.root / 'p4-run/helm-fixtures'
        helm.mkdir()
        (helm / 'helm-list.json').write_text(json.dumps([{'name': 'p4kdu-cu', 'chart': 'p4kdu-cu-0.1.0'}]))
        k8s = self.root / 'p4-run/k8s-fixtures'
        k8s.mkdir()
        (k8s / 'namespace-metadata.json').write_text(json.dumps({'items': [
            {'kind': 'Secret', 'type': 'helm.sh/release.v1', 'metadata': {'name': 'sh.helm.release.v1.p4kdu-cu.v1'}}]}))
        self.sources = [self.root / 'p4-run/evidence', helm, k8s]
        self.output = self.root / 'archive/pre-rerun'

    def cleanup(self):
        for path in self.root.rglob('*'):
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IWUSR)
        self.temp.cleanup()

    def create(self):
        return archive.create(self.sources, self.output)

    def test_create_and_verify(self):
        manifest = self.create()
        self.assertEqual((manifest['files'], manifest['problems']), (5, []))
        self.assertEqual(archive.verify(self.output), {'files': 5, 'verified': True})
        self.assertFalse((self.output / 'INCOMPLETE').exists())
        self.assertFalse(os.access(self.output / 'SHA256SUMS', os.W_OK))
        sums = (self.output / 'SHA256SUMS').read_text()
        self.assertIn('files/k8s-fixtures/namespace-metadata.json', sums)
        source = self.root / 'p4-run/helm-fixtures/helm-list.json'
        self.assertTrue(os.access(source, os.W_OK))             # sources are never changed

    def test_existing_archive_is_never_overwritten(self):
        self.create()
        with self.assertRaisesRegex(archive.ArchiveError, 'already exists'):
            self.create()

    def test_verify_detects_changes(self):
        self.create()
        target = self.output / 'files/helm-fixtures/helm-list.json'
        cases = {'checksum mismatch': lambda: target.write_text('[]'),
                 'missing': lambda: target.unlink(),
                 'not listed': lambda: (self.output / 'files/helm-fixtures/extra.json').write_text('{}'),
                 'differs from summary.json': lambda: (
                     self.output / 'files/evidence/teardown-check-20261022T141618Z/teardown-check/other-ns.json'
                 ).write_text('[1]'),
                 'does not match SHA256SUMS': lambda: (self.output / 'MANIFEST.json').write_text(
                     json.dumps({'sha256sums': '0' * 64}))}
        for reason, change in cases.items():
            with self.subTest(reason):
                self.cleanup_output()
                self.create()
                for path in self.output.rglob('*'):
                    path.chmod(path.stat().st_mode | stat.S_IWUSR)
                self.output.chmod(self.output.stat().st_mode | stat.S_IWUSR)
                change()
                with self.assertRaisesRegex(archive.ArchiveError, reason):
                    archive.verify(self.output)

    def cleanup_output(self):
        if self.output.exists():
            for path in list(self.output.rglob('*')) + [self.output]:
                path.chmod(path.stat().st_mode | stat.S_IWUSR)
            import shutil
            shutil.rmtree(self.output)

    def test_summary_mismatch_in_sources_fails_creation(self):
        (self.root / 'p4-run/evidence/teardown-check-20261022T141618Z/teardown-check/other-ns.json').write_text('[2]')
        with self.assertRaisesRegex(archive.ArchiveError, 'not verified: .*differs from summary.json'):
            self.create()
        self.assertTrue((self.output / 'INCOMPLETE').exists())
        with self.assertRaisesRegex(archive.ArchiveError, 'did not finish'):
            archive.verify(self.output)

    def test_unsafe_input_is_refused_before_writing(self):
        helm = self.root / 'p4-run/helm-fixtures'
        cases = {
            'symbolic link': lambda: (helm / 'link.json').symlink_to(helm / 'helm-list.json'),
            'credentials or key material': lambda: (helm / 'osm-password').write_text('x'),
            'PEM key or certificate': lambda: (helm / 'note.txt').write_text('-----BEGIN CERTIFICATE-----\n'),
            'kubeconfig credential': lambda: (helm / 'cfg.yaml').write_text('client-key-data: abc'),
            'credential field': lambda: (helm / 'login.json').write_text('{"token": "abc"}'),
            'Kubernetes Secret data': lambda: (helm / 'secret.json').write_text(json.dumps(
                {'items': [{'kind': 'Secret', 'metadata': {'name': 'x'}, 'data': {'release': 'H4sI'}}]})),
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                for path in helm.iterdir():
                    if path.name != 'helm-list.json':
                        path.unlink()
                change()
                with self.assertRaisesRegex(archive.ArchiveError, reason):
                    self.create()
                self.assertFalse(self.output.exists())

    def test_missing_source_is_refused(self):
        self.sources.append(self.root / 'p4-run/absent')
        with self.assertRaisesRegex(archive.ArchiveError, 'not a directory'):
            self.create()
        self.assertFalse(self.output.exists())

    def test_cli_reports_failure_with_status_1(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            self.assertEqual(archive.main(['verify', str(self.root / 'nothing')]), 1)
        self.assertFalse(json.loads(out.getvalue())['ok'])
        args = ['create', '--output', str(self.output)] + [a for s in self.sources for a in ('--source', str(s))]
        with mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(archive.main(args), 0)
            self.assertEqual(archive.main(['verify', str(self.output)]), 0)


if __name__ == '__main__':
    unittest.main()
