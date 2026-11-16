#!/usr/bin/env python3
"""Independent Stage5I fail-closed dual-UE postfault recovery evidence contract."""
import argparse
import hashlib
import json
from pathlib import Path
import re

RETRIED_FLOW=(
    "Generate initial NAS message: Service Request",
    "Stage5I: schedule one bounded Service Request retry",
    "Generate initial NAS message: Service Request",
    "Received NAS Service Reject message with cause",
    "Stage5G: cause #9, cleared stale GUTI and NAS security",
    "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE",
    "Received Registration Accept",
    "PDU Session Establishment Accept",
)
DIRECT_FLOW=(
    "Generate initial NAS message: Service Request",
    "Received NAS Service Reject message with cause",
    "Stage5G: cause #9, cleared stale GUTI and NAS security",
    "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE",
    "Received Registration Accept",
    "PDU Session Establishment Accept",
)
FLOW = RETRIED_FLOW  # Backward-compatible reference to the existing strict retried flow.
REQUIRED=("STAGE4O","STAGE5E","STAGE5I",
          "T31-AMF-actually-stopped","T34-new-NGSetupResponse-after-AMF-restart",
          "T37-both-F1U-and-NGU-data-paths-restored")
# Stage5E inherits Stage4P post-pcap gate, whose T37 is required.

def check(path):
    path=Path(path)
    errors=[]
    try:
        main=json.loads((path/"stage5i-runtime.json").read_text())
        markers=json.loads((path/"stage5i-postfault-markers.json").read_text())
        events=json.loads((path/"results.json").read_text())
        upstream=json.loads((path/"stage5e-recovery-status.json").read_text())
    except (OSError,ValueError,TypeError) as e:
        return False,["Missing/malformed real runtime evidence: "+str(e)]
    if not isinstance(main,dict) or not isinstance(markers,dict) or not isinstance(events,list):
        return False,["Wrong evidence structure types"]
    expected={
        "schema":"stage5i-two-UE-t3517-cause9-v1",
        "source":"disposable-real-OAI-W45-RFsim-dual-UE",
        "amf_only_fault":True,
        "two_ue_processes_unchanged":True,
        "cause9_re_registration_observed_for_both":True,
        "postfault_bidirectional_gtpu_verified_by_stage5e":True,
        "both_ue_icmp_recovered":True,
        "production_authorized":False,
        "production_decision":"NO_GO",
    }
    for key,expected_value in expected.items():
        if main.get(key)!=expected_value:
            errors.append("Stage5I invalid "+key)
    for stage in REQUIRED:
        if not any(isinstance(x,dict) and x.get("stage")==stage and x.get("status")=="PASS" for x in events):
            errors.append("Missing real "+stage+" PASS")
    validated_paths={}
    for role in ("ue","ue-small"):
        detail=markers.get("ue",{}).get(role,{})
        path_type=detail.get("recovery_path")
        validated_paths[role]=path_type
        expected_flow=(RETRIED_FLOW if path_type=="bounded_retry" else
                       DIRECT_FLOW if path_type=="direct_service_reject" else ())
        chain=detail.get("selected_ordered_chain")
        if not expected_flow or not isinstance(chain,list) or [x.get("marker") for x in chain if isinstance(x,dict)]!=list(expected_flow):
            errors.append("Missing ordered postfault direct/retried NAS flow for "+role)
        if detail.get("chain_missing_from"):
            errors.append("Incomplete NAS flow "+role)
        counts={x["marker"]:x["count"] for x in detail.get("marker_counts",[])
                if isinstance(x,dict) and isinstance(x.get("marker"),str)}
        retries=counts.get("Stage5I: schedule one bounded Service Request retry",0)
        requests=counts.get("Generate initial NAS message: Service Request",0)
        if path_type=="bounded_retry" and not (retries>=1 and requests>=2):
            errors.append("Missing required retried NAS requests "+role)
        elif path_type=="direct_service_reject" and not (retries==0 and requests==1):
            errors.append("Incorrectly classified direct NAS Service Reject "+role)
        elif path_type not in ("bounded_retry","direct_service_reject"):
            errors.append("No authenticated NAS Service Reject recovery path "+role)
        if counts.get("Stage5I: failed to arm recovery timer",0) or counts.get(
              "Stage5I: bounded Service Request attempts exhausted",0):
            errors.append("Retry failure/exhaustion "+role)
    if main.get("recovery_paths")!=validated_paths:
        errors.append("Runtime recovery path does not match observed NAS evidence")
    both_retry=all(validated_paths.get(role)=="bounded_retry" for role in ("ue","ue-small"))
    if main.get("bounded_service_request_retry_observed_for_both") is not both_retry:
        errors.append("False positive/negative bounded Service Request retry claim")
    if upstream.get("both_ue_icmp_recovered") is not True or upstream.get("ues_were_restarted") is not False:
        errors.append("Missing original unchanged-UE ICMP recovery proof")
    attempts=upstream.get("user_plane_probes")
    if not isinstance(attempts,list) or not attempts or not all(
        attempts[-1].get("probes",{}).get(role,{}).get("success") is True
        for role in ("ue","ue-small")):
        errors.append("Both postfault live user-plane probes not successful")
    f=path/"gtpu.pcap"
    digest=main.get("postfault_gtpu_pcap_sha256")
    if not f.is_file() or f.stat().st_size<150:
        errors.append("Missing real postfault GTPU PCAP")
    elif not isinstance(digest,str) or not re.fullmatch("[0-9a-f]{64}",digest) or hashlib.sha256(f.read_bytes()).hexdigest()!=digest:
        errors.append("Postfault PCAP SHA256 mismatch")
    result=main.get("postfault_gtpu_evidence")
    if not isinstance(result,dict) or any(k not in result for k in ("macro","small")):
        errors.append("Missing macro/small wire evidence")
    else:
        for role in ("macro","small"):
            counts=result.get(role,{}).get("G_PDU_counts",{})
            for direction in ("f1u_ul","f1u_dl"):
                if not isinstance(counts.get(direction),int) or counts[direction]<3:
                    errors.append(role+" "+direction+" insufficient GTP-U")
        counts=result.get("macro",{}).get("G_PDU_counts",{})
        for direction in ("ngu_ul","ngu_dl"):
            if not isinstance(counts.get(direction),int) or counts[direction]<6:
                errors.append(direction+" insufficient NG-U")
    if not (path/"post-AMF-gtpu-evidence.json").is_file():
        errors.append("Missing Stage5E postfault wire decoder proof")
    return not errors,errors

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("evidence",type=Path)
    args=ap.parse_args()
    ok,issues=check(args.evidence)
    print(json.dumps({"stage":"STAGE5I-EVIDENCE","status":"PASS" if ok else "FAIL",
                      "reasons":issues,"production_decision":"NO_GO"},indent=2))
    return 0 if ok else 1
if __name__=="__main__":
    raise SystemExit(main())
