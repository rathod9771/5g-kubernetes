#!/usr/bin/env python3
"""Stage5ZF redacted bounded SI-decision observation from pinned OAI UE logs.

Only recognized category strings plus bounded numeric state. No subscriber,
RNTI, PLMN, NAS payload, credentials or raw log persistence.
"""
import re

SI=re.compile(r"Stage5ZF: si_decision\s+sample=(\d+) sib1_valid=(\d+) sib_pending=(\d+) get_sib=(\d+) rrc_state=(-?\d+) barred=(\d+)")
MAC=re.compile(r"Stage5ZD: mib_configured\s+sample=(\d+) state=(-?\d+) mib_present=(\d+) get_sib1=(\d+) ul_bwp=(\d+) dl_bwp=(\d+)")
LIMIT=4096
def _safe(values, names, upper, allow_nonsampled_sib=False):
    d={}
    for name, val in zip(names, values):
        number=int(val)
        if name=="sample_n":
            if not 1<=number<=0xffffffff:
                return None
        elif not -128<=number<=upper:
            return None
        d[name]=number
    n=d["sample_n"]
    if n & (n-1) and not (allow_nonsampled_sib and d.get("get_sib",0)>0):
        return None
    return d

def classify(role,line):
    if role not in ("ue","ue-small"):
        return ()
    s=line[:LIMIT]
    out=[]
    m=SI.search(s)
    if m:
        metadata=_safe(m.groups(),("sample_n","sib1_valid","sib_pending","get_sib","rrc_state","barred"),1024,True)
        if metadata is not None:
            out.append(("ue_rrc_si_schedule_decision",metadata))
    m=MAC.search(s)
    if m:
        metadata=_safe(m.groups(),("sample_n","mac_state","mib_present","get_sib1","ul_bwp","dl_bwp"),1024)
        if metadata is not None:
            out.append(("ue_mac_mib_sampled",metadata))
    return tuple(out)
