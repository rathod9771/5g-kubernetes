#!/usr/bin/env python3
"""Stage5ZC: read-only post-NGSetup SIB1/BWP0 and RA liveness audit.

Only consumes Stage5R bounded category JSON. Diagnostic outputs are NOT
authoritative acceptance, packet-delivery proof, or production approval.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

SCHEMA = "hcran-stage5zc-post-idle-sib1-ra-v1"
OUTPUT = "stage5zc-sib1-ra-readiness.json"
CELLS = {
    "macro":{"ue":"ue","du":"macro","cu_cell":3804001,"amf_cell":0xe0101},
    "small":{"ue":"ue-small","du":"small","cu_cell":3804002,"amf_cell":0xe0102},
}
KEYS = (
    "nas_service_request","ue_rrc_mac_ra_triggered",
    "ue_ra_deferred_until_sib1","ue_phy_sync_started",
    "ue_phy_initial_sync_success","ue_phy_synchronized",
    "rrc_sib1_decoded","ue_prach_scheduled",
    "ue_mac_msg3_transmitted","ue_mac_ra_succeeded",
    "ue_rrc_setup_complete_generated",
    "nas_t3517_timeout","nas_retries_exhausted","nas_pdu_accept",
)
def _counts(rows):
    ct=Counter(x["event"] for x in rows)
    return {k:ct[k] for k in KEYS}

def _phase(rows):
    if not rows:
        return None
    t=min(x["elapsed_seconds"] for x in rows)
    return round(t,3)

def diagnose(root):
    root=Path(root)
    out={
        "schema":SCHEMA,
        "status":"INCOMPLETE_OBSERVATION",
        "production_authorized":False,
        "production_decision":"NO_GO",
        "limits":[
            "Observer timestamps are not clock-synchronized interface packet timestamps",
            "Missing log categories cannot prove protocol packet loss",
            "A DU PRACH-late warning may occur in passing and failing sessions",
            "No UE or CU state machines, retry budgets or control-plane behavior were modified",
        ],
        "cycles":[],
    }
    try:
        data=json.loads((root/"stage5r-protocol-timeline.json").read_text())
        if (data.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1"
                or data.get("stage5zb_radio_boundary_categories") is not True
                or data.get("stage5zc_sib1_reacquisition_categories") is not True
                or not isinstance(data.get("events"),list)):
            raise ValueError("Unqualified Stage5ZC source")
        complete=(data.get("collection_issues")==[] and
                  data.get("collection_limit_reached") is False)
        out["observation_complete"]=complete
        events=data["events"]
        starts=sorted((x for x in events if
            x.get("source")=="harness" and x.get("event")=="milestone" and
            x.get("stage")=="STAGE5P-CYCLE-START" and
            type(x.get("cycle")) is int),key=lambda x:x["elapsed_seconds"])
        for i,start in enumerate(starts):
            stop=starts[i+1]["elapsed_seconds"] if i+1<len(starts) else float("inf")
            segment=[e for e in events
                     if start["elapsed_seconds"]<=e.get("elapsed_seconds",-1)<stop]
            ng=next((e["elapsed_seconds"] for e in segment if
                e.get("source")=="harness" and e.get("event")=="milestone"
                and e.get("stage")=="T34-new-NGSetupResponse-after-AMF-restart"
                and e.get("status")=="PASS"),None)
            post=[e for e in segment if ng is not None
                  and e.get("elapsed_seconds",-1)>=ng]
            end=next((e.get("status") for e in reversed(segment) if
                e.get("source")=="harness" and e.get("event")=="milestone"
                and e.get("stage")=="STAGE5P-CYCLE"),"NOT_COMPLETE")
            cells={}
            for name,roles in CELLS.items():
                ue=[e for e in post if e.get("source")==roles["ue"]]
                du=[e for e in post if e.get("source")==roles["du"]]
                cu=[e for e in post if e.get("source")=="cu" and
                    e.get("cell_id")==roles["cu_cell"]]
                amf=[e for e in post if e.get("source")=="core" and
                     e.get("cell_id")==roles["amf_cell"]]
                ct=_counts(ue)
                late=sum(e.get("event")=="du_prach_job_in_past_warning" for e in du)
                receipt=sum(e.get("event")=="cu_rrc_setup_complete" for e in cu)
                ingress=sum(e.get("event")=="amf_initial_ue_cell" for e in amf)
                deferrals=[e for e in ue if e.get("event")=="ue_ra_deferred_until_sib1"]
                readiness=[{"mac_state":e.get("mac_state"),
                            "ul_bwp_count":e.get("ul_bwp_count"),
                            "dl_bwp_count":e.get("dl_bwp_count")}
                           for e in deferrals[:4]]
                if not complete or ng is None:
                    boundary="INCOMPLETE_OBSERVATION"
                elif ct["nas_pdu_accept"]:
                    boundary="FRESH_PDU_OBSERVED"
                elif (ct["ue_rrc_mac_ra_triggered"] and
                      ct["ue_ra_deferred_until_sib1"] and
                      not ct["rrc_sib1_decoded"] and
                      ct["nas_t3517_timeout"]):
                    boundary="DEFERRED_RA_NO_NEW_SIB1_BEFORE_NAS_TIMEOUT"
                elif ct["ue_rrc_mac_ra_triggered"] and not ct["rrc_sib1_decoded"]:
                    boundary="RA_TRIGGERED_NO_NEW_SIB1_OBSERVED"
                elif ct["rrc_sib1_decoded"] and not ct["ue_prach_scheduled"]:
                    boundary="SIB1_OBSERVED_BUT_NO_PRACH_SCHEDULE"
                elif ct["ue_prach_scheduled"] and not ct["ue_mac_msg3_transmitted"]:
                    boundary="PRACH_SCHEDULED_BUT_NO_MSG3"
                elif ct["ue_mac_msg3_transmitted"] and not ct["ue_rrc_setup_complete_generated"]:
                    boundary="MSG3_PRESENT_WITHOUT_RRC_COMPLETE_GENERATION"
                elif ct["ue_rrc_setup_complete_generated"] and not receipt:
                    boundary="RRC_COMPLETE_GENERATED_NO_CU_RECEIPT_MARKER"
                else:
                    boundary="INSUFFICIENT_TO_LOCALIZE"
                cells[name]={
                    "category_counts":ct,
                    "du_prach_late_warnings":late,
                    "cu_rrc_setup_complete_markers":receipt,
                    "amf_initial_ue_cell_markers":ingress,
                    "deferred_bwp_numeric_snapshots":readiness,
                    "diagnostic_boundary":boundary,
                    "observer_first_after_ngsetup_s":{
                        k: (round(_phase([e for e in ue if e.get("event")==k])-ng,3)
                            if _phase([e for e in ue if e.get("event")==k]) is not None else None)
                        for k in ("ue_rrc_mac_ra_triggered",
                                  "ue_ra_deferred_until_sib1",
                                  "ue_phy_initial_sync_success",
                                  "rrc_sib1_decoded","ue_prach_scheduled",
                                  "ue_rrc_setup_complete_generated",
                                  "nas_t3517_timeout")
                    },
                }
            out["cycles"].append({
                "cycle":start["cycle"],
                "new_ngsetup_observed":ng is not None,
                "genuine_recovery_status":end,
                "post_ngsetup_cells":cells,
            })
        if starts and complete:
            out["status"]="DIAGNOSTIC_ONLY"
    except (OSError,ValueError,TypeError,KeyError) as exc:
        out["status"]="INCOMPLETE_OBSERVATION"
        out["error_type"]=type(exc).__name__
    (root/OUTPUT).write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5ZC-SIB1-REACQUISITION",
                      "status":out["status"],"cycles":len(out["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    return out

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("evidence",type=Path)
    diagnose(p.parse_args().evidence)
