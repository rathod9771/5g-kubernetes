"""Purely offline Ninja response-file evidence collection tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from build_policy import Blocked
from open5gs_rsp_review import TARGETS, collect_rsp_review


def ninja_manifest():
    # A synthetic Meson/Ninja manifest with two response-file link edges.
    return ('rule c_LINKER_RSP\n'
            ' command = cc @$out.rsp\n'
            ' rspfile = $out.rsp\n'
            ' rspfile_content = $ARGS\n'
            ' description = Linking $out\n'
            '\n'
            'build lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5: c_LINKER_RSP a.o b.o\n'
            ' ARGS = -shared -o lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5 a.o b.o\n'
            'build lib/sbi/openapi/libogssbi-openapi.so.2.7.5: c_LINKER_RSP c.o\n'
            ' ARGS = -shared -o lib/sbi/openapi/libogssbi-openapi.so.2.7.5 c.o\n')


class RspReviewTests(unittest.TestCase):
    def fixture(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / 'build.ninja').write_text(ninja_manifest())
        return tmp, root

    def test_collect_manifest_without_rsp_is_review_required(self):
        tmp, root = self.fixture()
        with tmp:
            report = collect_rsp_review(root)
            self.assertEqual(report['classification'], 'REVIEW_REQUIRED')
            self.assertFalse(report['native_compilation_authorized'])
            self.assertFalse(report['response_files_complete'])
            self.assertEqual(set(report['targets']), set(TARGETS))
            self.assertTrue(all(x['response_file_state'] == 'NOT_MATERIALIZED' for x in report['targets'].values()))
            self.assertEqual(report['build_ninja_sha256'], hashlib.sha256(ninja_manifest().encode()).hexdigest())
            json.dumps(report)

    def test_read_both_materialized_rsp_exactly(self):
        tmp, root = self.fixture()
        with tmp:
            for i, path in enumerate(TARGETS.values()):
                dst = root / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(f'-shared -o /work/lib{i}.so /work/object{i}.o\n')
            report = collect_rsp_review(root)
            self.assertTrue(report['response_files_complete'])
            self.assertFalse(report['native_compilation_authorized'])
            for row in report['targets'].values():
                self.assertEqual(row['response_file_state'], 'PRESENT_UNREVIEWED')
                self.assertEqual(row['response_file_sha256'], hashlib.sha256(row['response_file_text'].encode()).hexdigest())

    def test_no_manifest_is_blocked(self):
        tmp, root = self.fixture()
        with tmp:
            (root / 'build.ninja').unlink()
            with self.assertRaisesRegex(Blocked, 'MANIFEST_MISSING'):
                collect_rsp_review(root)

    def test_missing_target_is_blocked(self):
        tmp, root = self.fixture()
        with tmp:
            data = ninja_manifest().replace('build lib/sbi/openapi/libogssbi-openapi.so.2.7.5:', 'build different.so:')
            (root / 'build.ninja').write_text(data)
            with self.assertRaisesRegex(Blocked, 'TARGET_DEFINITION_MISSING'):
                collect_rsp_review(root)

    def test_missing_rsp_rule_blocks(self):
        tmp, root = self.fixture()
        with tmp:
            (root / 'build.ninja').write_text(ninja_manifest().replace(' rspfile_content = $ARGS\n', ' other = $ARGS\n'))
            with self.assertRaisesRegex(Blocked, 'RULE_NOT_REVIEWABLE'):
                collect_rsp_review(root)

    def test_duplicate_target_is_blocked(self):
        tmp, root = self.fixture()
        with tmp:
            (root / 'build.ninja').write_text(ninja_manifest() + 'build lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5: c_LINKER_RSP a.o\n')
            with self.assertRaisesRegex(Blocked, 'DUPLICATE_TARGET'):
                collect_rsp_review(root)

    def test_rsp_symlink_rejected(self):
        tmp, root = self.fixture()
        with tmp:
            dst = root / next(iter(TARGETS.values()))
            dst.parent.mkdir(parents=True)
            dst.symlink_to('/etc/passwd')
            with self.assertRaises(Blocked):
                collect_rsp_review(root)

    def test_rsp_parent_symlink_rejected(self):
        tmp, root = self.fixture()
        with tmp:
            (root / 'lib').symlink_to('/tmp')
            with self.assertRaises(Blocked):
                collect_rsp_review(root)

    def test_manifest_symlink_rejected(self):
        tmp, root = self.fixture()
        with tmp:
            (root / 'build.ninja').unlink()
            (root / 'build.ninja').symlink_to('/etc/passwd')
            with self.assertRaises(Blocked):
                collect_rsp_review(root)

    def test_manifest_oversized_rejected(self):
        from unittest.mock import patch
        tmp, root = self.fixture()
        with tmp, patch('open5gs_rsp_review.MANIFEST_LIMIT', 100):
            with self.assertRaises(Blocked):
                collect_rsp_review(root)

    def test_rsp_oversized_rejected(self):
        from unittest.mock import patch
        tmp, root = self.fixture()
        with tmp, patch('open5gs_rsp_review.RSP_LIMIT', 8):
            dst = root / next(iter(TARGETS.values()))
            dst.parent.mkdir(parents=True)
            dst.write_text('0123456789abcdef')
            with self.assertRaises(Blocked):
                collect_rsp_review(root)

    def test_rsp_non_utf8_rejected(self):
        tmp, root = self.fixture()
        with tmp:
            dst = root / next(iter(TARGETS.values()))
            dst.parent.mkdir(parents=True)
            dst.write_bytes(b'\xff')
            with self.assertRaisesRegex(Blocked, 'UNSAFE_RESPONSE_BYTES'):
                collect_rsp_review(root)

    def test_manifest_truncated_utf8_rejected(self):
        tmp, root = self.fixture()
        with tmp:
            (root / 'build.ninja').write_bytes(ninja_manifest().encode() + b'\xff')
            with self.assertRaisesRegex(Blocked, 'MANIFEST_ENCODING'):
                collect_rsp_review(root)

    def test_no_shell_or_compiler_execution(self):
        import inspect
        import open5gs_rsp_review
        src = inspect.getsource(open5gs_rsp_review)
        self.assertNotIn('subprocess', src.split('import hashlib', 1)[1])
        self.assertNotIn('os.system(', src)
        self.assertNotIn('os.popen(', src)


if __name__ == '__main__':
    unittest.main()
