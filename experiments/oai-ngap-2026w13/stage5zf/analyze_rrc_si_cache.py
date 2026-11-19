#!/usr/bin/env python3
"""Stage5ZF fail-closed RRC SI cache vs MAC BWP0 observation audit.

Research-only bounded log categories. Independent Stage5P recovery verdict is
authoritative. No inference of subscriber identity, 3GPP conformance or
packet-level delivery; sample gaps prevent root-cause certainty.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

OUTPUT="stage5zf-si-cache-bwp0-gap.json"
ROLES=("ue","ue-small")
def _counter(events):
    return dict(Counter(e["event"] for e in events))
def build(root):
    root=Path(root)
    out={
        "schema":"hcran-stage5zf-rrc-si-cache-mac-bwp0-v1",
        "status":"INCOMPLETE_OBSERVATION",
        "production_authorized":False,
        "production_decision":"NO_GO",
        "limitations":[
            "Power-of-two MIB decisions are sampled, not exhaustive",
            "Co-occurrence of RRC SI validity and missing MAC BWP0 is not causal proof",
            "Log absence cannot prove undelivered MIB, SIB1 or F1AP packets",
            "Real Stage5P/5Y acceptance remains independent and fail closed",
        ],
        "cycles":[],
    }
    try:
        d=json.loads((root/"stage5r-protocol-timeline.json").read_text())
        if (d.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1"
            or d.get("stage5zf_rrc_si_schedule_decision_categories") is not True
            or not isinstance(d.get("events"),list)):
            raise ValueError("Unqualified Stage5ZF source")
        complete=(d.get("collection_limit_reached") is False and
                  d.get("collection_issues")==[])
        out["observation_complete"]=complete
        events=d["events"]
        starts=sorted((e for e in events
            if e.get("source")=="harness" and e.get("event")=="milestone"
            and e.get("stage")=="STAGE5P-CYCLE-START"
            and type(e.get("cycle")) is int),key=lambda e:e["elapsed_seconds"])
        for i,start in enumerate(starts):
            low=start["elapsed_seconds"]
            high=starts[i+1]["elapsed_seconds"] if i+1<len(starts) else float("inf")
            window=[e for e in events if low<=e.get("elapsed_seconds",-1)<high]
            ng=next((e["elapsed_seconds"] for e in window
                if e.get("source")=="harness" and e.get("event")=="milestone"
                and e.get("stage")=="T34-new-NGSetupResponse-after-AMF-restart"
                and e.get("status")=="PASS"),None)
            verdict=next((e.get("status") for e in reversed(window)
                if e.get("source")=="harness" and e.get("event")=="milestone"
                and e.get("stage")=="STAGE5P-CYCLE"),"NOT_COMPLETE")
            cells={}
            for role in ROLES:
                ev=[e for e in window if e.get("source")==role]
                prior=[e for e in ev if ng is not None and e["elapsed_seconds"]<ng]
                later=[e for e in ev if ng is not None and e["elapsed_seconds"]>=ng]
                def decision(rows):
                    return [e for e in rows if e.get("event")=="ue_rrc_si_schedule_decision"]
                def mac_mib(rows):
                    return [e for e in rows if e.get("event")=="ue_mac_mib_sampled"]
                rr=decision(ev)
                mm=mac_mib(ev)
                cache=[e for e in rr
                       if e.get("sib1_valid")==1 and e.get("get_sib")==0
                       and e.get("sib_pending")==0]
                missing=[e for e in mm if e.get("ul_bwp")==0 and e.get("dl_bwp")==0]
                if not complete or ng is None:
                    label="INCOMPLETE_OBSERVATION"
                elif any(e.get("event")=="nas_pdu_accept" for e in later):
                    label="FRESH_PDU_OBSERVED"
                elif cache and missing and not any(
                      e.get("event")=="ue_sib1_scheduled" for e in later):
                    label="SI_VALID_NO_SIB1_SCHEDULE_WITH_MAC_BWP0_ABSENT_CANDIDATE"
                elif any(e.get("event")=="ue_ra_deferred_until_sib1" for e in later):
                    label="RA_DEFERRED_WITHOUT_CONCLUSIVE_RRC_SI_CACHE_CAUSE"
                else:
                    label="INSUFFICIENT_EVIDENCE"
                cells["macro" if role=="ue" else "small"]={
                    "category_counts_total":_counter(ev),
                    "category_counts_before_ngsetup":_counter(prior),
                    "category_counts_after_ngsetup":_counter(later),
                    "sampled_si_decisions":[
                        {k:e[k] for k in ("sample_n","sib1_valid","sib_pending",
                                          "get_sib","rrc_state","barred") if k in e}
                        for e in rr[:20]
                    ],
                    "sampled_mac_mib":[
                        {k:e[k] for k in ("sample_n","mac_state","mib_present",
                                          "get_sib1","ul_bwp","dl_bwp") if k in e}
                        for e in mm[:20]
                    ],
                    "si_valid_no_sib1_request_sample_count":len(cache),
                    "mac_missing_bwp0_sample_count":len(missing),
                    "diagnostic_hypothesis":label,
                }
            out["cycles"].append({
                "cycle":start["cycle"],"genuine_recovery_status":verdict,
                "restored_ngsetup_observed":ng is not None,"cells":cells,
            })
        if starts and complete:
            out["status"]="DIAGNOSTIC_ONLY"
    except (OSError,ValueError,TypeError,KeyError) as exc:
        out["status"]="INCOMPLETE_OBSERVATION"
        out["error_type"]=type(exc).__name__
    (root/OUTPUT).write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5ZF-RRC-SI-MAC-BWP0",
                      "status":out["status"],"cycles":len(out["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    return out

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence",type=Path)
    build(ap.parse_args().evidence)
