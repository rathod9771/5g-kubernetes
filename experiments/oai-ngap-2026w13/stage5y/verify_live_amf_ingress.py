#!/usr/bin/env python3
"""Stage5Y: independently require LIVE per-cell AMF ingress coverage.

Do not confuse a successful two-UE service recovery with a complete
AMF-ingress observer. Stage5X 12/12 genuine RFsim recovery qualified the
former, but its amf-live stream was missing. The Stage5Y gate must find
a bounded AMF InitialUEMessage *cell category* for both synthetic
cells, for EACH post-fault cycle, alongside actual CU RRC and UE NAS/PDU
evidence. Classification is cell-level, not per-SUPI message identity.
This never grants commercial conformance or production authorization.
"""
import argparse
import json
from pathlib import Path

CELLS=("ue","ue-small")
REQUIRED_COUNT_FIELDS=(
    "ue_service_request_markers", "ue_fresh_pdu_accept_markers",
    "cu_rrc_setup_complete_cell_markers", "amf_initial_ue_cell_markers"
)

def check(root):
    root=Path(root)
    errors=[]
    try:
        gap=json.loads((root/"stage5x-f1c-amf-ingress-gap.json").read_text())
        truth=json.loads((root/"stage5p-multicycle-summary.json").read_text())
        trace=json.loads((root/"stage5r-protocol-timeline.json").read_text())
        negative=json.loads((root/"stage5u-negative-control-results.json").read_text())
    except (OSError,ValueError,TypeError) as exc:
        return False,["Missing Stage5Y genuine source evidence: "+type(exc).__name__]
    if not all(isinstance(x,dict) for x in (gap,truth,trace,negative)):
        return False,["Malformed Stage5Y source evidence structure"]
    if (gap.get("schema")!="hcran-stage5x-cu-f1c-amf-ingress-v1"
            or gap.get("status")!="DIAGNOSTIC_ONLY"
            or gap.get("trace_complete") is not True
            or gap.get("live_cu_amf_streams_present") is not True
            or gap.get("production_authorized") is not False
            or gap.get("production_decision")!="NO_GO"):
        errors.append("Incomplete Stage5X live CU/AMF synthetic per-cell sources")
    if (truth.get("status")!="PASS" or truth.get("requested_cycles")!=3
            or truth.get("production_authorized") is not False
            or len(truth.get("cycles",[]))!=3
            or negative.get("status")!="PASS"
            or len(negative.get("negative_controls",{}))!=9
            or not all(isinstance(x,dict) and x.get("rejected") is True
                       for x in negative.get("negative_controls",{}).values())):
        errors.append("Authentic three-cycle NAS/TUN/ICMP/PCAP or negative gate incomplete")
    if (trace.get("collection_issues")!=[]
            or trace.get("collection_limit_reached") is not False
            or trace.get("wire_header_sampling",{}).get("required_ue_events_truncated") is not False):
        errors.append("Bounded AMF/CU trace incomplete")
    counts=trace.get("category_counts",{})
    if (not isinstance(counts,dict)
            or counts.get("core",{}).get("amf_initial_ue_cell",0)<6):
        errors.append("Missing >=6 observed per-cell AMF InitialUEMessage categories")
    items=gap.get("cycles")
    if not isinstance(items,list) or len(items)!=3:
        return False,errors+["Exactly three complete per-cycle AMF ingress records required"]
    for i,item in enumerate(items,1):
        if not isinstance(item,dict):
            errors.append(f"Cycle {i} invalid source")
            continue
        if (item.get("cycle")!=i or item.get("genuine_recovery_status")!="PASS"
                or item.get("amf_restart_observed") is not True
                or item.get("new_ngsetup_response_observed") is not True):
            errors.append(f"Cycle {i} missing real AMF restart/NGSetup/recovery")
        roles=item.get("ue")
        if not isinstance(roles,dict) or set(roles)!=set(CELLS):
            errors.append(f"Cycle {i} missing independent macro/small roles")
            continue
        for role in CELLS:
            r=roles[role]
            if not isinstance(r,dict):
                errors.append(f"Cycle {i} invalid {role} evidence")
                continue
            for field in REQUIRED_COUNT_FIELDS:
                if type(r.get(field)) is not int or r[field]<1:
                    errors.append(f"Cycle {i} {role} missing {field}")
            if r.get("gap_classification")!="FRESH_PDU_OBSERVED":
                errors.append(f"Cycle {i} {role} no fresh PDU event")
            if r.get("ue_bwp_deferral_markers")!=0 or r.get("ue_nas_retries_exhausted")!=0:
                errors.append(f"Cycle {i} {role} MAC/NAS failure event")
    return not errors,errors

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence",type=Path)
    root=ap.parse_args().evidence
    ok,errors=check(root)
    report={
        "schema":"hcran-stage5y-live-amf-per-cell-gate-v1",
        "status":"PASS" if ok else "FAIL",
        "scope":"disposable-3-cycle-2UE-2DU-synthetic-AMF-cell-level",
        "no_delivery_claim":"AMF log cell categories do not cryptographically bind an InitialUEMessage to a subscriber; they support cell-level ingress only",
        "reasons":errors[:25],
        "production_authorized":False,
        "production_decision":"NO_GO",
    }
    (root/"stage5y-amf-cell-gate.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2),flush=True)
    return 0 if ok else 1

if __name__=="__main__":
    raise SystemExit(main())
