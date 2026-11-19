#!/usr/bin/env python3
"""Stage5ZG allowlisted source-pinned RRC->MAC SIB request observation.

Only bounded get_sib values (SIB type selector), no identities or raw lines.
"""
import re
SENT=re.compile(r"Stage5ZG: sib_schedule_sent get_sib=(\d+) sib_pending=(\d+)")
DISPATCH=re.compile(r"Stage5ZG: sib_schedule_dispatched get_sib=(\d+)")
def classify(role,line):
    if role not in ("ue","ue-small"):
        return ()
    fragment=line[:4096]
    values=[]
    sent=SENT.search(fragment)
    if sent:
        sib,pending=map(int,sent.groups())
        if 1 <= sib<=32 and pending in (0,1):
            values.append(("ue_rrc_sib_request_sent",{"get_sib":sib,"sib_pending":pending}))
    dispatch=DISPATCH.search(fragment)
    if dispatch:
        sib=int(dispatch.group(1))
        if 1<=sib<=32:
            values.append(("ue_mac_sib_request_dispatched",{"get_sib":sib}))
    return tuple(values)
