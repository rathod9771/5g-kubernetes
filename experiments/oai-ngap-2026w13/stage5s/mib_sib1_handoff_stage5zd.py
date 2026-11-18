#!/usr/bin/env python3
"""Stage5ZD privacy-reduced MAC MIB/SIB1 handoff and PHY PBCH log categories.

Only known strings from a pinned, isolated Stage5ZD RFsim UE build. Raw logs
are never retained in Stage5R JSON, and the output has only categorical
markers / bounded numeric state. No identity or packet-delivery inference.
"""
import re

NUMERIC = re.compile(r"\b(state|mib_present|get_sib1|ul_bwp|dl_bwp|current_ul|current_dl)=(-?\d+)\b")
ALLOWED = {"state","mib_present","get_sib1","ul_bwp","dl_bwp","current_ul","current_dl"}
CATEGORIES = (
    ("ue_phy_pbch_decode", re.compile(r"Initial sync: pbch decoded sucessfully",re.I)),
    ("ue_phy_pbch_rx_ok", re.compile(r"\bpbch rx ok\b",re.I)),
    ("ue_mib_configured", re.compile(r"Stage5ZD: mib_configured ",re.I)),
    ("ue_sib1_scheduled", re.compile(r"Stage5ZD: sib1_schedule ",re.I)),
    ("ue_sib1_mac_configured", re.compile(r"Stage5ZD: sib1_configured ",re.I)),
)
def classify(role, line):
    if role not in ("ue","ue-small"):
        return ()
    fragment = line[:4096]
    results=[]
    for name,rx in CATEGORIES:
        if not rx.search(fragment):
            continue
        meta={}
        if name.startswith("ue_") and "Stage5ZD:" in fragment:
            for k,v in NUMERIC.findall(fragment):
                if k in ALLOWED and k not in meta:
                    number=int(v)
                    if -128 <= number <= 1024:
                        meta[k]=number
        results.append((name,meta))
    return tuple(results)
