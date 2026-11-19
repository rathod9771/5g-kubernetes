#!/usr/bin/env python3
"""Stage5ZF: bounded log-only RRC SI decision + MAC MIB sampling (pinned OAI W45).

Apply AFTER frozen Stage5T and Stage5ZD research patches. This does not alter
RRC cached SIB validity, MAC BWP pointers, timers, RA, NAS, or transport.
Fail closed unless exact source-level anchors are present once.
"""
import argparse
from pathlib import Path

RRC_FILE="openair2/RRC/NR_UE/rrc_UE.c"
MAC_FILE="openair2/LAYER2/NR_MAC_UE/config_ue.c"
RRC_OLD="""  if (bcch_message->message.present == NR_BCCH_BCH_MessageType_PR_mib) {
    nr_mac_rrc_message_t rrc_msg = {0};
"""
RRC_NEW="""  /* Stage5ZF research-only: RRC's retained SI validity can disagree with
   * MAC BWP0 readiness after RRC idle. Inspect the decision, do not change it.
   * Bound repeated MIB reports using exponential diagnostic sampling. */
  static unsigned int stage5zf_si_observations = 0; /* one RRC per disposable UE process */
  if (stage5zf_si_observations < 0xffffffffU)
    stage5zf_si_observations++;
  const unsigned int stage5zf_n = stage5zf_si_observations;
  if ((stage5zf_n & (stage5zf_n - 1)) == 0 || get_sib != 0) {
    RRCLOG_I("Stage5ZF: si_decision sample=%u sib1_valid=%d sib_pending=%d get_sib=%d rrc_state=%d barred=%d\\n",
             stage5zf_n, SI_info->sib1_validity, SI_info->sib_pending, get_sib,
             rrc->nrRrcState, barred);
  }
""" + RRC_OLD

MAC_OLD="""  if (mac->state < UE_CONNECTED &&
      (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL)) {
    LOG_I(NR_MAC,
          "Stage5ZD: mib_configured state=%d mib_present=%d get_sib1=%d ul_bwp=%d dl_bwp=%d\\n",
          mac->state, mac->mib != NULL, mac->get_sib1,
          mac->ul_BWPs.count, mac->dl_BWPs.count);
  }
"""
MAC_NEW="""  if (mac->state < UE_CONNECTED &&
      (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL)) {
    /* Stage5ZF: bound repeated missing-BWP MIB markers. Observational only. */
    static unsigned int stage5zf_mac_mib_observations = 0;
    if (stage5zf_mac_mib_observations < 0xffffffffU)
      stage5zf_mac_mib_observations++;
    const unsigned int stage5zf_n = stage5zf_mac_mib_observations;
    if ((stage5zf_n & (stage5zf_n - 1)) == 0) {
      LOG_I(NR_MAC,
            "Stage5ZD: mib_configured sample=%u state=%d mib_present=%d get_sib1=%d ul_bwp=%d dl_bwp=%d\\n",
            stage5zf_n, mac->state, mac->mib != NULL, mac->get_sib1,
            mac->ul_BWPs.count, mac->dl_BWPs.count);
    }
  }
"""

def one(text, before, after, label):
    n=text.count(before)
    if n!=1:
        raise RuntimeError(f"FAIL CLOSED Stage5ZF: {label}: expected 1 pinned anchor, got {n}")
    return text.replace(before,after)

def patch(root):
    root=Path(root)
    rrc=root/RRC_FILE
    mac=root/MAC_FILE
    if not rrc.is_file() or not mac.is_file():
        raise RuntimeError("FAIL CLOSED Stage5ZF: missing pinned RRC/MAC source")
    rs=rrc.read_text()
    ms=mac.read_text()
    if "Stage5ZF:" in rs or "Stage5ZF:" in ms:
        raise RuntimeError("FAIL CLOSED Stage5ZF: already applied")
    required=("static void nr_rrc_ue_decode_NR_BCCH_BCH_Message(",
              "get_sib = check_si_status(SI_info);",
              "SI_info->sib_pending = true;",
              "SI_info->sib1_validity = false;")
    for token in required:
        if token not in rs:
            raise RuntimeError(f"FAIL CLOSED Stage5ZF: RRC source contract missing: {token}")
    new_rrc=one(rs,RRC_OLD,RRC_NEW,"RRC SI scheduling branch")
    new_mac=one(ms,MAC_OLD,MAC_NEW,"Stage5ZE MIB log-only marker")
    # Both modifications verified before either file is written.
    if new_rrc.count("Stage5ZF: si_decision")!=1 or new_mac.count("Stage5ZD: mib_configured")!=1:
        raise RuntimeError("FAIL CLOSED Stage5ZF: patch count incorrect")
    rrc.write_text(new_rrc)
    mac.write_text(new_mac)
    print("PASS Stage5ZF source-pinned RRC SI decision and bounded MAC MIB logs; NO DEPLOY",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source_root",type=Path)
    patch(ap.parse_args().source_root.resolve())
