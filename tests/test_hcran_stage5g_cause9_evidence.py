"""Adversarial Stage5G evidence validator synthetic tests, never deployment proof."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"experiments/oai-ngap-2026w13/stage5g/verify_cause9_recovery.py"
spec=importlib.util.spec_from_file_location("stage5g_validator",SRC)
validator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


STEPS=validator.STEPS
EVENTS=("STAGE4O","STAGE5E","T31-AMF-actually-stopped",
        "T34-new-NGSetupResponse-after-AMF-restart",
        "T95-NGAP-and-F1C-after-UE-service")
DEFAULT={
  "amf_only_fault":True,"new_ngap_connection_observed":True,
  "ues_were_restarted":False,"f1_du_was_restarted":False,
  "externally_triggered_f1_handover":False,"rc_ric_required":False,
  "both_ue_icmp_recovered":True,"production_authorized":False,
  "production_decision":"NO_GO",
  "before_pdu_ips":{"ue":"10.45.0.2","ue-small":"10.45.0.3"},
  "after_pdu_ips":{"ue":"10.45.0.4","ue-small":"10.45.0.5"},
}

def write_fixture(folder):
    base=Path(folder)
    (base/"results.json").write_text(json.dumps(
        [{"stage":x,"status":"PASS"} for x in EVENTS]))
    (base/"stage5e-recovery-status.json").write_text(json.dumps(DEFAULT))
    for role in ("ue","ue-small"):
        (base/("cleanup-"+role+".log")).write_text("\n".join(STEPS)+"\n")
    (base/"gtpu.pcap").write_bytes(b"not a real pcap, structural fixture only"*12)
    (base/"post-AMF-gtpu-evidence.json").write_text(json.dumps(
        {"fixture":True}))
    return base


class Evidence(unittest.TestCase):
    def test_structural_positive_not_3gpp_conformance(self):
        with tempfile.TemporaryDirectory() as t:
            base=write_fixture(t)
            result,reasons=validator.check(base)
            self.assertTrue(result,reasons)
            self.assertEqual(DEFAULT["production_decision"],"NO_GO")

    def test_missing_or_tampered_evidence_fails(self):
        with tempfile.TemporaryDirectory() as t:
            base=write_fixture(t)
            tests={
              "gtpu.pcap": lambda p:p.unlink(),
              "post-AMF-gtpu-evidence.json": lambda p:p.unlink(),
              "cleanup-ue.log":lambda p:p.write_text("No cause #9\n"),
              "cleanup-ue-small.log":lambda p:p.write_text("\n".join(reversed(STEPS))),
              "stage5e-recovery-status.json":lambda p:p.write_text(json.dumps({**DEFAULT, "ues_were_restarted":True})),
              "results.json":lambda p:p.write_text(json.dumps(
                  [{"stage":x,"status":"PASS"} for x in EVENTS[:-1]])),
            }
            for filename,break_it in tests.items():
                with self.subTest(filename=filename):
                    write_fixture(t)
                    break_it(base/filename)
                    ok,reasons=validator.check(base)
                    self.assertFalse(ok,filename)
                    self.assertTrue(reasons)

    def test_stale_logs_cannot_count_as_cause9_recovery(self):
        with tempfile.TemporaryDirectory() as t:
            base=write_fixture(t)
            (base/"cleanup-ue.log").write_text(
                "Received Registration Accept\n"+
                "PDU Session Establishment Accept\n"+
                "UE_identity_cannot_be_derived_by_the_network\n"+
                "Stage5G: cause #9, cleared stale GUTI and NAS security\n")
            ok,reasons=validator.check(base)
            self.assertFalse(ok)
            self.assertTrue(any("cleanup" not in x for x in reasons))

if __name__=="__main__":
    unittest.main()
