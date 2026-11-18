"""Stage5ZD source-pinned log-only MIB/SIB1 audit contracts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"experiments/oai-ngap-2026w13"
PATCH=BASE/"stage5zd/patch_mib_sib1_handoff_observe.py"
RULES=BASE/"stage5s/mib_sib1_handoff_stage5zd.py"
AUDIT=BASE/"stage5zd/analyze_mib_sib1_handoff.py"
BUILDER=ROOT/"scripts/hcran_build_pinned_w45_ue_stage5zd.sh"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class Stage5ZD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch=load(PATCH,"stage5zd_patch")
        cls.rules=load(RULES,"stage5zd_rules")
        cls.audit=load(AUDIT,"stage5zd_audit")

    def test_strict_three_instrumentation_calls_and_reapply_rejection(self):
        m=self.patch
        src="""void nr_rrc_mac_sched_sib(int i) {
"""+m.SCHED_ANCHOR+"""}
void nr_rrc_mac_config_req_mib(int i) {
"""+m.MIB_ANCHOR+"""}
void nr_rrc_mac_config_req_sib1(int i) {
"""+m.SIB1_ANCHOR+"""}
void nr_rrc_mac_config_other_sib(int i) {}
"""
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/m.PINNED
            p.parent.mkdir(parents=True)
            p.write_text(src)
            m.patch(tmp)
            changed=p.read_text()
            self.assertEqual(changed.count("Stage5ZD:"),3)
            self.assertIn(m.SCHED_ANCHOR,changed)
            self.assertIn(m.MIB_ANCHOR,changed)
            self.assertIn(m.SIB1_ANCHOR,changed)
            with self.assertRaises(RuntimeError):
                m.patch(tmp)
            p.write_text(src.replace(m.MIB_ANCHOR,""))
            with self.assertRaises(RuntimeError):
                m.patch(tmp)

    def test_redacted_categories_and_bounds(self):
        x=self.rules
        cases=[
          ("Stage5ZD: mib_configured state=0 mib_present=1 get_sib1=0 ul_bwp=0 dl_bwp=0",
           "ue_mib_configured"),
          ("Stage5ZD: sib1_schedule state=1 get_sib1=1 mib_present=1 ul_bwp=0 dl_bwp=0",
           "ue_sib1_scheduled"),
          ("Stage5ZD: sib1_configured state=2 ul_bwp=1 dl_bwp=1 current_ul=1 current_dl=1",
           "ue_sib1_mac_configured"),
          ("Initial sync: pbch decoded sucessfully, ssb index 0","ue_phy_pbch_decode"),
        ]
        for line,key in cases:
            with self.subTest(key=key):
                rows=x.classify("ue",line)
                self.assertEqual(rows[0][0],key)
                self.assertNotIn("RNTI",repr(rows))
        self.assertEqual(x.classify("core",cases[0][0]),())
        self.assertNotIn("RNTI",repr(x.classify("ue","RNTI secret "+cases[0][0])))

    def test_fail_during_mib_handoff_not_promotion(self):
        a=self.audit
        def e(t,source,event,**kwargs):
            return {"elapsed_seconds":t,"source":source,"event":event,**kwargs}
        source=[
            e(1,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1,status="OBSERVED"),
            e(2,"ue","ue_phy_pbch_decode"),
            e(2.2,"ue","ue_phy_initial_sync_success"),
            e(3,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
            e(4,"ue","ue_ra_deferred_until_sib1"),
            e(5,"ue-small","nas_pdu_accept"),
            e(6,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="FAIL"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "stage5zd_mib_sib1_handoff_categories":True,
                "collection_limit_reached":False,"collection_issues":[],
                "events":source}))
            result=a.build(p)
            self.assertEqual(result["status"],"DIAGNOSTIC_ONLY")
            self.assertEqual(result["cycles"][0]["genuine_recovery_status"],"FAIL")
            self.assertEqual(result["cycles"][0]["cells"]["macro"]["diagnostic_boundary"],
                             "PBCH_DECODE_NO_MAC_MIB_CONFIG_MARKER")
            self.assertFalse(result["production_authorized"])

    def test_genuine_gates_and_exact_pin_preserved(self):
        b=BUILDER.read_text()
        self.assertIn("dfb296ab867050a7bde81b393fa31b757ada2c83",b)
        self.assertIn("patch_pre_sib1_ra_ordering.py",b)
        self.assertIn("patch_mib_sib1_handoff_observe.py",b)
        self.assertIn("git -C \"$OAI_SOURCE\" diff --check",b)
        w=WORKFLOW.read_text()
        for required in (
            "experiment/hcran-stage5zd-mib-sib1-handoff-20261118",
            "hcran_build_pinned_w45_ue_stage5zd.sh",
            "--cycles 3 --verify-gtpu","stage5u/audit_repeatability.py",
            "stage5y/verify_live_amf_ingress.py",
            "stage5zd/analyze_mib_sib1_handoff.py",
        ):
            self.assertIn(required,w)
        self.assertNotIn("runs-on: self-hosted",w)

if __name__=="__main__":
    unittest.main()
