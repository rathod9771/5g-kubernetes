#!/usr/bin/env python3
"""Stage5ZB bounded redacted UE/DU signaling boundary classifier.

No UE/CU code modification, radio traffic, process control, NAS capture,
subscriber identifiers, RNTI, raw log persistence, or protocol decoding.
The strings below match actual OAI W45 CI logs. An observed category is
not proof of F1AP message delivery to the CU; absence is not packet loss.
"""
import re

RULES = {
    "ue": (
        ("ue_rrc_nas_buffered", r"Initial NAS UL: .*buffered .*RRCSetupComplete dedicatedNAS"),
        ("ue_rrc_mac_ra_triggered", r"Triggering MAC RA for RRCSetupComplete pending NAS"),
        ("ue_rrc_nas_pending_used", r"Using pending initial NAS message for RRCSetupComplete"),
        ("ue_rrc_setup_complete_generated", r"Generating RRCSetupComplete"),
        ("ue_mac_msg3_transmitted", r"RA-Msg3 transmitted"),
        ("ue_mac_ra_succeeded", r"RA procedure succeeded"),
    ),
    "ue-small": (
        ("ue_rrc_nas_buffered", r"Initial NAS UL: .*buffered .*RRCSetupComplete dedicatedNAS"),
        ("ue_rrc_mac_ra_triggered", r"Triggering MAC RA for RRCSetupComplete pending NAS"),
        ("ue_rrc_nas_pending_used", r"Using pending initial NAS message for RRCSetupComplete"),
        ("ue_rrc_setup_complete_generated", r"Generating RRCSetupComplete"),
        ("ue_mac_msg3_transmitted", r"RA-Msg3 transmitted"),
        ("ue_mac_ra_succeeded", r"RA procedure succeeded"),
    ),
    "macro": (
        ("du_mac_msg3_scheduled", r"Msg3 scheduled"),
        ("du_mac_msg4_ack", r"Received Ack of Msg4.*CBRA procedure succeeded"),
    ),
    "small": (
        ("du_mac_msg3_scheduled", r"Msg3 scheduled"),
        ("du_mac_msg4_ack", r"Received Ack of Msg4.*CBRA procedure succeeded"),
    ),
}
COMPILED = {role: tuple((name,re.compile(pattern,re.I)) for name,pattern in rules)
            for role,rules in RULES.items()}
MAX_LINE_CHARS = 4096

def classify(role, line):
    """Return category names only. Caller archives no raw text."""
    if role not in COMPILED:
        return ()
    fragment = line[:MAX_LINE_CHARS]
    return tuple(name for name,rule in COMPILED[role] if rule.search(fragment))
