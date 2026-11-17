#!/usr/bin/env python3
"""Stage5V: phase-resolved, privacy-safe observer report from existing RFsim events.

Does NOT decode raw NAS/SCTP, assert delivery to AMF, inject faults, alter UE
behavior, produce synthetic PASS, or access HP/OSM/Kubernetes. Designed to
run after both successful and failed genuine Stage5P sessions.
"""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path

ROLES=("ue","ue-small")
SELECT=(
    "rrc_sib1_decoded", "rrc_idle", "rrc_setup_complete",
    "mac_bwp_deferral", "nas_service_request", "nas_service_reject",
    "nas_service_accept", "nas_t3517_timeout", "nas_retries_exhausted",
    "nas_cause9", "nas_suci_register", "nas_registration_accept",
    "nas_pdu_accept", "tun_ipv4_refresh", "mac_prach"
)
CU_SELECT=("cu_rrc_setup_complete","cu_ul_nas","cu_dl_nas","cu_no_amf",
           "cu_rrc_context_release","cu_ngsetup_response")
CYCLE_START="STAGE5P-CYCLE-START"
CYCLE_FINAL="STAGE5P-CYCLE"
NG_RESPONSE="T34-new-NGSetupResponse-after-AMF-restart"

def analyze(root):
    root=Path(root)
    target=root/"stage5v-phase-gap-report.json"
    result={
        "schema":"hcran-stage5v-protocol-phase-gap-v1",
        "generated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status":"INSUFFICIENT_EVIDENCE",
        "source":"allowlisted-synthetic-RFsim-protocol-observer",
        "caution":"RRCSetupComplete at CU is NOT proof of NGAP UL NAS reaching AMF; absent logs are not proof of packet loss",
        "production_authorized":False,
        "production_decision":"NO_GO",
        "cycles":[],
    }
    try:
        d=json.loads((root/"stage5r-protocol-timeline.json").read_text())
        events=d["events"]
        if d.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1" or not isinstance(events,list):
            raise ValueError("invalid bounded timeline")
        starts=sorted((x for x in events if x.get("source")=="harness" and
                       x.get("event")=="milestone" and x.get("stage")==CYCLE_START and
                       isinstance(x.get("cycle"),int)),key=lambda e:e["elapsed_seconds"])
        for idx,st in enumerate(starts):
            begin=st["elapsed_seconds"]
            end=starts[idx+1]["elapsed_seconds"] if idx+1<len(starts) else float("inf")
            window=[x for x in events if begin<=x.get("elapsed_seconds",-1)<end]
            milestone=next((x for x in window if x.get("source")=="harness" and
                            x.get("event")=="milestone" and x.get("stage")==NG_RESPONSE and
                            x.get("status")=="PASS"),None)
            ng=milestone["elapsed_seconds"] if milestone else None
            fin=next((x for x in reversed(window) if x.get("source")=="harness" and
                      x.get("event")=="milestone" and x.get("stage")==CYCLE_FINAL),None)
            record={"cycle":st["cycle"],"amf_ngsetup_restored":ng is not None,
                    "ngsetup_elapsed_from_fault_seconds":round(ng-begin,3) if ng else None,
                    "genuine_cycle_status":fin.get("status") if fin else "NOT_COMPLETE",
                    "ue":{},"cu_shared_observation_after_ngsetup":{}}
            if ng is not None:
                after=[x for x in window if x.get("elapsed_seconds",-1)>=ng]
                for role in ROLES:
                    c=Counter(x.get("event") for x in after if x.get("source")==role)
                    detail={key:c[key] for key in SELECT}
                    record["ue"][role]=detail
                    record["ue"][role]["nas_outcome"]="FRESH_PDU_ACCEPT_OBSERVED" if c["nas_pdu_accept"] else (
                        "NO_SERVICE_REJECT_OBSERVED" if c["nas_service_request"] and not c["nas_service_reject"] else
                        "NO_FRESH_PDU_ACCEPT_OBSERVED")
                cu=Counter(x.get("event") for x in after if x.get("source")=="cu")
                record["cu_shared_observation_after_ngsetup"]={x:cu[x] for x in CU_SELECT}
            result["cycles"].append(record)
        if starts:
            result["status"]="DIAGNOSTIC_ONLY"
            result["trace_incomplete"]=bool(d.get("collection_limit_reached") or d.get("collection_issues"))
            result["header_sampling"]=d.get("wire_header_sampling")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result["reason_type"]=type(exc).__name__
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5V-PHASE-MATRIX","status":result["status"],
                      "cycles":len(result["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    # This diagnostic script never grants a PASS or changes a genuine
    # harness exit code. Explicit diagnostic status is preserved.
    return 0

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("evidence",type=Path)
    raise SystemExit(analyze(p.parse_args().evidence))
