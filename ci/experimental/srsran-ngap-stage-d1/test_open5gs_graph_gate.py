"""Offline-only validation: no native compilers, cgroups, mounts, or network."""
import hashlib
import os
import unittest
from unittest.mock import patch

from build_policy import Blocked
import open5gs_graph_gate as gate


SAMPLE = ('cc -c foo.c -o foo.o\n'
          '/usr/bin/meson --internal symbolextractor /work/ogs-build libfoo.so libfoo.so libfoo.so.p/symbols\n'
          'rm -f src/amf/libamf.a && gcc-ar csrDT src/amf/libamf.a src/amf/libamf.a.p/foo.c.o\n'
          'cc -o foo.so @lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5.rsp\n'
          'cc -o bar.so @lib/sbi/openapi/libogssbi-openapi.so.2.7.5.rsp\n')


class GraphGateTests(unittest.TestCase):
    def accepted_fixture(self, contents=SAMPLE):
        """Test the parser with a synthetic hash, never change production pins."""
        return (patch.object(gate, 'OPEN5GS_GRAPH_SHA256', hashlib.sha256(contents.encode()).hexdigest()),
                patch.object(gate, 'OPEN5GS_GRAPH_BYTES', len(contents.encode())),
                patch.object(gate, 'OPEN5GS_GRAPH_LINES', contents.count('\n')),
                patch.object(gate, 'OPEN5GS_GRAPH_TOOLS', {'cc': 3, 'meson': 1, 'archive': 1}))

    def test_published_pin_is_exact(self):
        self.assertEqual(gate.OPEN5GS_GRAPH_SHA256, '9f086ee1c67d2716a69b5ccf45379231897c29a059f2c51d2d3bc5d257e8fd6c')
        self.assertEqual((gate.OPEN5GS_GRAPH_BYTES, gate.OPEN5GS_GRAPH_LINES), (3873485, 2671))
        self.assertEqual(gate.OPEN5GS_GRAPH_TOOLS, {'cc': 2654, 'meson': 15, 'archive': 2})

    def test_pinned_synthetic_graph_passes(self):
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(): s.enter_context(x)
            self.assertTrue(gate.audit_open5gs_graph(SAMPLE))

    def test_mutation_rejected_even_when_length_unchanged(self):
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(): s.enter_context(x)
            with self.assertRaisesRegex(Blocked, 'UNREVIEWED_OPEN5GS_COMMAND_GRAPH'):
                gate.audit_open5gs_graph(SAMPLE.replace('foo.c', 'bar.c'))

    def test_missing_line_rejected(self):
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(): s.enter_context(x)
            with self.assertRaises(Blocked): gate.audit_open5gs_graph(SAMPLE[:-1])

    def test_bare_compiler_not_universally_allowed(self):
        with self.assertRaises(Blocked):
            gate.audit_open5gs_graph('cc -c outside_graph.c -o foo.o\n')

    def test_no_implicit_native_command(self):
        for command in ('/work/open5gs/src/amf/open5gs-amfd\n', 'curl https://example.invalid\n', 'python3 -c print(1)\n'):
            with self.subTest(command=command), self.assertRaises(Blocked):
                gate.audit_open5gs_graph(command)

    def test_tool_drift_rejected_even_with_alternate_pin(self):
        altered = SAMPLE.replace('cc -c foo.c', 'gcc -c foo.c')
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(altered): s.enter_context(x)
            with self.assertRaisesRegex(Blocked, 'UNREVIEWED_OPEN5GS_TOOL'):
                gate.audit_open5gs_graph(altered)

    def test_rsp_drift_rejected_even_with_alternate_pin(self):
        altered = SAMPLE.replace('libogssbi-openapi.so.2.7.5.rsp', 'other-build-file.rsp')
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(altered): s.enter_context(x)
            with self.assertRaisesRegex(Blocked, 'UNREVIEWED_OPEN5GS_TOOL_OR_RESPONSE_FILE_SET'):
                gate.audit_open5gs_graph(altered)

    def test_unreviewed_ar_chain_rejected_even_with_alternate_pin(self):
        altered = SAMPLE.replace('foo.c.o\n', 'foo.c.o && /usr/bin/touch /work/backdoor\n')
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(altered): s.enter_context(x)
            with self.assertRaisesRegex(Blocked, 'UNREVIEWED_OPEN5GS_ARCHIVE'):
                gate.audit_open5gs_graph(altered)

    def test_plugin_flag_rejected_even_with_alternate_pin(self):
        altered = SAMPLE.replace('cc -c foo.c', 'cc -fplugin=/work/evil.so -c foo.c')
        from contextlib import ExitStack
        with ExitStack() as s:
            for x in self.accepted_fixture(altered): s.enter_context(x)
            with self.assertRaisesRegex(Blocked, 'UNREVIEWED_OPEN5GS_COMPILER_FLAG'):
                gate.audit_open5gs_graph(altered)

    def test_invalid_type_rejected(self):
        for value in (None, 17, {}, b'cc', ''):
            with self.subTest(value=value), self.assertRaises(Blocked):
                gate.audit_open5gs_graph(value)

    def test_srsran_policy_remains_stricter(self):
        import build_policy
        with self.assertRaisesRegex(Blocked, 'UNTRUSTED_TOOL_PATH'):
            build_policy.audit_graph('cc -c synthetic.c -o synthetic.o')


if __name__ == '__main__':
    unittest.main()
