#!/usr/bin/env python3
"""Stage5ZG source-pinned, log-only RRC->MAC SIB scheduling handoff markers.

Applied only after Stage5ZF on an isolated GitHub-hosted research UE.
Adds two primitive-valued LOG calls without mutating RRC/MAC behavior.
Fail closed on upstream drift and never apply to the HP lab.
"""
from pathlib import Path
import argparse

RRC="openair2/RRC/NR_UE/rrc_UE.c"
L2="openair2/RRC/NR_UE/L2_interface_ue.c"

RRC_OLD="""      sib_msg.payload.sched_sib.get_sib = get_sib;
      nr_rrc_send_msg_to_mac(rrc, &sib_msg);
"""
RRC_NEW="""      sib_msg.payload.sched_sib.get_sib = get_sib;
      RRCLOG_I("Stage5ZG: sib_schedule_sent get_sib=%d sib_pending=%d\\n",
               get_sib, SI_info->sib_pending);
      nr_rrc_send_msg_to_mac(rrc, &sib_msg);
"""
L2_OLD="""    case NR_MAC_RRC_SCHED_SIB:
      nr_rrc_mac_sched_sib(instance_id, msg->payload.sched_sib.get_sib);
      break;
"""
L2_NEW="""    case NR_MAC_RRC_SCHED_SIB:
      LOG_I(NR_MAC, "Stage5ZG: sib_schedule_dispatched get_sib=%d\\n",
            msg->payload.sched_sib.get_sib);
      nr_rrc_mac_sched_sib(instance_id, msg->payload.sched_sib.get_sib);
      break;
"""

def _once(s,old,new,label):
    if s.count(old)!=1:
        raise RuntimeError(f"FAIL CLOSED Stage5ZG: {label} pinned callsite count {s.count(old)} instead of 1")
    return s.replace(old,new)

def patch(root):
    root=Path(root)
    a=root/RRC
    b=root/L2
    if not a.is_file() or not b.is_file():
        raise RuntimeError("FAIL CLOSED Stage5ZG: missing source files")
    ra=a.read_text()
    lb=b.read_text()
    if "Stage5ZG:" in ra or "Stage5ZG:" in lb:
        raise RuntimeError("FAIL CLOSED Stage5ZG: instrumentation already applied")
    for token in ("Stage5ZF: si_decision","get_sib = check_si_status(SI_info);",
                  "SI_info->sib_pending = true;"):
        if token not in ra:
            raise RuntimeError("FAIL CLOSED Stage5ZG: RRC state source contract missing "+token)
    aa=_once(ra,RRC_OLD,RRC_NEW,"RRC message-send")
    bb=_once(lb,L2_OLD,L2_NEW,"MAC message-dispatch")
    if aa.count("Stage5ZG: sib_schedule_sent")!=1 or bb.count("Stage5ZG: sib_schedule_dispatched")!=1:
        raise RuntimeError("FAIL CLOSED Stage5ZG: incorrect call count")
    a.write_text(aa)
    b.write_text(bb)
    print("PASS Stage5ZG: log-only RRC SIB scheduling message send and dispatch; NO DEPLOY",flush=True)

if __name__=="__main__":
    arg=argparse.ArgumentParser(description=__doc__)
    arg.add_argument("oai_source_root",type=Path)
    patch(arg.parse_args().oai_source_root.resolve())
