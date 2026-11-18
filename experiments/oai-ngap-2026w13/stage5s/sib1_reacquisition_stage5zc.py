#!/usr/bin/env python3
"""Stage5ZC: category-only SIB1/BWP0 recovery log observation for isolated RFsim.

Only parses existing live Stage5R UE/DU stdout streams. Does not persist raw
logs, PLMN, RNTI, subscriber identity, cell identifier or payload content.
The optional bounded BWP count metadata is diagnostic, never an acceptance
signal. Missing log messages cannot prove missing protocol packets.
"""
import re

MAX_LINE_CHARS = 4096

UE_PATTERNS = (
    ("ue_ra_deferred_until_sib1", re.compile(
        r"Stage5T: postpone MAC RA until SIB1 config restores BWP0", re.I)),
    ("ue_phy_sync_started", re.compile(r"Starting sync detection", re.I)),
    ("ue_phy_initial_sync_success", re.compile(r"Initial sync successful", re.I)),
    ("ue_phy_synchronized", re.compile(r"UE synchronized! decoded_frame_rx", re.I)),
    ("ue_prach_scheduled", re.compile(
        r"PRACH scheduler: Selected RO Frame", re.I)),
)
DU_PATTERNS = (
    ("du_prach_job_in_past_warning", re.compile(
        r"PRACH job is in the past", re.I)),
)
COUNTS = re.compile(
    r"mac_state=(-?\d+) ul_count=(\d+) dl_count=(\d+)")

def classify(role, line):
    """Yield (safe category, bounded numeric metadata); never the source line."""
    if role in ("ue", "ue-small"):
        rules = UE_PATTERNS
    elif role in ("macro", "small"):
        rules = DU_PATTERNS
    else:
        return ()
    fragment = line[:MAX_LINE_CHARS]
    found = []
    for name, pattern in rules:
        if pattern.search(fragment) is None:
            continue
        meta = {}
        if name == "ue_ra_deferred_until_sib1":
            m = COUNTS.search(fragment)
            if m:
                state, ul, dl = (int(x) for x in m.groups())
                if -128 <= state <= 128 and 0 <= ul <= 1024 and 0 <= dl <= 1024:
                    meta = {"mac_state":state, "ul_bwp_count":ul,
                            "dl_bwp_count":dl}
        found.append((name,meta))
    return tuple(found)
