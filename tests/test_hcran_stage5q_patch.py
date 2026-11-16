#!/usr/bin/env python3
"""Stage5Q negative/anchor tests on synthetic source fixtures; no radio claims."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATCH=ROOT/"experiments/oai-ngap-2026w13/stage5q/patch_keep_camped_t3517.py"
spec=importlib.util.spec_from_file_location("stage5q_patch",PATCH)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HEADER="static NR_UE_RRC_INST_t *NR_UE_rrc_inst[MAX_NUM_NR_UE_INST] = {0};\n"
ACTION=("case NAS_STAGE5I_FORCE_IDLE_REQ: {\n"
        "      nr_rrc_going_to_IDLE(rrc, OTHER, NULL);\n"
        "}\n")
PRE="  const bool preserve_camped_context = rrc->nrRrcState != RRC_STATE_DETACH_NR && release_cause == OTHER && RRCRelease\n"
POST="                                       && (!rrcReleaseIEs || !rrcReleaseIEs->redirectedCarrierInfo);"

class Tests(unittest.TestCase):
    def test_scope_and_idempotence_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rrc=root/"openair2/RRC/NR_UE/rrc_UE.c"
            rrc.parent.mkdir(parents=True)
            rrc.write_text(HEADER+ACTION+PRE+POST+"\n")
            module.patch(root)
            text=rrc.read_text()
            self.assertIn("stage5q_keep_camped_timeout",text)
            self.assertIn("valid_camped_sib1",text)
            self.assertIn("&& rrc->perNB[0].SInfo.sib1_validity",text)
            self.assertIn("GO_TO_IDLE", (ROOT/"docs/hcran-stage5q-camped-context-hypothesis.md").read_text())
            with self.assertRaises(RuntimeError):
                module.patch(root)

    def test_no_global_change_without_stage5i(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rrc=root/"openair2/RRC/NR_UE/rrc_UE.c"
            rrc.parent.mkdir(parents=True)
            rrc.write_text(HEADER+PRE+POST+"\n")
            with self.assertRaises(RuntimeError):
                module.patch(root)

    def test_isolated_workflow_not_target_cluster(self):
        w=(ROOT/".github/workflows/hcran-stage5q-keep-camped-t3517.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",w)
        self.assertIn("stage5p/verify_three_cycle_recovery.py",w)
        self.assertNotIn("self-hosted",w)
        self.assertNotIn("kubectl apply",w)

if __name__=="__main__":
    unittest.main()
