"""Stage5X synthetic source-contract tests; no synthetic radio PASS assertion."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"experiments/oai-ngap-2026w13/stage5x/analyze_f1c_amf_ingress.py"
TRACE=ROOT/"experiments/oai-ngap-2026w13/stage5s/run_protocol_trace.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5x-f1c-amf-ingress.yml"

def event(t,source,event,**metadata):
    return {"elapsed_seconds":t,"source":source,"event":event,**metadata}

class Stage5XContracts(unittest.TestCase):
    def load_analyzer(self):
        spec=importlib.util.spec_from_file_location("stage5x_analysis",SCRIPT)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fail_closed_boundary_for_absent_small_cu_rrc_and_amf_ingress(self):
        m=self.load_analyzer()
        events=[
            event(1,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1),
            event(2,"harness","milestone",stage="T33-AMF-process-restarted",status="PASS"),
            event(3,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
            event(4,"ue","nas_service_request"),
            event(4,"ue-small","nas_service_request"),
            event(5,"cu","cu_rrc_setup_complete",cell_id=3804001),
            event(5.1,"cu","cu_ul_nas",cell_id=3804001),
            event(5.2,"core","amf_initial_ue_cell",cell_id=0xe0101),
            event(6,"ue","nas_service_reject"),
            event(7,"ue","nas_pdu_accept"),
            event(9,"ue-small","nas_retries_exhausted"),
            event(10,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="FAIL")
        ]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            d={"schema":"hcran-stage5r-bounded-protocol-observation-v1",
               "events":events,"collection_limit_reached":False,"collection_issues":[],
               "category_counts":{"cu":{},"core":{},"ue":{},"ue-small":{}}}
            (p/"stage5r-protocol-timeline.json").write_text(json.dumps(d))
            self.assertEqual(m.build_report(p),0)
            out=json.loads((p/m.REPORT).read_text())
            self.assertEqual(out["status"],"DIAGNOSTIC_ONLY")
            self.assertEqual(out["cycles"][0]["genuine_recovery_status"],"FAIL")
            self.assertEqual(out["cycles"][0]["ue"]["ue"]["gap_classification"],"FRESH_PDU_OBSERVED")
            self.assertEqual(
                out["cycles"][0]["ue"]["ue-small"]["gap_classification"],
                "UE_SERVICE_REQUEST_BUT_NO_CU_RRCSETUPCOMPLETE_OBSERVED")
            self.assertFalse(out["production_authorized"])
            self.assertIn("not conclusive",out["no_delivery_inference"])

    def test_amf_ingress_is_not_inferred_from_cu_rrc_alone(self):
        m=self.load_analyzer()
        events=[
            event(0,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1),
            event(1,"harness","milestone",stage="T33-AMF-process-restarted",status="PASS"),
            event(2,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
            event(3,"ue-small","nas_service_request"),
            event(4,"cu","cu_rrc_setup_complete",cell_id=3804002),
            event(8,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="FAIL")
        ]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "events":events,"collection_limit_reached":False,"collection_issues":[],
                "category_counts":{"cu":{},"core":{}}}))
            m.build_report(p)
            out=json.loads((p/m.REPORT).read_text())
            self.assertEqual(out["cycles"][0]["ue"]["ue-small"]["gap_classification"],
                             "CU_RRC_OBSERVED_BUT_NO_AMF_INITIAL_UE_CELL_OBSERVED")

    def test_missing_core_stream_must_be_inconclusive(self):
        m=self.load_analyzer()
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "events":[event(0,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1),
                          event(1,"harness","milestone",stage="T33-AMF-process-restarted",status="PASS"),
                          event(2,"ue-small","nas_service_request")],
                "collection_limit_reached":False,"collection_issues":[],
                "category_counts":{"cu":{}}}))
            m.build_report(p)
            out=json.loads((p/m.REPORT).read_text())
            self.assertEqual(out["cycles"][0]["ue"]["ue-small"]["gap_classification"],
                             "INCONCLUSIVE_INCOMPLETE_LIVE_SOURCES")

    def test_privacy_preserving_bounded_live_cu_core_observer(self):
        s=TRACE.read_text()
        self.assertIn('for role in ("ue", "ue-small", "cu", "macro", "small", "core")',s)
        self.assertIn('"amf_initial_ue_cell"',s)
        self.assertIn('metadata["cell_id"] = int(m.group(1))',s)
        self.assertIn('metadata["cell_id"] = int(m.group(1), 16)',s)
        self.assertIn("MAX_EVENTS = 1800",s)
        self.assertIn("required_ue_events_truncated",s)
        self.assertNotIn('metadata["rnti"]',s)
        self.assertNotIn('metadata["imsi"]',s)

    def test_same_pinned_ue_and_no_production_deploy(self):
        w=WORKFLOW.read_text()
        for required in (
            "runs-on: ubuntu-24.04","trial: [1, 2, 3, 4]",
            "--cycles 3 --verify-gtpu",
            "hcran_build_pinned_w45_ue_stage5t.sh",
            "hcran_build_pinned_stage3_ngap_cu.sh",
            "stage5u/audit_repeatability.py",
            "stage5x/analyze_f1c_amf_ingress.py",
            "if: always()"):
            self.assertIn(required,w)
        for forbidden in ("runs-on: self-hosted","kubectl apply","helm upgrade","osm ns-"):
            self.assertNotIn(forbidden,w)

if __name__=="__main__":
    unittest.main()
