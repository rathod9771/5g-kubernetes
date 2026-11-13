#!/usr/bin/env python3
"""Independent fail-closed Stage5H real F1/RRC/NAS + postfault GTPU contract.

This validates a completed disposable experiment, not 3GPP conformance or
production authorization. Synthetic positive fixtures are merely unit tests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

FLOW=(
 "Generate initial NAS message: Service Request",
 "UE_identity_cannot_be_derived_by_the_network",
 "Stage5G: cause #9, cleared stale GUTI and NAS security",
 "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE",
 "Received Registration Accept",
 "PDU Session Establishment Accept",
)
EVENTS=(
 "STAGE4Z",
 "T100-before-AMF-real-two-way-F1-and-PDU",
 "T101-only-test-AMF-actually-stopped",
 "T102-new-NGSetupResponse-after-AMF-restart",
 "T104-nas-cause9-to-SUCI-and-PDU",
 "STAGE5H",
)
ANSI=re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def check(folder):
    folder=Path(folder)
    issues=[]
    try:
        record=json.loads((folder/"stage5h-cause9-runtime.json").read_text())
        events=json.loads((folder/"results.json").read_text())
    except (OSError,ValueError,TypeError) as exc:
        return False,["Missing/corrupt Stage5H runtime evidence: "+str(exc)]
    if not isinstance(record,dict) or not isinstance(events,list):
        return False,["Evidence is not an object/list"]
    expected={
      "schema":"stage5h-real-cause9-one-UE-F1-server",
      "source":"real-disposable-docker-RFSIM",
      "mode":"on-demand-Re16-24.501-cause9-SUCI-initial-registration",
      "original_ue_process_unchanged":True,
      "manual_f1_handover_after_fault":False,
      "native_spontaneous_recovery_proven":False,
      "recovery_timing_slo_proven":False,
      "production_authorized":False,
      "production_decision":"NO_GO",
    }
    for key,val in expected.items():
        if record.get(key)!=val:
            issues.append("Unexpected "+key+" or value")
    for event in EVENTS:
        if not any(isinstance(x,dict) and x.get("stage")==event and x.get("status")=="PASS" for x in events):
            issues.append("No real "+event+" PASS")
    packet=record.get("actual_postfault_gtpu")
    if not isinstance(packet,dict):
        issues.append("Missing actual postfault GTPU")
    else:
        for key in ("f1u_ul","f1u_dl","ngu_ul","ngu_dl"):
            val=packet.get(key)
            if type(val) is not int or val<6:
                issues.append("Postfault "+key+" G-PDU count too low")
    digest=record.get("post_fault_pcap_sha256")
    path=folder/"after-cause9-re-registration-gtpu.pcap"
    if not isinstance(digest,str) or not re.fullmatch("[0-9a-f]{64}",digest):
        issues.append("Missing SHA256 proof")
    if not path.is_file() or path.stat().st_size<150:
        issues.append("Missing postfault real PCAP")
    elif isinstance(digest,str) and hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        issues.append("Captured PCAP checksum mismatch")
    if not isinstance(record.get("nas_event_chain"),list) or record["nas_event_chain"]!=list(FLOW):
        issues.append("Incomplete NAS cause9/SUCI/registration sequence")
    try:
        logs=ANSI.sub("",(folder/"cleanup-ue.log").read_text(errors="replace"))
        offset=0
        for segment in FLOW:
            idx=logs.find(segment,offset)
            if idx<0:
                issues.append("Postfault NAS trace missing "+segment)
                break
            offset=idx+len(segment)
    except OSError:
        issues.append("Missing synthetic UE NAS log")
    if not all(isinstance(record.get(key),str) and re.fullmatch(r"10\.45\.\d+\.\d+",record[key])
               for key in ("pre_fault_pdu_ip","post_fault_pdu_ip")):
        issues.append("Missing 5GC synthetic PDU address proof")
    return not issues,issues


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence",type=Path)
    args=ap.parse_args()
    ok,issues=check(args.evidence)
    print(json.dumps({"stage":"STAGE5H-EVIDENCE","status":"PASS" if ok else "FAIL",
                      "reasons":issues,"production_decision":"NO_GO"},indent=2))
    return 0 if ok else 1


if __name__=="__main__":
    raise SystemExit(main())
