"""Stage5Q isolated source-patch safety contracts (no builds, no cluster)."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATCH=ROOT/"experiments/oai-ngap-2026w13/stage5q/patch_mac_bwp0_liveness.py"
spec=importlib.util.spec_from_file_location("stage5q_patch",PATCH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

DUMMY = """bool init_RA(NR_UE_MAC_INST_t *mac)
{
  /* Stage5M: prerequisite guard. */
  if (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL) {
    LOG_W(NR_MAC, "Stage5M: defer random access until UL/DL BWP configuration is ready\\n");
    return false;
  }
  RA_config_t *ra = &mac->ra;
}
"""

class Stage5QContracts(unittest.TestCase):
    def test_fail_closed_on_wrong_source(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"openair2/LAYER2/NR_MAC_UE"
            p.mkdir(parents=True)
            (p/"nr_ra_procedures.c").write_text("void unrelated(void) {}\n")
            with self.assertRaises(RuntimeError):
                mod.patch(Path(temp))

    def test_can_only_rebind_existing_configured_bwp0(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            p=root/"openair2/LAYER2/NR_MAC_UE"
            p.mkdir(parents=True)
            file=p/"nr_ra_procedures.c"
            file.write_text(DUMMY)
            mod.patch(root)
            result=file.read_text()
            self.assertIn("get_ul_bwp_structure(mac, 0, false)",result)
            self.assertIn("get_dl_bwp_structure(mac, 0, false)",result)
            self.assertIn("mac->mib && ul0 && dl0 && ul0->rach_ConfigCommon",result)
            self.assertIn("return false;",result)
            self.assertIn("Stage5M: defer random access",result)
            self.assertIn("count & (count - 1)",result)
            self.assertNotIn("calloc(",result)
            with self.assertRaises(RuntimeError):
                mod.patch(root)

    def test_workflow_is_isolated_and_requires_live_gtpu(self):
        w=(ROOT/".github/workflows/hcran-stage5q-bwp0-liveness.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",w)
        self.assertNotIn("runs-on: self-hosted",w)
        self.assertIn("run_three_cycle_recovery.py",w)
        self.assertIn("--cycles 3 --verify-gtpu",w)
        self.assertIn("verify_three_cycle_recovery.py",w)
        self.assertIn("stage5q/patch_mac_bwp0_liveness.py",w)
        self.assertNotIn("helm upgrade",w)
        self.assertNotIn("kubectl apply",w)
        self.assertNotIn("osm ns-",w)

if __name__=="__main__":
    unittest.main()
