#!/usr/bin/env python3
"""Stage5X passive F1-C/CU-to-AMF ingress gap correlation for *synthetic RFsim only*.

Observes genuine source-pinned Stage5T UE+CU behavior through bounded Stage5R
allowlisted events. Never captures IMSI, SUCI, RNTI, NAS payload, subscriber
credentials or arbitrary raw log lines. Emits DIAGNOSTIC_ONLY even on failure.
A missing log event cannot conclusively demonstrate packet non-delivery.
"""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path

CELLS={
    "ue":{"cu_cell_id":3804001,"amf_cell_id":0xe0101},
    "ue-small":{"cu_cell_id":3804002,"amf_cell_id":0xe0102},
}
STAGES={"begin":"STAGE5P-CYCLE-START",
        "amf_restarted":"T33-AMF-process-restarted",
        "ng_restored":"T34-new-NGSetupResponse-after-AMF-restart",
        "end":"STAGE5P-CYCLE"}
REPORT="stage5x-f1c-amf-ingress-gap.json"

def build_report(root):
    p=Path(root)
    result={
        "schema":"hcran-stage5x-cu-f1c-amf-ingress-v1",
        "scope":"isolated-source-pinned-2UE-2DU-5GC-RFsim-observation-only",
        "generated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status":"INSUFFICIENT_EVIDENCE",
        "no_delivery_inference":"CU RRCSetupComplete and AMF InitialUEMessage logs are observational; their absence is not conclusive proof of SCTP/NGAP packet loss",
        "cell_mapping":CELLS,
        "production_authorized":False,
        "production_decision":"NO_GO",
        "cycles":[]
    }
    try:
        timeline=json.loads((p/"stage5r-protocol-timeline.json").read_text())
        events=timeline["events"]
        if timeline.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1" or not isinstance(events,list):
            raise ValueError("Invalid Stage5R evidence structure")
        result["trace_complete"]=(timeline.get("collection_limit_reached") is False and
                                  timeline.get("collection_issues")==[])
        result["live_cu_amf_streams_present"]=(
            isinstance(timeline.get("category_counts"),dict) and
            "core" in timeline["category_counts"] and
            "cu" in timeline["category_counts"])
        starts=sorted((e for e in events if e.get("source")=="harness" and
                       e.get("event")=="milestone" and e.get("stage")==STAGES["begin"] and
                       isinstance(e.get("cycle"),int)),key=lambda x:x["elapsed_seconds"])
        for i,start in enumerate(starts):
            first=start["elapsed_seconds"]
            final=starts[i+1]["elapsed_seconds"] if i+1<len(starts) else float("inf")
            segment=[x for x in events if first<=x.get("elapsed_seconds",-1)<final]
            def find(name):
                return next((x for x in segment if x.get("source")=="harness" and
                             x.get("event")=="milestone" and x.get("stage")==STAGES[name] and
                             (name in ("begin","end") or x.get("status")=="PASS")),None)
            restarted=find("amf_restarted")
            ng=find("ng_restored")
            final_event=next((e for e in reversed(segment) if e.get("source")=="harness" and
                              e.get("event")=="milestone" and e.get("stage")==STAGES["end"]),None)
            if restarted is None:
                window=[]
            else:
                window=[x for x in segment if x.get("elapsed_seconds",-1)>=restarted["elapsed_seconds"]]
            row={
                "cycle":start["cycle"],
                "amf_restart_observed":restarted is not None,
                "new_ngsetup_response_observed":ng is not None,
                "genuine_recovery_status":final_event.get("status") if final_event else "NOT_COMPLETE",
                "ue":{},
            }
            for role,mapping in CELLS.items():
                ue=Counter(x.get("event") for x in window if x.get("source")==role)
                cu=Counter(x.get("event") for x in window if x.get("source")=="cu" and
                           x.get("cell_id")==mapping["cu_cell_id"])
                amf=Counter(x.get("event") for x in window if x.get("source")=="core" and
                            x.get("cell_id")==mapping["amf_cell_id"])
                entry={
                    "ue_service_request_markers":ue["nas_service_request"],
                    "ue_service_reject_markers":ue["nas_service_reject"],
                    "ue_fresh_pdu_accept_markers":ue["nas_pdu_accept"],
                    "ue_bwp_deferral_markers":ue["mac_bwp_deferral"],
                    "ue_nas_retries_exhausted":ue["nas_retries_exhausted"],
                    "cu_rrc_setup_complete_cell_markers":cu["cu_rrc_setup_complete"],
                    "cu_ul_information_transfer_cell_markers":cu["cu_ul_nas"],
                    "amf_initial_ue_cell_markers":amf["amf_initial_ue_cell"],
                }
                if not result["trace_complete"] or not result["live_cu_amf_streams_present"]:
                    gap="INCONCLUSIVE_INCOMPLETE_LIVE_SOURCES"
                elif entry["ue_fresh_pdu_accept_markers"]>0:
                    gap="FRESH_PDU_OBSERVED"
                elif entry["ue_service_request_markers"] and not entry["cu_rrc_setup_complete_cell_markers"]:
                    gap="UE_SERVICE_REQUEST_BUT_NO_CU_RRCSETUPCOMPLETE_OBSERVED"
                elif entry["cu_rrc_setup_complete_cell_markers"] and not entry["amf_initial_ue_cell_markers"]:
                    gap="CU_RRC_OBSERVED_BUT_NO_AMF_INITIAL_UE_CELL_OBSERVED"
                elif entry["amf_initial_ue_cell_markers"] and not entry["ue_service_reject_markers"]:
                    gap="AMF_INITIAL_UE_OBSERVED_BUT_NO_UE_SERVICE_REJECT"
                else:
                    gap="INSUFFICIENT_EVIDENCE_FOR_INGRESS_BOUNDARY"
                entry["gap_classification"]=gap
                row["ue"][role]=entry
            result["cycles"].append(row)
        if starts:
            result["status"]="DIAGNOSTIC_ONLY" if result["trace_complete"] else "INCOMPLETE_OBSERVATION"
    except (OSError,ValueError,TypeError,KeyError) as exc:
        result["error_type"]=type(exc).__name__
    dest=p/REPORT
    dest.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5X-F1C-AMF-INGRESS",
                      "status":result["status"],"cycles":len(result["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    return 0  # Never override the genuine Stage5P recovery exit code.

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence",type=Path)
    raise SystemExit(build_report(ap.parse_args().evidence))
