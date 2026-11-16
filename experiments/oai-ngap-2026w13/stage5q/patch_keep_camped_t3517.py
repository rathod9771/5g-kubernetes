#!/usr/bin/env python3
"""Stage5Q TEST-ONLY: keep valid camped SIB1/BWP on forced NAS timeout idle.

Hypothesis under test: Stage5I's forced RRC_IDLE event selects GO_TO_IDLE,
reinitializing the MAC/PHY and discarding initial BWP0; unreliable SIB1
reacquisition can cause Stage5M's defer-RA guard to loop until NAS exhausts.
For the exact Stage5I FORCED-IDLE event ONLY, preserve the already-valid
camped-cell common BWP state via OAI's existing GO_TO_IDLE_KEEP_CAMPED
path. All ordinary release/detach/RLF paths are unchanged. Research only:
not a general TS 38.331 implementation, not deployable.
"""
from pathlib import Path
import argparse

def one(data, before, after, label):
    n = data.count(before)
    if n != 1:
        raise RuntimeError(f"FAIL CLOSED: {label}: expected one anchor, got {n}")
    return data.replace(before, after)

def patch(root):
    p = root / "openair2/RRC/NR_UE/rrc_UE.c"
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("FAIL CLOSED: pinned UE RRC source missing/unsafe")
    data = p.read_text()
    if "NAS_STAGE5I_FORCE_IDLE_REQ" not in data or "Stage5Q:" in data:
        raise RuntimeError("FAIL CLOSED: Stage5I not present or Stage5Q applied already")
    data = one(data,
        "static NR_UE_RRC_INST_t *NR_UE_rrc_inst[MAX_NUM_NR_UE_INST] = {0};\n",
        """static NR_UE_RRC_INST_t *NR_UE_rrc_inst[MAX_NUM_NR_UE_INST] = {0};
/* Stage5Q isolated experiment: private one-shot override scoped to an
 * existing UE RRC task during the NAS timeout forced-IDLE operation. */
static bool stage5q_keep_camped_timeout[MAX_NUM_NR_UE_INST] = {false};
""",
        "RRC-scoped one-shot flag")
    data = one(data,
        "      nr_rrc_going_to_IDLE(rrc, OTHER, NULL);\n",
        """      /* Stage5Q: preserve a still-valid camped-cell SIB1 for this
       * experimental timeout only; if missing, fall back to normal
       * cell selection and reacquisition. No fake network RRCRelease. */
      AssertFatal(rrc->ue_id < MAX_NUM_NR_UE_INST, "Stage5Q invalid UE index\\n");
      const bool valid_camped_sib1 = rrc->perNB[0].SInfo.sib1 != NULL
                                  && rrc->perNB[0].SInfo.sib1_validity;
      stage5q_keep_camped_timeout[rrc->ue_id] = valid_camped_sib1;
      LOG_W(NR_RRC, "Stage5Q: forced Service Request timeout idle, keep_camped=%d\\n",
            valid_camped_sib1);
      nr_rrc_going_to_IDLE(rrc, OTHER, NULL);
      stage5q_keep_camped_timeout[rrc->ue_id] = false;
""",
        "Stage5I-only forced idle")
    old = """  const bool preserve_camped_context = rrc->nrRrcState != RRC_STATE_DETACH_NR && release_cause == OTHER && RRCRelease
                                       && (!rrcReleaseIEs || !rrcReleaseIEs->redirectedCarrierInfo);"""
    new = """  const bool preserve_camped_context =
      (rrc->nrRrcState != RRC_STATE_DETACH_NR && release_cause == OTHER && RRCRelease
       && (!rrcReleaseIEs || !rrcReleaseIEs->redirectedCarrierInfo))
      || (rrc->nrRrcState != RRC_STATE_DETACH_NR && release_cause == OTHER
          && stage5q_keep_camped_timeout[rrc->ue_id]
          && rrc->perNB[0].SInfo.sib1 != NULL && rrc->perNB[0].SInfo.sib1_validity);"""
    data = one(data,old,new,"keep camped only on valid forced timeout")
    if data.count("stage5q_keep_camped_timeout[rrc->ue_id]") != 3:
        raise RuntimeError("FAIL CLOSED: wrong number of Stage5Q guard references")
    p.write_text(data)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    args = ap.parse_args()
    patch(args.source.resolve())
    print("PASS experimental Stage5Q camped-context guard anchored; NO DEPLOY")
