#!/usr/bin/env python3
"""Stage5ZB: fail-closed, post-NGSetup UE radio -> CU receipt diagnostics.

Only ingests the bounded/redacted Stage5R timeline. No packet non-delivery,
3GPP conformance, per-subscriber or production readiness inference.
Never modifies the genuine Stage5P / Stage5Y acceptance outcome.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

NAME="stage5zb-rrc-radio-boundary.json"
ROLES={"macro":("ue","macro",3804001,0xe0101),
       "small":("ue-small","small",3804002,0xe0102)}
UE_CATS=("nas_service_request","ue_rrc_nas_buffered","ue_rrc_mac_ra_triggered",
         "ue_rrc_nas_pending_used","ue_rrc_setup_complete_generated",
         "ue_mac_msg3_transmitted","ue_mac_ra_succeeded","nas_service_reject",
         "nas_pdu_accept","nas_retries_exhausted")
DU_CATS=("du_mac_msg3_scheduled","du_mac_msg4_ack","du_ra_ind","du_ue_context")
CU_CATS=("cu_rrc_setup_complete","cu_ul_nas","cu_amf_selection_selected",
         "cu_amf_selection_no_match")
AMF_CATS=("amf_initial_ue_cell",)
def count(events,source,categories,cell=None):
    values=Counter(e.get("event") for e in events
                   if e.get("source")==source and
                   (cell is None or e.get("cell_id")==cell))
    return {name:values[name] for name in categories}

def build(evidence):
    p=Path(evidence)
    report={
        "schema":"hcran-stage5zb-rrc-radio-boundary-v1",
        "source":"bounded-stage5r-category-timeline-only",
        "status":"INCOMPLETE_OBSERVATION",
        "production_decision":"NO_GO", "production_authorized":False,
        "limitations":[
            "Log categories do not demonstrate message-level delivery, loss, or UE identity",
            "OAI logs can omit or duplicate message categories",
            "A generated RRCSetupComplete marker is not proof of F1-C delivery",
            "All classifications are diagnostic-only; the genuine recovery verdict is authoritative",
        ], "cycles":[]
    }
    try:
        data=json.loads((p/"stage5r-protocol-timeline.json").read_text())
        events=data["events"]
        if (data.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1"
                or not isinstance(events,list)
                or data.get("stage5zb_radio_boundary_categories") is not True):
            raise ValueError("Unqualified Stage5ZB source")
        clean=(data.get("collection_limit_reached") is False and
               data.get("collection_issues")==[])
        report["observation_complete"]=clean
        starts=sorted((e for e in events if
            e.get("source")=="harness" and e.get("event")=="milestone" and
            e.get("stage")=="STAGE5P-CYCLE-START" and type(e.get("cycle")) is int),
            key=lambda e:e["elapsed_seconds"])
        for i,start in enumerate(starts):
            high=starts[i+1]["elapsed_seconds"] if i+1<len(starts) else float("inf")
            subset=[e for e in events if start["elapsed_seconds"]<=e.get("elapsed_seconds",-1)<high]
            ng=next((e["elapsed_seconds"] for e in subset if
                e.get("source")=="harness" and e.get("event")=="milestone" and
                e.get("stage")=="T34-new-NGSetupResponse-after-AMF-restart" and
                e.get("status")=="PASS"),None)
            post=[e for e in subset if ng is not None and e.get("elapsed_seconds",-1)>=ng]
            end=next((e.get("status") for e in reversed(subset) if
                e.get("source")=="harness" and e.get("event")=="milestone" and
                e.get("stage")=="STAGE5P-CYCLE"),"NOT_COMPLETE")
            cells={}
            for label,(ue,du,cu_cell,amf_cell) in ROLES.items():
                u=count(post,ue,UE_CATS)
                d=count(post,du,DU_CATS)
                c=count(post,"cu",CU_CATS,cu_cell)
                a=count(post,"core",AMF_CATS,amf_cell)
                if not clean or ng is None:
                    classification="INCOMPLETE_OBSERVATION"
                elif u["nas_pdu_accept"]:
                    classification="FRESH_PDU_OBSERVED"
                elif u["ue_rrc_nas_buffered"] and not u["ue_rrc_setup_complete_generated"]:
                    classification="NAS_BUFFERED_WITHOUT_GENERATED_RRC_COMPLETE_MARKER"
                elif u["ue_rrc_setup_complete_generated"] and not c["cu_rrc_setup_complete"]:
                    classification="UE_GENERATED_RRC_COMPLETE_WITHOUT_CU_RECEIPT_MARKER"
                elif c["cu_rrc_setup_complete"] and not a["amf_initial_ue_cell"]:
                    classification="CU_RRC_COMPLETE_WITHOUT_AMF_CELL_INGRESS_MARKER"
                else:
                    classification="INSUFFICIENT_TO_LOCALIZE"
                cells[label]={
                    "ue":u, "du":d, "cu":c, "amf":a,
                    "diagnostic_classification":classification
                }
            report["cycles"].append({
                "cycle":start["cycle"],
                "new_ngsetup_response_observed":ng is not None,
                "genuine_recovery_status":end,
                "post_ngsetup":cells
            })
        if starts and clean:
            report["status"]="DIAGNOSTIC_ONLY"
    except (OSError, ValueError, TypeError, KeyError) as e:
        report["error_type"]=type(e).__name__
    (p/NAME).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5ZB-RADIO-RRC-BOUNDARY",
                      "status":report["status"],
                      "cycles":len(report["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    return report

def main():
    arg=argparse.ArgumentParser(description=__doc__)
    arg.add_argument("evidence",type=Path)
    build(arg.parse_args().evidence)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
