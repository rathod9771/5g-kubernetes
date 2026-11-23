"""Exercise actual disposable ZeroMQ sockets; not a real 5G UE."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"scripts/experiments/hcransrs_zmq_socket_smoke.py"
spec=importlib.util.spec_from_file_location("hcransrs_zmq_socket_smoke",P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ZMQTransport(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('zmq') is not None,
                         'pyzmq is installed only in isolated ZMQ socket job')
    def test_real_loopback_two_independent_cells(self):
        r=m.smoke()
        self.assertEqual(r["status"],"INDEPENDENT_PYTHON_ZMQ_PEER_TRANSPORT_PASS")
        self.assertEqual(r["h2_ue_interoperability"],"NOT_VALIDATED")
        self.assertEqual(r["production_decision"],"NO_GO")
        self.assertEqual({x["cell"] for x in r["cells"]},{"macro","small"})
        for x in r["cells"]:
            self.assertEqual(x["ul_frames"],m.FRAMES)
            self.assertEqual(x["dl_frames"],m.FRAMES)
    def test_binary_iq_round_trip_and_distinct_direction(self):
        payload=m.iq(1,"macro","dl")
        self.assertEqual(len(payload),m.SAMPLES*8)
        self.assertNotEqual(payload,m.iq(1,"macro","ul"))
        self.assertNotEqual(payload,m.iq(1,"small","dl"))
        self.assertNotEqual(payload,m.iq(2,"macro","dl"))
        first=struct.unpack("<ff",payload[:8])
        self.assertAlmostEqual(first[0],0.001,places=6)
        self.assertAlmostEqual(first[1],-0.001,places=6)
    def test_no_protocol_overclaim(self):
        src=P.read_text()
        self.assertIn("No real srsRAN binary",src)
        self.assertIn("No real OAI UE binary",src)
        self.assertNotIn("H2_RRC_PASS",src)
if __name__=="__main__":
    unittest.main()
