"""Stage5V conservative observation accounting & phase gap contracts (synthetic only)."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
OBSERVER=ROOT/"experiments/oai-ngap-2026w13/stage5s/run_protocol_trace.py"
AUDIT=ROOT/"experiments/oai-ngap-2026w13/stage5u/audit_repeatability.py"
ANALYZER=ROOT/"experiments/oai-ngap-2026w13/stage5v/analyze_protocol_gap.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5v-phase-diagnostics.yml"

class Stage5V(unittest.TestCase):
    def test_observer_headers_are_sampled_but_genuine_overflows_fail_closed(self):
        src=OBSERVER.read_text()
        ast.parse(src)
        self.assertIn('if len(self.events) >= MAX_EVENTS:',src)
        self.assertIn('self.limit_reached = True  # a real bounded-observation gap',src)
        self.assertIn('if role == "wire-headers":',src)
        self.assertIn('self.header_samples_suppressed[category] += 1',src)
        self.assertIn('else:\n                    self.limit_reached = True',src)
        self.assertIn('"all_header_packets_counted": True',src)
        self.assertIn('"required_ue_events_truncated": self.limit_reached',src)

    def test_auditor_does_not_ignore_truncated_ue_events(self):
        src=AUDIT.read_text()
        ast.parse(src)
        self.assertIn('timeline.get("collection_limit_reached") is not False',src)
        self.assertIn('sampled.get("required_ue_events_truncated") is not False',src)
        self.assertIn('sampled.get("all_header_packets_counted") is not True',src)
        self.assertIn('len(report["negative_controls"])==len(mutations())',src)

    def test_phase_report_identifies_failed_ue_without_claiming_ngap_delivery(self):
        sp=importlib.util.spec_from_file_location("stage5v_phase",ANALYZER)
        mod=importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        def event(t,source,category,**extra):
            return {"elapsed_seconds":t,"source":source,"event":category,**extra}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            x={
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "collection_limit_reached":False,"collection_issues":[],
                "events":[
                    event(1,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1),
                    event(2,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
                    event(3,"ue","nas_service_request"),
                    event(4,"ue","nas_service_reject"),
                    event(5,"ue","nas_pdu_accept"),
                    event(3,"ue-small","nas_service_request"),
                    event(4,"ue-small","nas_t3517_timeout"),
                    event(5,"ue-small","nas_retries_exhausted"),
                    event(7,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="FAIL"),
                ]
            }
            (root/"stage5r-protocol-timeline.json").write_text(json.dumps(x))
            self.assertEqual(mod.analyze(root),0)
            y=json.loads((root/"stage5v-phase-gap-report.json").read_text())
            self.assertEqual(y["status"],"DIAGNOSTIC_ONLY")
            self.assertEqual(y["cycles"][0]["genuine_cycle_status"],"FAIL")
            self.assertEqual(y["cycles"][0]["ue"]["ue"]["nas_outcome"],"FRESH_PDU_ACCEPT_OBSERVED")
            self.assertEqual(y["cycles"][0]["ue"]["ue-small"]["nas_outcome"],"NO_SERVICE_REJECT_OBSERVED")
            self.assertEqual(y["production_decision"],"NO_GO")
            self.assertIn("NOT proof of NGAP",y["caution"])

    def test_workflow_source_pinned_diagnostic_only(self):
        workflow=WORKFLOW.read_text()
        self.assertIn("trial: [1, 2]",workflow)
        self.assertIn("runs-on: ubuntu-24.04",workflow)
        self.assertIn("hcran_build_pinned_w45_ue_stage5t.sh",workflow)
        self.assertIn("stage5u/audit_repeatability.py",workflow)
        self.assertIn("stage5v/analyze_protocol_gap.py",workflow)
        self.assertIn("if: always()",workflow)
        self.assertIn("--cycles 3 --verify-gtpu",workflow)
        for forbidden in ("runs-on: self-hosted","kubectl apply","helm upgrade","osm ns-"):
            self.assertNotIn(forbidden,workflow)

if __name__=="__main__":
    unittest.main()
