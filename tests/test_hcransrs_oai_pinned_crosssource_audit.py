"""Cross-source source-anchor negative controls; fixtures do not run NR UE."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/"scripts/experiments/hcransrs_oai_pinned_crosssource_audit.py"
spec=importlib.util.spec_from_file_location("hcransrs_oai_pinned_crosssource_audit",PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def source_fixture():
    return {
        "oai":"\n".join((*m.OAI_TX,*m.OAI_RX,
              "const size_t sample_size = sizeof(cf_t);","num_samples_received")),
        "srsran_tx":"\n".join((*m.SRS_TX,"count * sizeof(cf_t)")),
        "srsran_rx":"\n".join((*m.SRS_RX,"int sample_size = sizeof(cf_t);")),
    }

class CrossSourceAudit(unittest.TestCase):
    def test_source_complement_not_ue_success(self):
        result=m.inspect(source_fixture())
        self.assertEqual(result["status"],"SOURCE_SOCKET_ROLES_AND_PAYLOAD_FRAMING_COMPATIBLE")
        self.assertEqual(result["protocol_validation"],"NOT_RUN")
        self.assertFalse(result["actual_oai_ue_connected"])
        self.assertFalse(result["actual_srsran_du_connected"])
        self.assertEqual(result["production_decision"],"NO_GO")
    def test_any_missing_source_rejected(self):
        for key in m.SOURCES:
            with self.subTest(key=key):
                x=source_fixture();x.pop(key)
                with self.assertRaises(ValueError):m.inspect(x)
    def test_socket_role_and_payload_changes_rejected(self):
        for key,marker in (("oai","ZMQ_REP"),("oai","ZMQ_REQ"),
                           ("srsran_tx","ZMQ_REP"),("srsran_rx","ZMQ_REQ"),
                           ("oai","sizeof(cf_t)"),("srsran_tx","sizeof(cf_t)")):
            with self.subTest(key=key,marker=marker):
                x=source_fixture();x[key]=x[key].replace(marker,"UNKNOWN")
                with self.assertRaises(ValueError):m.inspect(x)
    def test_source_pins_are_immutable_hex(self):
        self.assertEqual(len(m.SOURCES),3)
        for info in m.SOURCES.values():
            self.assertEqual(len(info["blob"]),40)
            self.assertEqual(len(info["commit"]),40)
            self.assertNotIn("develop",info["commit"])
if __name__=="__main__":
    unittest.main()
