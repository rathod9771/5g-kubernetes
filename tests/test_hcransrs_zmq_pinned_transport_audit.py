"""Pinned transport source audit contract tests, with synthetic C++ excerpts only."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"scripts/experiments/hcransrs_zmq_pinned_transport_audit.py"
spec=importlib.util.spec_from_file_location("hcransrs_zmq_pinned_transport_audit",P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def inputs():
    return {path:"\n".join(checks) for path,checks in m.ANCHORS.items()}

class SourceAudit(unittest.TestCase):
    def test_pinned_transport_observation_is_not_interoperability_pass(self):
        r=m.examine(inputs())
        self.assertEqual(r["h2"],"BLOCKED")
        self.assertEqual(r["h3_h4_h5_h6"],"NOT_RUN")
        self.assertEqual(r["decision"],"ZMQ_HANDSHAKE_SOURCE_OBSERVED_INTEROPERABILITY_BLOCKED")
        self.assertEqual(r["production_decision"],"NO_GO")
    def test_reject_missing_or_drifts(self):
        for path in m.BLOBS:
            with self.subTest(path=path):
                d=inputs();del d[path]
                with self.assertRaises(ValueError):m.examine(d)
                d=inputs();d[path]="missing source"
                with self.assertRaises(ValueError):m.examine(d)
    def test_pinned_file_hashes(self):
        self.assertEqual(len(m.BLOBS),3)
        self.assertTrue(all(len(v)==40 for v in m.BLOBS.values()))
        self.assertIn("REQ/REP",P.read_text())
if __name__=="__main__":unittest.main()
