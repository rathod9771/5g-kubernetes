"""Stage5ZB synthetic diagnostic tests. Fixtures never establish live acceptance."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RADIO=ROOT/"experiments/oai-ngap-2026w13/stage5s/rrc_radio_boundary_stage5zb.py"
ANALYZER=ROOT/"experiments/oai-ngap-2026w13/stage5zb/analyze_rrc_radio_boundary.py"
OBS=ROOT/"experiments/oai-ngap-2026w13/stage5s/run_protocol_trace.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class Stage5ZBContracts(unittest.TestCase):
    def test_radio_categories_distinguish_buffered_from_generated(self):
        m=module(RADIO,"stage5zb_radio")
        def contains(role,line,cat):
            return cat in m.classify(role,line)
        self.assertTrue(contains("ue","Initial NAS UL: no SRB yet; buffered 44 B for RRCSetupComplete dedicatedNAS (RRC state=0)","ue_rrc_nas_buffered"))
        self.assertTrue(contains("ue","Triggering MAC RA for RRCSetupComplete pending NAS (prev_trigger=1)","ue_rrc_mac_ra_triggered"))
        self.assertTrue(contains("ue","RNTI 0xaaa Using pending initial NAS message for RRCSetupComplete","ue_rrc_nas_pending_used"))
        self.assertTrue(contains("ue","Logical Channel UL-DCCH (SRB1), Generating RRCSetupComplete (bytes39)","ue_rrc_setup_complete_generated"))
        self.assertTrue(contains("ue-small","RA-Msg3 transmitted","ue_mac_msg3_transmitted"))
        self.assertTrue(contains("ue","4-Step RA procedure succeeded. CBRA: Contention Resolution is successful","ue_mac_ra_succeeded"))
        self.assertTrue(contains("macro","Received Ack of Msg4. CBRA procedure succeeded (UE Connected)","du_mac_msg4_ack"))
        self.assertTrue(contains("small","Msg3 scheduled at 1004.17 (1004.10 TDA 1)","du_mac_msg3_scheduled"))
        self.assertEqual(m.classify("cu","bad"),())
        self.assertEqual(m.classify("ue","RRCSetupComplete pending"),())

    def test_failed_macro_without_generated_marker_is_not_pdu_recovery(self):
        m=module(ANALYZER,"stage5zb_an")
        def event(t,source,category,**kw):
            return {"elapsed_seconds":t,"source":source,"event":category,**kw}
        ev=[
            event(1,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1,status="OBSERVED"),
            event(2,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
            event(3,"ue","nas_service_request"),
            event(4,"ue","ue_rrc_nas_buffered"),
            event(5,"ue","ue_rrc_mac_ra_triggered"),
            event(6,"ue-small","ue_rrc_setup_complete_generated"),
            event(7,"cu","cu_rrc_setup_complete",cell_id=3804002),
            event(8,"core","amf_initial_ue_cell",cell_id=0xe0102),
            event(9,"ue-small","nas_pdu_accept"),
            event(10,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="FAIL")
        ]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "stage5zb_radio_boundary_categories":True,
                "collection_issues":[],"collection_limit_reached":False,
                "events":ev
            }))
            result=m.build(root)
            self.assertEqual(result["status"],"DIAGNOSTIC_ONLY")
            self.assertEqual(result["production_decision"],"NO_GO")
            self.assertEqual(result["cycles"][0]["genuine_recovery_status"],"FAIL")
            cells=result["cycles"][0]["post_ngsetup"]
            self.assertEqual(cells["macro"]["diagnostic_classification"],
                             "NAS_BUFFERED_WITHOUT_GENERATED_RRC_COMPLETE_MARKER")
            self.assertEqual(cells["small"]["diagnostic_classification"],"FRESH_PDU_OBSERVED")
            self.assertNotIn("IMSI",json.dumps(result))
    def test_missing_or_truncated_source_never_qualifies(self):
        m=module(ANALYZER,"stage5zb_missing")
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self.assertEqual(m.build(root)["status"],"INCOMPLETE_OBSERVATION")
            (root/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "stage5zb_radio_boundary_categories":True,
                "collection_issues":[{"error_type":"source_gap"}],
                "collection_limit_reached":False,"events":[]
            }))
            obj=m.build(root)
            self.assertEqual(obj["status"],"INCOMPLETE_OBSERVATION")
            self.assertIs(obj["production_authorized"],False)
    def test_workflow_keeps_real_stage5p_and_stage5y_gates(self):
        w=WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:",w)
        for part in ("--cycles 3 --verify-gtpu",
                     "stage5u/audit_repeatability.py",
                     "stage5y/verify_live_amf_ingress.py",
                     "stage5zb/analyze_rrc_radio_boundary.py"):
            self.assertIn(part,w)
        self.assertNotIn("runs-on: self-hosted",w)
        self.assertIn("classify_radio_boundary",OBS.read_text())

if __name__=="__main__":unittest.main()
