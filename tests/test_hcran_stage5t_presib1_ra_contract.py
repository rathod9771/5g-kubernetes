"""Stage5T source-only contracts. Fixtures are NOT live RFsim recovery evidence."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATCH=ROOT/"experiments/oai-ngap-2026w13/stage5t/patch_pre_sib1_ra_ordering.py"
spec=importlib.util.spec_from_file_location("stage5t_patch",PATCH)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

RRC_SIB1_CONTRACT = """
void nr_rrc_mac_sched_sib(module_id_t module_id, int sched_sib) {
  if (mac->state == UE_NOT_SYNC && mac->get_sib1)
    mac->state = UE_RECEIVING_SIB;
}
void nr_rrc_mac_config_req_sib1(void) {
  configure_common_BWP_ul(mac, bwp_id, &scc->uplinkConfigCommon->initialUplinkBWP);
  if (mac->state == UE_RECEIVING_SIB && can_start_ra)
    mac->state = UE_PERFORMING_RA;
}
"""

class Stage5TSourceContracts(unittest.TestCase):
    def setup_source(self, body):
        # Return a source folder containing only synthetic *source text*.
        root=Path(self.temp.name)
        p=root/"openair2/LAYER2/NR_MAC_UE"
        p.mkdir(parents=True,exist_ok=True)
        f=p/"config_ue.c"
        f.write_text(body)
        return root,f

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_fail_closed_if_pinned_mac_anchor_missing(self):
        root,_=self.setup_source(RRC_SIB1_CONTRACT)
        with self.assertRaises(RuntimeError):
            module.patch(root)

    def test_fail_closed_if_sib1_recovery_contract_missing(self):
        root,_=self.setup_source(module.ANCHOR)
        with self.assertRaises(RuntimeError):
            module.patch(root)

    def test_sib1_guard_is_before_ra_state_transition(self):
        root,source=self.setup_source(RRC_SIB1_CONTRACT+module.ANCHOR)
        module.patch(root)
        actual=source.read_text()
        self.assertEqual(actual.count("Stage5T: postpone MAC RA"),1)
        self.assertLess(actual.index("Stage5T: postpone MAC RA"),
                        actual.index("      reset_ra(mac, false);"))
        self.assertIn("mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL", actual)
        self.assertIn("ul_count=%d dl_count=%d",actual)
        self.assertIn("if (mac->state == UE_NOT_SYNC && mac->get_sib1)",actual)
        self.assertIn("if (mac->state == UE_RECEIVING_SIB && can_start_ra)",actual)
        self.assertNotIn("calloc(",module.NEW)
        self.assertNotIn("send_nas",module.NEW)
        self.assertNotIn("NR_MAC_RRC_CONFIG_RESET",module.NEW)

    def test_second_source_patch_application_rejected(self):
        root,_=self.setup_source(RRC_SIB1_CONTRACT+module.ANCHOR)
        module.patch(root)
        with self.assertRaises(RuntimeError):
            module.patch(root)

    def test_research_only_builder_has_all_existing_patches_and_new_file(self):
        b=(ROOT/"scripts/hcran_build_pinned_w45_ue_stage5t.sh").read_text()
        for required in ("stage5g/patch_oai_ue_cause9.py",
                         "stage5i/patch_oai_ue_t3517.py",
                         "stage5k/patch_ue_tun_renewal.py",
                         "stage5m/patch_ra_bwp_guard.py",
                         "stage5q/patch_mac_bwp0_liveness.py",
                         "stage5t/patch_pre_sib1_ra_ordering.py",
                         "openair2/LAYER2/NR_MAC_UE/config_ue.c",
                         "dfb296ab867050a7bde81b393fa31b757ada2c83"):
            self.assertIn(required,b)
        self.assertLess(b.index("stage5q/patch_mac_bwp0_liveness.py"),
                        b.index("stage5t/patch_pre_sib1_ra_ordering.py"))

    def test_workflow_remains_disposable_and_does_not_bypass_genuine_wire_gate(self):
        w=(ROOT/".github/workflows/hcran-stage5t-rrc-sib1-ra-ordering.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",w)
        self.assertIn("trial: [1, 2]",w)
        self.assertIn("hcran_build_pinned_w45_ue_stage5t.sh",w)
        self.assertIn("stage5s/run_protocol_trace.py",w)
        self.assertIn("stage5s/verify_three_cycle_recovery.py",w)
        self.assertIn("--cycles 3 --verify-gtpu",w)
        self.assertNotIn("runs-on: self-hosted",w)
        self.assertNotIn("helm upgrade",w)
        self.assertNotIn("kubectl apply",w)
        self.assertNotIn("osm ns-",w)
        self.assertNotIn("actions/download-artifact",w)

if __name__=="__main__":
    unittest.main()
