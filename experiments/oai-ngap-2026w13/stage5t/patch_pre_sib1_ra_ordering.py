#!/usr/bin/env python3
"""Stage5T: test-only MAC RA trigger ordering guard for post-idle SIB1 reacquisition.

Pinned OAI W45 source behavior:
* Full GO_TO_IDLE resets current UL/DL BWP and sets UE_NOT_SYNC, requests PHY sync.
* MIB/SIB1 scheduling transitions UE_NOT_SYNC -> UE_RECEIVING_SIB.
* SIB1 configuration populates BWP0 then transitions RECEIVING_SIB -> PERFORMING_RA.
* But NAS_INITIAL_UL_TRANSFER_REQ may trigger MAC RA before the MIB/SIB1
  sequence; upstream nr_mac_start_ra previously forced PERFORMING_RA
  without any BWP. That can prevent SIB1 reception and leave Stage5M
  permanently deferring RA in UE_PERFORMING_RA.

Only postpone the RRC/NAS-triggered RA state transition until actual SIB1
config exists. Never fabricate a BWP, SIB1, registration or successful packet.
Keep existing Stage5M/5Q fail-closed guards. Experimental NO DEPLOY.
"""
from pathlib import Path
import argparse

ANCHOR = """    case NR_MAC_RA_START_SETUP:
    case NR_MAC_RA_START_T300:
      reset_ra(mac, false);
      reset_mac_inst(mac);
      mac->msg3_C_RNTI = false;
      mac->state = UE_PERFORMING_RA;
      break;
"""
NEW = """    case NR_MAC_RA_START_SETUP:
    case NR_MAC_RA_START_T300:
      /* Stage5T: A T3517-like RRC idle reset may have cleared all BWP
       * configuration, while the NAS Service Request is already pending
       * for RRCSetupComplete. Forcing PERFORMING_RA here prevents the
       * normal UE_NOT_SYNC -> UE_RECEIVING_SIB -> SIB1/BWP0 setup path.
       * Keep the current sync/SIB state and pending NAS intact. The
       * existing SIB1 config path starts RA only after BWP0 setup.
       * No allocation, fabricated SIB1, fake NAS reply or override
       * of Stage5M/5Q RA guards is permitted. */
      if (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL) {
        LOG_W(NR_MAC,
              "Stage5T: postpone MAC RA until SIB1 config restores BWP0 (mac_state=%d ul_count=%d dl_count=%d)\\n",
              mac->state, mac->ul_BWPs.count, mac->dl_BWPs.count);
        break;
      }
      reset_ra(mac, false);
      reset_mac_inst(mac);
      mac->msg3_C_RNTI = false;
      mac->state = UE_PERFORMING_RA;
      break;
"""

def patch(root):
    root=Path(root)
    path=root/"openair2/LAYER2/NR_MAC_UE/config_ue.c"
    if not path.is_file():
        raise RuntimeError("FAIL CLOSED Stage5T: missing pinned MAC config_ue.c")
    original=path.read_text()
    if "Stage5T: postpone MAC RA" in original:
        raise RuntimeError("FAIL CLOSED Stage5T: already applied")
    if original.count(ANCHOR)!=1:
        raise RuntimeError("FAIL CLOSED Stage5T: expected exactly one frozen W45 MAC RA trigger anchor")
    for required in (
        "if (mac->state == UE_NOT_SYNC && mac->get_sib1)",
        "mac->state = UE_RECEIVING_SIB;",
        "if (mac->state == UE_RECEIVING_SIB && can_start_ra)",
        "configure_common_BWP_ul(mac, bwp_id",
    ):
        if required not in original:
            raise RuntimeError("FAIL CLOSED Stage5T: missing native SIB1/BWP recovery contract: "+required)
    changed=original.replace(ANCHOR,NEW)
    if changed.count("Stage5T: postpone MAC RA")!=1:
        raise RuntimeError("FAIL CLOSED Stage5T: anomalous RA patch")
    path.write_text(changed)
    print("PASS Stage5T source-only MAC RA trigger ordering guard; isolated 5G RFsim research; NO DEPLOY")

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("source",type=Path)
    patch(p.parse_args().source.resolve())
