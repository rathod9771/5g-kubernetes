"""Offline negative controls for H2 source-built OAI UE / srsRAN ZMQ research."""
import importlib.util
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/"scripts/experiments"
import sys
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location("hcransrs_real_ue_zmq_h2",BASE/"hcransrs_real_ue_zmq_h2.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class H2ZMQContracts(unittest.TestCase):
    def test_overlay_is_macro_only_and_does_not_change_other_config(self):
        cfg={"ru_sdr":{"device_args":"tx_port=tcp://127.0.0.1:2000,rx_port=tcp://127.0.0.1:2001"}}
        self.assertEqual(m.overlay_config(cfg,"macro")["ru_sdr"]["device_args"],
            "tx_port=tcp://0.0.0.0:2000,rx_port=tcp://172.29.78.1:2001")
        for role in ("cu","small"):
            other={"ru_sdr":{"device_args":"keep"}}
            self.assertIs(m.overlay_config(other,role),other)
            self.assertEqual(other["ru_sdr"]["device_args"],"keep")
    def test_unreviewed_macro_args_fail_closed(self):
        for s in ("tx_port=tcp://192.0.2.1:2000,rx_port=tcp://127.0.0.1:2001",""):
            with self.subTest(s=s),self.assertRaises(Exception):
                m.overlay_config({"ru_sdr":{"device_args":s}},"macro")
    def test_actual_oai_binary_command_uses_no_production_address(self):
        args=m.ue_command(Path("/tmp/pinned-oai-nrue"))
        joined=" ".join(args)
        self.assertEqual(args[0],"/tmp/pinned-oai-nrue")
        self.assertIn("--device.name oai_zmqdevif",joined)
        self.assertIn("tcp://172.29.78.30:2000",joined)
        self.assertIn("tcp://0.0.0.0:2001",joined)
        self.assertIn("208930100001100",joined)
        self.assertNotIn("kubectl",joined)
        self.assertNotIn("osm",joined)
        self.assertIn("NR_SIB1_DECODE",m.NOT_VALIDATED)
    def test_never_claims_protocol_from_large_tcp_payload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # No PCAP means no wire success, not a synthetic positive.
            evidence=m.wire_summary(Path(td)/"absent.pcap")
            self.assertEqual(evidence["wire_evidence"],"UNQUALIFIED_OR_ONE_WAY_ZMQ_TCP_PAYLOAD")
            self.assertEqual(evidence["actual_du_to_ue_large_tcp_payload_frames"],0)
            self.assertEqual(evidence["actual_ue_to_du_large_tcp_payload_frames"],0)
if __name__=="__main__":unittest.main()
