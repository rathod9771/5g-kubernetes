"""Stage5ZF: RRC SI-cache decision observation-only contracts and negative tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"experiments/oai-ngap-2026w13"
PATCH=BASE/"stage5zf/patch_rrc_si_decision_observe.py"
CLASS=BASE/"stage5s/rrc_si_decision_stage5zf.py"
ANALYZE=BASE/"stage5zf/analyze_rrc_si_cache.py"
BUILDER=ROOT/"scripts/experiments/hcran_build_pinned_w45_ue_stage5zf.sh"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class Stage5ZF(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=module(PATCH,"stage5zf_patch")
        cls.classify=module(CLASS,"stage5zf_classifier")
        cls.analyze=module(ANALYZE,"stage5zf_analyzer")

    def test_bounded_rerecording_no_protocol_mutation(self):
        p=self.p
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            f=root/p.RRC_FILE
            f.parent.mkdir(parents=True)
            f.write_text(
                "static void nr_rrc_ue_decode_NR_BCCH_BCH_Message(int x) {\n"
                "get_sib = check_si_status(SI_info);\n"
                "SI_info->sib_pending = true;\n"
                "SI_info->sib1_validity = false;\n"
                + p.RRC_OLD + "}\n")
            mac=root/p.MAC_FILE
            mac.parent.mkdir(parents=True)
            mac.write_text("void nr_rrc_mac_config_req_mib(int x) {\n"+p.MAC_OLD+"}\n")
            p.patch(root)
            self.assertEqual(f.read_text().count("Stage5ZF: si_decision"),1)
            self.assertEqual(mac.read_text().count("Stage5ZD: mib_configured"),1)
            self.assertIn("stage5zf_si_observations",f.read_text())
            self.assertIn("stage5zf_mac_mib_observations",mac.read_text())
            with self.assertRaises(RuntimeError):
                p.patch(root)
            mac.write_text("upstream changed")
            with self.assertRaises(RuntimeError):
                p.patch(root)

    def test_redaction_and_power_of_two_restriction(self):
        c=self.classify
        x=c.classify("ue",
          "IDENTITY NOT SAVED Stage5ZF: si_decision sample=128 sib1_valid=1 sib_pending=0 get_sib=0 rrc_state=0 barred=0")
        self.assertEqual(x[0][0],"ue_rrc_si_schedule_decision")
        self.assertEqual(x[0][1]["sib1_valid"],1)
        self.assertNotIn("IDENTITY",repr(x))
        y=c.classify("ue",
          "Stage5ZD: mib_configured sample=64 state=0 mib_present=1 get_sib1=0 ul_bwp=0 dl_bwp=0")
        self.assertEqual(y[0][0],"ue_mac_mib_sampled")
        self.assertEqual(c.classify("ue","Stage5ZF: si_decision sample=3 sib1_valid=1 sib_pending=0 get_sib=0 rrc_state=0 barred=0"),())
        self.assertEqual(c.classify("cu","Stage5ZF: si_decision sample=1 sib1_valid=1 sib_pending=0 get_sib=0 rrc_state=0 barred=0"),())

    def test_failure_remains_failure_and_diagnostic_not_proof(self):
        a=self.analyze
        def ev(t,source,event,**kwargs):
            return {"elapsed_seconds":t,"source":source,"event":event,**kwargs}
        markers=[
            ev(1,"harness","milestone",stage="STAGE5P-CYCLE-START",cycle=1,status="OBSERVED"),
            ev(2,"ue","ue_rrc_si_schedule_decision",
              sample_n=8,sib1_valid=1,sib_pending=0,get_sib=0,rrc_state=0,barred=0),
            ev(3,"ue","ue_mac_mib_sampled",
              sample_n=64,mac_state=0,mib_present=1,get_sib1=0,ul_bwp=0,dl_bwp=0),
            ev(4,"harness","milestone",stage="T34-new-NGSetupResponse-after-AMF-restart",status="PASS"),
            ev(5,"ue","ue_ra_deferred_until_sib1"),
            ev(6,"ue-small","nas_pdu_accept"),
            ev(7,"harness","milestone",stage="STAGE5P-CYCLE",cycle=1,status="FAIL"),
        ]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"stage5r-protocol-timeline.json").write_text(json.dumps({
                "schema":"hcran-stage5r-bounded-protocol-observation-v1",
                "stage5zf_rrc_si_schedule_decision_categories":True,
                "collection_limit_reached":False,"collection_issues":[],
                "events":markers}))
            d=a.build(root)
            self.assertEqual(d["status"],"DIAGNOSTIC_ONLY")
            self.assertEqual(d["cycles"][0]["genuine_recovery_status"],"FAIL")
            self.assertEqual(d["production_decision"],"NO_GO")
            self.assertFalse(d["production_authorized"])
            self.assertEqual(d["cycles"][0]["cells"]["macro"]["diagnostic_hypothesis"],
                "NON_TEMPORAL_SI_CACHE_AND_MISSING_BWP_COOCCURRENCE_INCONCLUSIVE")
            broken={**json.loads((root/"stage5r-protocol-timeline.json").read_text()),
                    "collection_limit_reached":True}
            (root/"stage5r-protocol-timeline.json").write_text(json.dumps(broken))
            self.assertEqual(a.build(root)["status"],"INCOMPLETE_OBSERVATION")

    def test_pinned_frozen_stack_and_real_ci_gates(self):
        b=BUILDER.read_text()
        self.assertIn("dfb296ab867050a7bde81b393fa31b757ada2c83",b)
        for x in ("patch_pre_sib1_ra_ordering.py","patch_mib_sib1_handoff_observe.py",
                  "patch_rrc_si_decision_observe.py",
                  "git -C \"$OAI_SOURCE\" diff --check"):
            self.assertIn(x,b)
        w=WORKFLOW.read_text()
        for x in ("experiment/hcran-stage5zf-rrc-si-cache-decision-20261119",
                  "hcran_build_pinned_w45_ue_stage5zf.sh",
                  "stage5zf/analyze_rrc_si_cache.py",
                  "--cycles 3 --verify-gtpu",
                  "stage5u/audit_repeatability.py",
                  "stage5y/verify_live_amf_ingress.py",
                  "hcran_build_pinned_w45_ue_stage5t.sh"):
            self.assertIn(x,w)
        self.assertNotIn("runs-on: self-hosted",w)

if __name__=="__main__":
    unittest.main()
