#!/usr/bin/env python3
"""Stage5ZD: SOURCE-PINNED log-only MIB -> SIB1/BWP0 transition markers.

Experimental OAI UE build in disposable GitHub-hosted RFsim, never HP.
Only adds LOG_I calls containing bounded primitive MAC-state integers.
No state mutation, retry, resource allocation, NAS, packet or UE identity.
Strict anchors against pinned W45 code; fail closed on drift/reapplication.
"""
from pathlib import Path
import argparse

PINNED = "openair2/LAYER2/NR_MAC_UE/config_ue.c"
SCHED_ANCHOR = """  if (mac->state == UE_NOT_SYNC && mac->get_sib1)
    mac->state = UE_RECEIVING_SIB;
"""
SCHED_NEW = SCHED_ANCHOR + """  LOG_I(NR_MAC,
        "Stage5ZD: sib1_schedule state=%d get_sib1=%d mib_present=%d ul_bwp=%d dl_bwp=%d\\n",
        mac->state, mac->get_sib1, mac->mib != NULL,
        mac->ul_BWPs.count, mac->dl_BWPs.count);
"""
MIB_ANCHOR = "  nr_ue_decode_mib(mac, cc_idP);\n"
MIB_NEW = MIB_ANCHOR + """  /* Stage5ZE: avoid periodic connected-state MIB logs. The probe is only
   * needed when post-idle recovery lacks BWP0. No MAC state is changed. */
  if (mac->state < UE_CONNECTED &&
      (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL)) {
    LOG_I(NR_MAC,
          "Stage5ZD: mib_configured state=%d mib_present=%d get_sib1=%d ul_bwp=%d dl_bwp=%d\\n",
          mac->state, mac->mib != NULL, mac->get_sib1,
          mac->ul_BWPs.count, mac->dl_BWPs.count);
  }
"""
SIB1_ANCHOR = """  if (mac->state == UE_RECEIVING_SIB && can_start_ra)
    mac->state = UE_PERFORMING_RA;
"""
SIB1_NEW = SIB1_ANCHOR + """  LOG_I(NR_MAC,
        "Stage5ZD: sib1_configured state=%d ul_bwp=%d dl_bwp=%d current_ul=%d current_dl=%d\\n",
        mac->state, mac->ul_BWPs.count, mac->dl_BWPs.count,
        mac->current_UL_BWP != NULL, mac->current_DL_BWP != NULL);
"""

def _once(s, old, new, label):
    if s.count(old) != 1:
        raise RuntimeError("FAIL CLOSED Stage5ZD: "+label+" anchor missing, repeated or source drifted")
    return s.replace(old,new)

def patch(root):
    root=Path(root)
    target=root/PINNED
    if not target.is_file():
        raise RuntimeError("FAIL CLOSED Stage5ZD: expected pinned W45 MAC config source missing")
    raw=target.read_text()
    if "Stage5ZD:" in raw:
        raise RuntimeError("FAIL CLOSED Stage5ZD: candidate already instrumented")
    if not all(x in raw for x in (
        "void nr_rrc_mac_sched_sib(", "void nr_rrc_mac_config_req_mib(",
        "void nr_rrc_mac_config_req_sib1(", "void nr_rrc_mac_config_other_sib(",
    )):
        raise RuntimeError("FAIL CLOSED Stage5ZD: native 3GPP MAC SIB transition contract unavailable")
    new=_once(raw,SCHED_ANCHOR,SCHED_NEW,"sched_sib")
    new=_once(new,MIB_ANCHOR,MIB_NEW,"MIB configured")
    start=new.index("void nr_rrc_mac_config_req_sib1(")
    end=new.index("void nr_rrc_mac_config_other_sib(",start)
    segment=_once(new[start:end],SIB1_ANCHOR,SIB1_NEW,"SIB1 configured")
    new=new[:start]+segment+new[end:]
    if new.count("Stage5ZD:")!=3:
        raise RuntimeError("FAIL CLOSED Stage5ZD: number of observed callsites changed")
    target.write_text(new)
    print("Stage5ZD PASS: exactly three log-only MIB/SIB1 MAC transition markers; no deployment",flush=True)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("oai_source_root",type=Path)
    patch(p.parse_args().oai_source_root)
