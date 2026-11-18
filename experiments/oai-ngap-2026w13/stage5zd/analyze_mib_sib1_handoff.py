#!/usr/bin/env python3
"""Stage5ZD diagnostic-only PBCH/MIB/SIB1 handoff correlation by AMF cycle.

Fail closed on malformed, missing or truncated bounded Stage5R sources. Never
amend genuine Stage5P verdict or equate omitted log events to packet loss.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

REPORT="stage5zd-mib-sib1-handoff.json"
ROLES=("ue","ue-small")
MARKERS=(
    "ue_phy_sync_started","ue_phy_pbch_decode","ue_phy_pbch_rx_ok",
    "ue_phy_initial_sync_success","ue_phy_synchronized",
    "ue_mib_configured","ue_sib1_scheduled","rrc_sib1_decoded",
    "ue_sib1_mac_configured","ue_ra_deferred_until_sib1",
    "ue_prach_scheduled","ue_mac_msg3_transmitted",
    "ue_rrc_setup_complete_generated","nas_t3517_timeout",
    "nas_retries_exhausted","nas_pdu_accept",
)
def counts(rows):
    ct=Counter(e["event"] for e in rows)
    return {k:ct[k] for k in MARKERS}

def build(root):
    root=Path(root)
    out={
        "schema":"hcran-stage5zd-mib-sib1-transition-diagnostic-v1",
        "status":"INCOMPLETE_OBSERVATION",
        "production_decision":"NO_GO","production_authorized":False,
        "limits":[
            "Logs are category-level, sampled, and not packet or 3GPP conformance proofs",
            "Absent categories cannot establish absence of UE or CU protocol messages",
            "Compilation adds LOG_I calls but does not alter native radio state transitions",
            "This report never overrides real Stage5P recovery outcome",
        ],"cycles":[],
    }
    try:
        d=json.loads((root/"stage5r-protocol-timeline.json").read_text())
        if (d.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1"
            or d.get("stage5zd_mib_sib1_handoff_categories") is not True
            or not isinstance(d.get("events"),list)):
            raise ValueError("Unqualified Stage5ZD source")
        clean=(d.get("collection_limit_reached") is False and
               d.get("collection_issues")==[])
        out["observation_complete"]=clean
        ev=d["events"]
        starts=sorted((e for e in ev if e.get("source")=="harness" and
                       e.get("event")=="milestone" and
                       e.get("stage")=="STAGE5P-CYCLE-START" and
                       type(e.get("cycle")) is int),
                      key=lambda e:e["elapsed_seconds"])
        for i,start in enumerate(starts):
            low=start["elapsed_seconds"]
            high=starts[i+1]["elapsed_seconds"] if i+1<len(starts) else float("inf")
            cycle=[e for e in ev if low<=e.get("elapsed_seconds",-1)<high]
            ng=next((e["elapsed_seconds"] for e in cycle if
                e.get("source")=="harness" and e.get("event")=="milestone" and
                e.get("stage")=="T34-new-NGSetupResponse-after-AMF-restart" and
                e.get("status")=="PASS"),None)
            verdict=next((e.get("status") for e in reversed(cycle) if
                e.get("source")=="harness" and e.get("event")=="milestone" and
                e.get("stage")=="STAGE5P-CYCLE"),"NOT_COMPLETE")
            roles={}
            for role in ROLES:
                before=[e for e in cycle if e.get("source")==role and
                        ng is not None and e["elapsed_seconds"] < ng]
                after=[e for e in cycle if e.get("source")==role and
                       ng is not None and e["elapsed_seconds"] >= ng]
                all_events=before+after
                c=counts(all_events)
                cpost=counts(after)
                if not clean or ng is None:
                    boundary="INCOMPLETE_OBSERVATION"
                elif cpost["nas_pdu_accept"]:
                    boundary="FRESH_PDU_OBSERVED"
                elif c["ue_phy_pbch_decode"] and not c["ue_mib_configured"]:
                    boundary="PBCH_DECODE_NO_MAC_MIB_CONFIG_MARKER"
                elif c["ue_mib_configured"] and not c["ue_sib1_scheduled"]:
                    boundary="MAC_MIB_CONFIG_NO_SIB1_SCHEDULE_MARKER"
                elif c["ue_sib1_scheduled"] and not c["rrc_sib1_decoded"]:
                    boundary="SIB1_SCHEDULED_NO_RRC_SIB1_DECODE_MARKER"
                elif c["rrc_sib1_decoded"] and not c["ue_sib1_mac_configured"]:
                    boundary="RRC_SIB1_DECODE_NO_MAC_SIB1_CONFIG_MARKER"
                elif cpost["ue_ra_deferred_until_sib1"] and not cpost["ue_prach_scheduled"]:
                    boundary="POST_NGSETUP_RA_DEFERRED_WITHOUT_PRACH_MARKER"
                else:
                    boundary="INSUFFICIENT_TO_LOCALIZE"
                def event_t(key):
                    es=[e["elapsed_seconds"] for e in all_events if e.get("event")==key]
                    return round(min(es)-low,3) if es else None
                roles[role]={
                    "before_ngsetup_counts":counts(before),
                    "after_ngsetup_counts":cpost,
                    "cycle_relative_first_marker_s":{k:event_t(k)
                        for k in ("ue_phy_sync_started","ue_phy_pbch_decode",
                                  "ue_phy_initial_sync_success","ue_mib_configured",
                                  "ue_sib1_scheduled","rrc_sib1_decoded",
                                  "ue_sib1_mac_configured")},
                    "bounded_mac_state_snapshots":[
                        {k:e[k] for k in ("state","mib_present","get_sib1",
                                          "ul_bwp","dl_bwp","current_ul",
                                          "current_dl") if k in e}
                        for e in all_events if e.get("event") in
                        ("ue_mib_configured","ue_sib1_scheduled","ue_sib1_mac_configured")
                    ][:12],
                    "diagnostic_boundary":boundary,
                }
            out["cycles"].append({
                "cycle":start["cycle"],
                "new_ngsetup_observed":ng is not None,
                "genuine_recovery_status":verdict,
                "cells":{"macro":roles["ue"],"small":roles["ue-small"]},
            })
        if starts and clean:
            out["status"]="DIAGNOSTIC_ONLY"
    except (OSError,ValueError,TypeError,KeyError) as exc:
        out["status"]="INCOMPLETE_OBSERVATION"
        out["error_type"]=type(exc).__name__
    (root/REPORT).write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({"stage":"STAGE5ZD-MIB-SIB1-HANDOFF",
                      "status":out["status"],"cycles":len(out["cycles"]),
                      "production_decision":"NO_GO"}),flush=True)
    return out

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("evidence",type=Path)
    build(p.parse_args().evidence)
