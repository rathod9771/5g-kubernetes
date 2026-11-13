#!/usr/bin/env python3
"""Read-only 3GPP AS algorithm negotiation proof from bounded Stage4Z CU+UE logs.

This parser verifies CU's actual selected NEA2/NIA2 AS algorithm numbers,
CU downlink SecurityModeCommand, UE reception/response, CU uplink
SecurityModeComplete, and successful NAS/PDU continuation, all in
correct order in genuine captured runtime log files.

It does NOT independently decode over-the-air ASN.1/PDCP encryption,
does NOT prove NAS algorithm negotiation, and does NOT authorize
production, multi-UE security compliance or future rollouts.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

ANSI=re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

def inspect(cu_source,ue_source):
    cu=ANSI.sub("",cu_source)
    ue=ANSI.sub("",ue_source)
    reasons=[]
    match=re.search(r"Selected security algorithms: ciphering\s+(\d+),\s+integrity\s+(\d+)",cu,re.I)
    if match is None:
        reasons.append("No CU AS security algorithm selection captured")
    elif tuple(map(int,match.groups()))!=(2,2):
        reasons.append("CU did not select NEA2/NIA2")
    for stream,name,signatures in [
        (cu,"CU",["Selected security algorithms: ciphering 2, integrity 2",
                  "Generate SecurityModeCommand","Received Security Mode Complete",
                  "Received RRCReconfigurationComplete"]),
        (ue,"UE",["Received securityModeCommand",
                  "encoding securityModeComplete","Received Registration Accept",
                  "Received PDU Session Establishment Accept"]),
    ]:
        positions=[]
        for marker in signatures:
            idx=stream.find(marker)
            if idx==-1:
                reasons.append(name+": missing runtime event "+marker)
            else:
                positions.append(idx)
        if len(positions)==len(signatures) and positions!=sorted(positions):
            reasons.append(name+": SMC and service events out of order")
    report={
        "schema":"hcran-stage5f-observed-as-security-v1",
        "scope":"actual OAI RRC CU/UE logs; no wire-level 3GPP conformance",
        "as_security_mode_observed":len(reasons)==0,
        "cu_as_ciphering_algorithm":2 if len(reasons)==0 else None,
        "cu_as_integrity_algorithm":2 if len(reasons)==0 else None,
        "nas_ciphering_or_integrity_proven":False,
        "onwire_ciphertext_proven":False,
        "ue_and_core_registered_post_security":len(reasons)==0,
        "production_decision":"NO_GO",
        "production_authorized":False,
        "limitations":reasons+[
            "Source logs alone cannot prove on-wire cryptography or NAS security algorithms",
            "Separate staging validation, negative cases and security reviewer approval required"],
    }
    return report

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cu",type=pathlib.Path,required=True)
    ap.add_argument("--ue",type=pathlib.Path,required=True)
    ap.add_argument("--out",type=pathlib.Path,required=True)
    a=ap.parse_args(argv)
    cu=a.cu.read_text(errors="replace")
    ue=a.ue.read_text(errors="replace")
    data=inspect(cu,ue)
    data["cu_log_sha256"]=hashlib.sha256(a.cu.read_bytes()).hexdigest()
    data["ue_log_sha256"]=hashlib.sha256(a.ue.read_bytes()).hexdigest()
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(data,indent=2)+"\n")
    print(json.dumps(data,indent=2))
    return 0 if data["as_security_mode_observed"] else 2

if __name__=="__main__":
    sys.exit(main())
