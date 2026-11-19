#!/usr/bin/env python3
"""Stage5ZG diagnostic-only RRC SIB send/dispatch/MAC schedule chain.

Does not infer packet loss, identity binding or 3GPP conformance. The genuine
Stage5P recovery status remains authoritative and cannot be overwritten.
"""
import json
import argparse
from pathlib import Path

NAME="stage5zg-rrc-mac-si-message-handoff.json"
ROLES={"macro":"ue","small":"ue-small"}
SIGNALS=(
    "ue_rrc_si_schedule_decision",
    "ue_rrc_sib_request_sent",
    "ue_mac_sib_request_dispatched",
    "ue_sib1_scheduled",
    "rrc_sib1_decoded",
    "ue_sib1_mac_configured",
    "ue_ra_deferred_until_sib1",
    "ue_mac_mib_sampled",
    "nas_pdu_accept",
)
def build(root):
    root=Path(root)
    report={
        "schema":"hcran-stage5zg-rrc-mac-sib-message-boundary-v1",
        "status":"INCOMPLETE_OBSERVATION",
        "production_authorized":False,
        "production_decision":"NO_GO",
        "limitations":[
            "Category-only in-process logs, not proof of delivery/loss",
            "Log scheduling may perturb RFsim timing",
            "No per-message identity/correlation token; role/time-order only",
            "A missing marker is not a proof of missing message",
            "Real three-cycle Stage5P result and negative-control gates are untouched",
        ],
        "cycles":[],
    }
    try:
        d=json.loads((root/"stage5r-protocol-timeline.json").read_text())
        if (d.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1"
            or d.get("stage5zg_rrc_mac_sib_message_categories") is not True
            or not isinstance(d.get("events"),list)):
            raise ValueError("Unqualified Stage5ZG source")
        good=d.get("collection_issues")==[] and d.get("collection_limit_reached") is False
        report["observation_complete"]=good
        ev=d["events"]
        starts=sorted((e for e in ev
            if e.get("source")=="harness" and e.get("event")=="milestone"
            and e.get("stage")=="STAGE5P-CYCLE-START"
            and type(e.get("cycle")) is int),key=lambda e:e["elapsed_seconds"])
        for i,start in enumerate(starts):
            a=start["elapsed_seconds"]
            b=starts[i+1]["elapsed_seconds"] if i+1<len(starts) else float("inf")
            cycle=[e for e in ev if a<=e.get("elapsed_seconds",-1)<b]
            ng=next((e["elapsed_seconds"] for e in cycle
                if e.get("source")=="harness" and e.get("event")=="milestone"
                and e.get("stage")=="T34-new-NGSetupResponse-after-AMF-restart"
                and e.get("status")=="PASS"),None)
            verdict=next((e.get("status") for e in reversed(cycle)
                if e.get("source")=="harness" and e.get("event")=="milestone"
                and e.get("stage")=="STAGE5P-CYCLE"),"NOT_COMPLETE")
            cells={}
            for cell,role in ROLES.items():
                post=sorted([e for e in cycle
                    if e.get("source")==role and ng is not None and
                    e["elapsed_seconds"]>=ng and e.get("event") in SIGNALS],
                    key=lambda e:e["elapsed_seconds"])
                def select(category):
                    return [e for e in post if e.get("event")==category]
                req=[e for e in select("ue_rrc_si_schedule_decision")
                     if e.get("get_sib")==1]
                sent=select("ue_rrc_sib_request_sent")
                dispatch=select("ue_mac_sib_request_dispatched")
                scheduled=select("ue_sib1_scheduled")
                sib=select("rrc_sib1_decoded")
                bwp=select("ue_sib1_mac_configured")
                missing=[e for e in select("ue_mac_mib_sampled")
                         if e.get("ul_bwp")==0 and e.get("dl_bwp")==0]
                pdu=select("nas_pdu_accept")
                # The last unpaired SEND marker in the post-NGSetup window is a
                # candidate only. Earlier successful dispatch never supplies a
                # later missing send; no subscriber or per-message correlation.
                no_dispatch=[
                    e for e in sent
                    if not any(d["elapsed_seconds"] >= e["elapsed_seconds"]
                               and d.get("get_sib")==e.get("get_sib") for d in dispatch)]
                no_schedule=[
                    e for e in dispatch
                    if not any(x["elapsed_seconds"]>=e["elapsed_seconds"] for x in scheduled)]
                if not good or ng is None:
                    label="INCOMPLETE_OBSERVATION"
                elif pdu:
                    label="FRESH_PDU_OBSERVED"
                elif no_dispatch and missing:
                    label="RRC_SIB_SENT_NO_LATER_MAC_DISPATCH_MARKER_WITH_BWP_GAP"
                elif no_schedule and missing:
                    label="MAC_SIB_DISPATCH_NO_LATER_SIB_SCHEDULE_MARKER_WITH_BWP_GAP"
                elif req and not sent and missing:
                    label="RRC_SIB_REQUEST_DECISION_NO_SEND_MARKER_WITH_BWP_GAP"
                elif sent and not scheduled and missing:
                    label="RRC_SIB_SENT_NO_SCHEDULE_MARKER_WITH_BWP_GAP"
                elif any(select(x) for x in ("ue_ra_deferred_until_sib1",)):
                    label="RA_DEFERRED_BUT_SIB_MESSAGE_BOUNDARY_INCONCLUSIVE"
                else:
                    label="INSUFFICIENT_TO_LOCALIZE"
                cells[cell]={
                    "post_ngsetup_counts":{k:len(select(k)) for k in SIGNALS},
                    "first_post_ngsetup_seconds":{
                        k:(round(select(k)[0]["elapsed_seconds"]-ng,3)
                           if select(k) else None)
                        for k in SIGNALS
                    },
                    "sampled_rrc_requests":[
                        {"get_sib":e.get("get_sib"),
                         "seconds_after_ngsetup":round(e["elapsed_seconds"]-ng,3)}
                        for e in sent[:12]],
                    "sampled_mac_dispatches":[
                        {"get_sib":e.get("get_sib"),
                         "seconds_after_ngsetup":round(e["elapsed_seconds"]-ng,3)}
                        for e in dispatch[:12]],
                    "unmatched_rrc_send_marker_count":len(no_dispatch),
                    "unmatched_mac_dispatch_marker_count":len(no_schedule),
                    "diagnostic_boundary":label,
                }
            report["cycles"].append({
                "cycle":start["cycle"],"genuine_recovery_status":verdict,
                "new_ngsetup_response_observed":ng is not None,
                "cells":cells
            })
        if starts and good:
            report["status"]="DIAGNOSTIC_ONLY"
    except (OSError,TypeError,KeyError,ValueError) as exc:
        report["error_type"]=type(exc).__name__
    (root/NAME).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5ZG-RRC-MAC-SIB-HANDOFF",
                      "status":report["status"],
                      "cycles":len(report["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    return report

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("evidence",type=Path)
    build(p.parse_args().evidence)
