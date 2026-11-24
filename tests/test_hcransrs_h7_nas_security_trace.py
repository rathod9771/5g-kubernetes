"""H7 independent NAS 5GS security transition and registration negative controls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

p = Path(__file__).resolve().parents[1] / "scripts/experiments/hcransrs_h7_nas_security_trace.py"
spec = importlib.util.spec_from_file_location("hcransrs_h7_nas_security_trace", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

H6 = {"status": m.H6_PASS,
      "nas_wire": {"authentication_request_frames": [21],
                   "authentication_response_frames": [24]}}

def events(**kw):
    base = {"source": "INDEPENDENT_REAL_NGAP_WIRE",
            "known_frame_numbers": {k: [] for k in m.KNOWN.values()},
            "security_header_counts": {}}
    base.update(kw)
    return base

class H7PacketContracts(unittest.TestCase):
    def test_plain_and_protected_cleartext_without_cipher_guess(self):
        self.assertEqual(m.classify_nas_pdu("7e0057", "uplink")["class"], "AUTHENTICATION_RESPONSE")
        self.assertEqual(m.classify_nas_pdu("7e0355667788017e005d", "downlink")["class"], "SECURITY_MODE_COMMAND")
        self.assertEqual(m.classify_nas_pdu("7e0455667788017e005e", "uplink")["class"],
                         "PROTECTED_CIPHERED_NAS_TYPE_UNAVAILABLE")
        self.assertEqual(m.classify_nas_pdu("7e0355667788017e005d", "uplink")["class"],
                         "MESSAGE_DIRECTION_MISMATCH")
        self.assertEqual(m.classify_nas_pdu("7e005d", "downlink")["class"],
                         "UNQUALIFIED_SECURITY_MODE_COMMAND")
    def test_rejects_invalid_payloads_and_non5gs(self):
        for v in ("", "zzzz", "7e0", "7e00z1", "7e0", "7e00", "7e00123"):
            self.assertEqual(m.classify_nas_pdu(v, "uplink")["class"], "INVALID_OR_UNDECODABLE_NAS")
        self.assertEqual(m.classify_nas_pdu("070041", "uplink")["class"], "UNRELATED_OR_UNKNOWN_NAS")
        with self.assertRaises(m.H7EvidenceError):
            m.classify_nas_pdu("7e0057", "outside")
    def test_ordered_auth_and_smc_require_independently_visible_message(self):
        w = events()
        w["known_frame_numbers"]["SECURITY_MODE_COMMAND"] = [25]
        w["security_header_counts"]["uplink:4"] = 1
        self.assertEqual(m.qualify(H6, w), "NAS_SMC_AND_UPLINK_PROTECTED_TRAFFIC_ONLY")
        w["known_frame_numbers"]["SECURITY_MODE_COMPLETE"] = [26]
        self.assertEqual(m.qualify(H6, w),
                         "NAS_SECURITY_MODE_COMMAND_COMPLETE_HEADERS_CANDIDATE_ONLY")
        w["known_frame_numbers"]["REGISTRATION_REJECT"] = [30]
        self.assertEqual(m.qualify(H6, w), "BLOCKED_VISIBLE_NAS_REJECTION")
    def test_unqualified_or_missing_auth_never_passes(self):
        self.assertEqual(m.qualify({}, events()), "BLOCKED_H6_AUTHENTICATION")
        self.assertEqual(m.qualify(H6, {}), "BLOCKED_NO_REAL_NAS_WIRE")
        w = events()
        self.assertEqual(m.qualify(H6, w), "BLOCKED_SECURITY_MODE_COMMAND_WIRE")
    def test_real_wire_fields_but_no_nas_payload_in_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "setup.pcapng"
            p.write_bytes(b"x"*160)
            def fake(cmd):
                self.assertIn("ngap.NAS_PDU", cmd)
                expr = cmd[cmd.index("-Y")+1]
                if "ip.src == " + m.AMF in expr:
                    return "25\t7e0312345678017e005d\n"
                return "28\t7e04aabbccdd01" + "aa"*40 + "\n"
            out=m.decode_wire(p, fake)
            self.assertEqual(out["known_frame_numbers"]["SECURITY_MODE_COMMAND"], [25])
            self.assertEqual(out["security_header_counts"]["uplink:4"], 1)
            self.assertEqual(out["other_classes"].get("PROTECTED_CIPHERED_NAS_TYPE_UNAVAILABLE"), 1)
            self.assertNotIn("aabbccdd", str(out))
    def test_reject_invalid_tshark_fields(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "setup.pcapng"
            p.write_bytes(b"x"*160)
            with self.assertRaises(m.H7EvidenceError):
                m.decode_wire(p, lambda cmd: "9\t7e0057\tunexpected\n")
    def test_no_claim_registration_from_ciphered_outer_header(self):
        w=events()
        w["security_header_counts"]["downlink:2"]=1
        w["security_header_counts"]["uplink:4"]=1
        self.assertEqual(m.qualify(H6,w),"BLOCKED_SECURITY_MODE_COMMAND_WIRE")
        self.assertFalse(w.get("known_frame_numbers",{}).get("REGISTRATION_COMPLETE"))

if __name__=="__main__":
    unittest.main()
