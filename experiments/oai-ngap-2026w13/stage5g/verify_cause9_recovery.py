#!/usr/bin/env python3
"""Fail-closed real evidence gate for Stage 5G 3GPP cause-#9 UE fallback.

Synthetic fixtures may test this parser, but only a successful isolated
runtime experiment with distinct post-fault NAS/PDU/F1U/NGU evidence passes.
Do not confuse test source coverage with native service restoration.
"""
import argparse
import json
from pathlib import Path
import re

ANSI=re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

STEPS=(
    "UE_identity_cannot_be_derived_by_the_network",
    "Stage5G: cause #9, cleared stale GUTI and NAS security",
    "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE",
    "Received Registration Accept",
    "PDU Session Establishment Accept",
)


def check(out):
    out=Path(out)
    reasons=[]
    try:
        events=json.loads((out/"results.json").read_text())
        status=json.loads((out/"stage5e-recovery-status.json").read_text())
    except (OSError,ValueError,TypeError) as exc:
        return False,["Missing/corrupt stage evidence: "+str(exc)]
    if not isinstance(events,list) or not isinstance(status,dict):
        return False,["Malformed evidence structures"]
    for name in ("STAGE4O","STAGE5E","T31-AMF-actually-stopped",
                 "T34-new-NGSetupResponse-after-AMF-restart",
                 "T95-NGAP-and-F1C-after-UE-service"):
        if not any(isinstance(x,dict) and x.get("stage")==name and x.get("status")=="PASS" for x in events):
            reasons.append("Missing live "+name+" PASS")
    for key,expected in (
        ("amf_only_fault",True),
        ("new_ngap_connection_observed",True),
        ("ues_were_restarted",False),
        ("f1_du_was_restarted",False),
        ("externally_triggered_f1_handover",False),
        ("rc_ric_required",False),
        ("both_ue_icmp_recovered",True),
        ("production_authorized",False),
        ("production_decision","NO_GO"),
    ):
        if key not in status or status[key] != expected:
            reasons.append("Invalid "+key+" evidence")
    pdu_before=status.get("before_pdu_ips",{})
    pdu_after=status.get("after_pdu_ips",{})
    if not isinstance(pdu_before,dict) or not isinstance(pdu_after,dict) or not all(
        isinstance(pdu_before.get(x),str) and isinstance(pdu_after.get(x),str)
        and pdu_before[x] != pdu_after.get("ue-small" if x=="ue" else "ue")
        for x in ("ue","ue-small")
    ):
        reasons.append("Missing two distinct pre/post PDU addresses")
    if not (out/"gtpu.pcap").is_file() or (out/"gtpu.pcap").stat().st_size<150:
        reasons.append("Missing post-AMF real GTP-U PCAP")
    if not (out/"post-AMF-gtpu-evidence.json").is_file():
        reasons.append("Missing post-AMF F1U and NGU wire decoding")
    for role in ("ue","ue-small"):
        p=out/("cleanup-"+role+".log")
        try:
            log=ANSI.sub("",p.read_text(errors="replace"))
        except OSError:
            reasons.append("Missing final log "+role)
            continue
        pos=-1
        for step in STEPS:
            index=log.find(step,pos+1)
            if index<0:
                reasons.append(role+" no new sequential NAS "+step)
                break
            pos=index
    return not reasons,reasons


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("evidence",type=Path)
    args=p.parse_args()
    ok,reasons=check(args.evidence)
    print(json.dumps({"stage":"STAGE5G-RELEASE16-CAUSE9",
                      "status":"PASS" if ok else "FAIL",
                      "reasons":reasons,"production_decision":"NO_GO"},indent=2))
    return 0 if ok else 1


if __name__=="__main__":
    raise SystemExit(main())
