#!/usr/bin/env python3
"""Stage5U repeatability + evidence-negative controls for real Stage5T RFsim results.

Strictly OFFLINE evidence validation. No changes to UE/CU binary, radio config,
AMF, docker, production, Kubernetes or user's HP host. Generates deliberately
corrupted COPIES of real disposable-run evidence to demonstrate fail-closed
verification. Never copies raw logs or credentials into negative-test outputs.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"stage5s"))
from verify_three_cycle_recovery import verify as verify_three

CYCLE_FILES=(
    "stage5e-recovery-status.json", "stage5i-postfault-markers.json",
    "stage5i-runtime.json", "post-AMF-gtpu-evidence.json", "gtpu.pcap",
    "results.json"
)
TRIAL_ROOT_FILES=("stage5p-multicycle-summary.json","results.json","stage5r-protocol-timeline.json")
ROLES=("ue","ue-small")
REQUIRED_TRACE=("rrc_sib1_decoded","nas_suci_register","nas_registration_accept","nas_pdu_accept","tun_ipv4_refresh")
MAX_TRACE_EVENTS=1800

def jload(path):
    return json.loads(path.read_text())

def jwrite(path, object_):
    path.write_text(json.dumps(object_,indent=2)+"\n")

def verify_stage5u(root):
    root=Path(root)
    errors=[]
    ok, prior=verify_three(root)
    if not ok:
        errors.extend(["Stage5S existing strict three-cycle gate: "+x for x in prior])
    try:
        summary=jload(root/"stage5p-multicycle-summary.json")
        timeline=jload(root/"stage5r-protocol-timeline.json")
    except (OSError,ValueError,TypeError) as exc:
        return False,errors+["Stage5U missing/malformed timeline: "+type(exc).__name__]
    if not isinstance(summary,dict) or not isinstance(timeline,dict):
        return False,errors+["Stage5U invalid summary/trace structures"]
    if (timeline.get("schema")!="hcran-stage5r-bounded-protocol-observation-v1"
            or timeline.get("verdict_from_genuine_stage5p_harness")!="PASS"
            or timeline.get("production_authorized") is not False
            or timeline.get("production_decision")!="NO_GO"):
        errors.append("Stage5U protocol timeline is not a completed NO_GO research PASS")
    if timeline.get("collection_issues")!=[] or timeline.get("collection_limit_reached") is not False:
        errors.append("Stage5U incomplete/dropped bounded live observations")
    # Stage5V: distinguish intentional wire-header display sampling from
    # safety-critical UE/NAS/RRC event loss; never waive real event truncation.
    sampled=timeline.get("wire_header_sampling")
    if (not isinstance(sampled,dict) or
            sampled.get("all_header_packets_counted") is not True or
            sampled.get("required_ue_events_truncated") is not False or
            sampled.get("per_category_display_cap") != 100 or
            not isinstance(sampled.get("suppressed_header_only_samples"),dict)):
        errors.append("Stage5V missing or malformed header sampling audit")
    events=timeline.get("events")
    if not isinstance(events,list) or len(events)<50 or len(events)>MAX_TRACE_EVENTS:
        errors.append("Stage5U missing/overflowed diagnostic events")
    counts=timeline.get("category_counts")
    if not isinstance(counts,dict):
        errors.append("Stage5U category counts absent")
        counts={}
    for role in ROLES:
        byrole=counts.get(role,{})
        if not isinstance(byrole,dict):
            errors.append("Stage5U missing UE role "+role)
            continue
        for category in REQUIRED_TRACE:
            if not isinstance(byrole.get(category),int) or byrole[category]<3:
                errors.append(f"Stage5U {role} lacks >=3 real post-start {category} events")
        if byrole.get("nas_retries_exhausted",0)>0:
            errors.append("Stage5U retry exhaustion seen "+role)
        if byrole.get("mac_bwp_deferral",0)>0:
            errors.append("Stage5U unexpected empty-BWP RA guard deferral "+role)
    cycles=summary.get("cycles",[])
    if not isinstance(cycles,list) or len(cycles)!=3:
        errors.append("Stage5U wrong number of consecutive trials")
    else:
        ident=summary.get("ue_process_identity_sha256")
        for i,cyc in enumerate(cycles,1):
            if not isinstance(cyc,dict) or cyc.get("ue_process_identity_sha256")!=ident:
                errors.append(f"Stage5U cycle {i} UE identity changed")
                continue
            per=root/"cycles"/f"cycle-{i:02d}"
            try:
                original=jload(per/"stage5e-recovery-status.json")
                paths=jload(per/"stage5i-runtime.json").get("nas_flow_type_by_ue",{})
            except (OSError,ValueError,TypeError):
                errors.append(f"Stage5U cycle {i} required real postfault state missing")
                continue
            if original.get("both_ue_icmp_recovered") is not True or original.get("ues_were_restarted") is not False:
                errors.append(f"Stage5U cycle {i} lost unchanged-UE successful ICMP proof")
            if not isinstance(paths,dict) or set(paths)!=set(ROLES) or any(
                    x not in ("retry","direct_cause9") for x in paths.values()):
                errors.append(f"Stage5U cycle {i} NAS flow unqualified")
            if any(cyc.get("ra_guard_deferred_counts",{}).get(role)!=0 for role in ROLES):
                errors.append(f"Stage5U cycle {i} previously failing empty-BWP guard recurred")
    return not errors,errors

def make_minimal_copy(original, destination):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    for name in TRIAL_ROOT_FILES:
        shutil.copy2(Path(original)/name,destination/name)
    for i in range(1,4):
        cdir=destination/"cycles"/f"cycle-{i:02d}"
        cdir.mkdir(parents=True)
        for name in CYCLE_FILES:
            shutil.copy2(Path(original)/"cycles"/f"cycle-{i:02d}"/name,cdir/name)

def mutations():
    def remove_pcap(root):
        (root/"cycles/cycle-02/gtpu.pcap").unlink()
    def corrupt_pcap(root):
        p=root/"cycles/cycle-01/gtpu.pcap"
        b=bytearray(p.read_bytes()); b[-1]^=0x01;p.write_bytes(b)
    def stale_ip(root):
        p=root/"stage5p-multicycle-summary.json"
        x=jload(p);x["cycles"][2]["before_pdu_ips"]["ue"]="10.45.88.200";jwrite(p,x)
    def changed_identity(root):
        p=root/"stage5p-multicycle-summary.json"
        x=jload(p);x["cycles"][1]["ue_process_identity_sha256"]="f"*64;jwrite(p,x)
    def remove_cycle(root):
        p=root/"stage5p-multicycle-summary.json"
        x=jload(p);x["cycles"].pop();jwrite(p,x)
    def remove_nas_reject(root):
        p=root/"cycles/cycle-01/stage5i-postfault-markers.json"
        x=jload(p);chain=x["ue"]["ue-small"]["selected_ordered_chain"]
        x["ue"]["ue-small"]["selected_ordered_chain"]=[
            item for item in chain if "Received NAS Service Reject" not in item.get("marker","")]
        jwrite(p,x)
    def drop_f1u(root):
        p=root/"cycles/cycle-01/stage5i-runtime.json"
        x=jload(p);x["postfault_gtpu_evidence"]["small"]["G_PDU_counts"]["f1u_dl"]=0;jwrite(p,x)
    def remove_stage_event(root):
        p=root/"results.json"
        x=jload(p);x=[e for e in x if not(
            isinstance(e,dict) and e.get("stage")=="STAGE5P-CYCLE" and e.get("cycle")==2 and e.get("status")=="PASS")]
        jwrite(p,x)
    def remove_trace(root):
        (root/"stage5r-protocol-timeline.json").unlink()
    return {
        "missing_real_gtpu_pcap":remove_pcap,
        "corrupt_gtpu_digest":corrupt_pcap,
        "stale_before_pdu_ip":stale_ip,
        "changed_ue_process_identity":changed_identity,
        "missing_third_recovery_cycle":remove_cycle,
        "missing_nas_service_reject":remove_nas_reject,
        "missing_small_f1u_downlink":drop_f1u,
        "missing_per_cycle_runner_pass":remove_stage_event,
        "missing_bounded_protocol_trace":remove_trace,
    }

def run(root):
    root=Path(root).resolve()
    ok,issues=verify_stage5u(root)
    report={
        "schema":"hcran-stage5u-repeatability-negative-proof-v1",
        "source":"real-pass-recovery-evidence-on-isolated-github-runner",
        "base_evidence_pass":ok,
        "base_evidence_issues":issues[:15],
        "negative_controls":{},
        "production_authorized":False,
        "production_decision":"NO_GO",
        "trace_header_sampling_policy":"count-all-SCTP-headers-sample-first-100-per-kind; never omit UE/NAS/RRC",
    }
    if ok:
        with tempfile.TemporaryDirectory(prefix="hcran-stage5u-negative-") as td:
            for case,mutate in mutations().items():
                dest=Path(td)/case
                make_minimal_copy(root,dest)
                mutate(dest)
                valid,denials=verify_stage5u(dest)
                report["negative_controls"][case]={
                    "rejected":not valid,
                    "reasons_count":len(denials),
                }
    report["status"]="PASS" if ok and all(
        x["rejected"] for x in report["negative_controls"].values()
    ) and len(report["negative_controls"])==len(mutations()) else "FAIL"
    jwrite(root/"stage5u-negative-control-results.json",report)
    print(json.dumps(report,indent=2),flush=True)
    return report["status"]=="PASS"

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence",type=Path)
    args=ap.parse_args()
    raise SystemExit(0 if run(args.evidence) else 1)
