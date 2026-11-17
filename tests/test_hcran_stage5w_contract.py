"""Stage5W pre-NGSetup boundary contract, source-only negative-case tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"experiments/oai-ngap-2026w13/stage5w/analyze_protocol_gap.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5w-multisession-phase-audit.yml"

class Stage5WContracts(unittest.TestCase):
    def test_ue_nas_observed_just_before_ngsetup_not_silently_dropped(self):
        spec=importlib.util.spec_from_file_location("stage5w_phase",SCRIPT)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        def ev(t,role,event,**extra):
            return {"elapsed_seconds":t,"source":role,"event":event,**extra}
        trace={
            "schema":"hcran-stage5r-bounded-protocol-observation-v1",
            "events":[
                ev(10,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1),
                ev(31.0,"harness","milestone",stage="T33-AMF-process-restarted",status="PASS"),
                ev(32.9,"ue","nas_service_request"),
                ev(33.0,"ue","nas_service_reject"),
                ev(33.01,"ue","nas_pdu_accept"),
                ev(33.05,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
                ev(33.10,"ue-small","nas_service_request"),
                ev(33.15,"ue-small","nas_service_reject"),
                ev(33.17,"ue-small","nas_pdu_accept"),
                ev(41,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="PASS")
            ],
            "collection_issues":[],"collection_limit_reached":False
        }
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/"stage5r-protocol-timeline.json").write_text(json.dumps(trace))
            self.assertEqual(mod.analyze(p),0)
            report=json.loads((p/"stage5v-phase-gap-report.json").read_text())
            self.assertEqual(report["status"],"DIAGNOSTIC_ONLY")
            r=report["cycles"][0]
            self.assertTrue(r["amf_restart_observed"])
            self.assertTrue(r["amf_ngsetup_restored"])
            self.assertEqual(r["ue"]["ue"]["nas_outcome"],"FRESH_PDU_ACCEPT_OBSERVED")
            self.assertEqual(r["ue"]["ue"]["before_ngsetup_observation"]["nas_pdu_accept"],1)
            self.assertEqual(r["ue"]["ue"]["after_ngsetup_observation"].get("nas_pdu_accept",0),0)
            self.assertEqual(r["ue"]["ue-small"]["after_ngsetup_observation"]["nas_pdu_accept"],1)
            self.assertIn("do NOT prove AMF ingress",report["caution"])

    def test_observation_is_not_taken_before_amf_restart(self):
        spec=importlib.util.spec_from_file_location("stage5w_pre",SCRIPT)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        trace={"schema":"hcran-stage5r-bounded-protocol-observation-v1","events":[
            {"elapsed_seconds":1,"source":"harness","event":"milestone","stage":"STAGE5P-CYCLE-START","cycle":1},
            {"elapsed_seconds":2,"source":"ue","event":"nas_pdu_accept"},
            {"elapsed_seconds":4,"source":"harness","event":"milestone","stage":"T33-AMF-process-restarted","status":"PASS"},
            {"elapsed_seconds":6,"source":"harness","event":"milestone","stage":"T34-new-NGSetupResponse-after-AMF-restart","status":"PASS"},
            {"elapsed_seconds":8,"source":"harness","event":"milestone","stage":"STAGE5P-CYCLE","cycle":1,"status":"FAIL"}
        ]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/"stage5r-protocol-timeline.json").write_text(json.dumps(trace))
            mod.analyze(p)
            result=json.loads((p/"stage5v-phase-gap-report.json").read_text())
            self.assertEqual(result["cycles"][0]["ue"]["ue"]["nas_pdu_accept"],0)
            self.assertEqual(result["cycles"][0]["genuine_cycle_status"],"FAIL")
            self.assertFalse(result["production_authorized"])

    def test_four_independent_three_cycle_real_wire_and_negative_proofs(self):
        w=WORKFLOW.read_text()
        for p in ("runs-on: ubuntu-24.04",
                  "trial: [1, 2, 3, 4]",
                  "max-parallel: 2",
                  "--cycles 3 --verify-gtpu",
                  "hcran_build_pinned_w45_ue_stage5t.sh",
                  "stage5s/verify_three_cycle_recovery.py",
                  "stage5u/audit_repeatability.py",
                  "stage5w/analyze_protocol_gap.py"):
            self.assertIn(p,w)
        for forbidden in ("runs-on: self-hosted","kubectl apply","helm upgrade","osm ns-"):
            self.assertNotIn(forbidden,w)

if __name__=="__main__":
    unittest.main()
