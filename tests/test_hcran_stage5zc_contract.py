"""Stage5ZC SIB1/BWP0 observer contracts; synthetic evidence never proves RFsim."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HERE=ROOT/"experiments/oai-ngap-2026w13"
PARSER=HERE/"stage5s/sib1_reacquisition_stage5zc.py"
ANALYZER=HERE/"stage5zc/analyze_sib1_reacquisition.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"
COLLECTOR=HERE/"stage5s/run_protocol_trace.py"

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Stage5ZCContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser=load(PARSER,"stage5zc_parser")
        cls.analyzer=load(ANALYZER,"stage5zc_analyzer")

    def test_ue_deferral_is_distinct_from_phy_sync_and_sib1(self):
        m=self.parser
        line="Stage5T: postpone MAC RA until SIB1 config restores BWP0 (mac_state=0 ul_count=0 dl_count=0)"
        self.assertEqual(m.classify("ue",line),(
            ("ue_ra_deferred_until_sib1",
             {"mac_state":0,"ul_bwp_count":0,"dl_bwp_count":0}),))
        self.assertEqual(m.classify("ue","Initial sync successful, PCI: 101"),(
            ("ue_phy_initial_sync_success",{}),))
        self.assertEqual(m.classify("ue","PRACH scheduler: Selected RO Frame 893, Slot 19"),(
            ("ue_prach_scheduled",{}),))
        self.assertEqual(m.classify("macro","PRACH job is in the past"),(
            ("du_prach_job_in_past_warning",{}),))
        self.assertEqual(m.classify("cu","Stage5T: postpone MAC RA until SIB1 config restores BWP0"),())
        self.assertNotIn("RNTI",repr(m.classify("ue","RNTI 0x1234 "+line)))

    def test_no_sib1_before_nas_timeout_reports_diagnostic_only(self):
        a=self.analyzer
        def event(t,src,cat,**kwargs):
            return {"elapsed_seconds":t,"source":src,"event":cat,**kwargs}
        def milestone(t,name,status,**kwargs):
            return event(t,"harness","milestone",stage=name,status=status,**kwargs)
        ev=[milestone(1,"STAGE5P-CYCLE-START","OBSERVED",cycle=1),
            milestone(2,"T34-new-NGSetupResponse-after-AMF-restart","PASS"),
            event(3,"ue","ue_rrc_mac_ra_triggered"),
            event(4,"ue","ue_ra_deferred_until_sib1",
                  mac_state=0,ul_bwp_count=0,dl_bwp_count=0),
            event(5,"ue","ue_phy_initial_sync_success"),
            event(16,"ue","nas_t3517_timeout"),
            event(16.1,"ue","nas_retries_exhausted"),
            event(5,"ue-small","rrc_sib1_decoded"),
            event(6,"ue-small","ue_prach_scheduled"),
            event(7,"ue-small","ue_mac_msg3_transmitted"),
            event(8,"ue-small","nas_pdu_accept"),
            event(8.5,"cu","cu_rrc_setup_complete",cell_id=3804002),
            event(9,"core","amf_initial_ue_cell",cell_id=0xe0102),
            milestone(19,"STAGE5P-CYCLE","FAIL",cycle=1)]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "stage5zb_radio_boundary_categories":True,
                "stage5zc_sib1_reacquisition_categories":True,
                "collection_limit_reached":False,"collection_issues":[],
                "events":ev}))
            r=a.diagnose(root)
            self.assertEqual(r["status"],"DIAGNOSTIC_ONLY")
            self.assertFalse(r["production_authorized"])
            c=r["cycles"][0]
            self.assertEqual(c["genuine_recovery_status"],"FAIL")
            self.assertEqual(c["post_ngsetup_cells"]["macro"]["diagnostic_boundary"],
                 "DEFERRED_RA_NO_NEW_SIB1_BEFORE_NAS_TIMEOUT")
            self.assertEqual(c["post_ngsetup_cells"]["small"]["diagnostic_boundary"],
                 "FRESH_PDU_OBSERVED")
            self.assertEqual(c["post_ngsetup_cells"]["macro"]["deferred_bwp_numeric_snapshots"][0]["ul_bwp_count"],0)
            self.assertNotIn("RNTI",json.dumps(r))

    def test_missing_source_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            result=self.analyzer.diagnose(td)
            self.assertEqual(result["status"],"INCOMPLETE_OBSERVATION")
            self.assertEqual(result["production_decision"],"NO_GO")

    def test_original_gates_remain_unchanged(self):
        s=WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:",s)
        for required in ("--cycles 3 --verify-gtpu",
                         "stage5u/audit_repeatability.py",
                         "stage5y/verify_live_amf_ingress.py",
                         "stage5zc/analyze_sib1_reacquisition.py"):
            self.assertIn(required,s)
        self.assertNotIn("runs-on: self-hosted",s)
        src=COLLECTOR.read_text()
        self.assertIn("classify_sib1_reacquisition",src)
        self.assertIn("actual = probe.fault_and_recover",src)

if __name__=="__main__":
    unittest.main()
