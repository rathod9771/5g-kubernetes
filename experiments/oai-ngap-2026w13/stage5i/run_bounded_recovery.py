#!/usr/bin/env python3
"""Stage5I: two real synthetic UEs, bounded retry, cause-9 re-registration.

Uses frozen Stage4P RFsim baseline and existing Stage5E real two-UE AMF fault
harness. No synthetic PASS, no manual UE restart, no production cluster.
Captures marker-only NAS evidence, not raw credentials or IMSIs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import types

FLOW=(
    "Generate initial NAS message: Service Request",
    "Stage5I: schedule one bounded Service Request retry",
    "Generate initial NAS message: Service Request",
    "Received NAS Service Reject message with cause",
    "Stage5G: cause #9, cleared stale GUTI and NAS security",
    "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE",
    "Received Registration Accept",
    "PDU Session Establishment Accept",
)
DIAGNOSTICS=(
    "RRC moved into IDLE state",
    "NR_NAS_CONN_RELEASE_IND",
    "Stage5I: T3517-like timer armed",
    "Stage5I: T3517-like expiry",
    "Stage5I: schedule one bounded Service Request retry",
    "Stage5I: bounded Service Request attempts exhausted",
    "Stage5I: failed to arm recovery timer",
    "Generate initial NAS message: Service Request",
    "Received NAS Service Reject message with cause",
    "Stage5G: cause #9",
    "Stage5G: sent SUCI-based initial Registration Request",
    "Received Registration Accept",
    "PDU Session Establishment Accept",
)

def ordered(log, flow):
    position=0
    missing=[]
    selected=[]
    for marker in flow:
        ix=log.find(marker, position)
        if ix<0:
            missing.append(marker)
            break
        selected.append({"marker":marker,"offset_chars":ix})
        position=ix+len(marker)
    return selected,missing

def build(opts):
    repo=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(repo/"stage5e"))
    from run_idle_service_recovery import make_probe
    p=make_probe(opts.baseline,opts)
    original_fault=p.fault_and_recover
    original_cleanup=p.cleanup

    def diagnostic(self):
        offsets=getattr(self,"_stage5i_offsets",{})
        record={"schema":"stage5i-postfault-marker-evidence-v1",
                "production_decision":"NO_GO","ue":{}}
        for role in ("ue","ue-small"):
            try:
                log=self.logs(role)[offsets.get(role,len(self.logs(role))):]
                occurrences=[]
                for marker in DIAGNOSTICS:
                    count=log.count(marker)
                    occurrences.append({"marker":marker,"count":count})
                chain,missing=ordered(log,FLOW)
                record["ue"][role]={
                    "selected_ordered_chain":chain,
                    "chain_missing_from":missing,
                    "marker_counts":occurrences,
                    "original_postfault_chars":len(log)}
            except Exception as e:
                record["ue"][role]={"capture_error":type(e).__name__}
        (self.out/"stage5i-postfault-markers.json").write_text(
            json.dumps(record,indent=2)+"\n")
        return record

    def fault(self):
        self._stage5i_offsets={r:len(self.logs(r)) for r in ("ue","ue-small")}
        original_fault()
        proof=diagnostic(self)
        for role in ("ue","ue-small"):
            obj=proof["ue"].get(role,{})
            if obj.get("chain_missing_from"):
                raise RuntimeError("Stage5I "+role+" missing ordered postfault NAS event: "+
                                   str(obj["chain_missing_from"][0]))
            if len(obj.get("selected_ordered_chain",[]))!=len(FLOW):
                raise RuntimeError("Stage5I "+role+" missing protocol-level cause9 and bounded retry evidence")
            if any(x["count"] for x in obj.get("marker_counts",[]) if
                   x["marker"] in ("Stage5I: bounded Service Request attempts exhausted",
                                   "Stage5I: failed to arm recovery timer")):
                raise RuntimeError("Stage5I "+role+" exhausted retry budget or timer failed")
        old=json.loads((self.out/"stage5e-recovery-status.json").read_text())
        if not old.get("both_ue_icmp_recovered"):
            raise RuntimeError("Stage5I missing real postfault ICMP for both UEs")
        pcap=self.out/"gtpu.pcap"
        gtpu=self.out/"post-AMF-gtpu-evidence.json"
        if not pcap.is_file() or pcap.stat().st_size<150 or not gtpu.is_file():
            raise RuntimeError("Stage5I missing real postfault GTPU proof")
        record={
            "schema":"stage5i-two-UE-t3517-cause9-v1",
            "source":"disposable-real-OAI-W45-RFsim-dual-UE",
            "amf_only_fault":True,
            "two_ue_processes_unchanged":True,
            "cause9_re_registration_observed_for_both":True,
            "bounded_service_request_retry_observed_for_both":True,
            "postfault_bidirectional_gtpu_verified_by_stage5e":True,
            "postfault_gtpu_pcap_sha256":hashlib.sha256(pcap.read_bytes()).hexdigest(),
            "postfault_gtpu_evidence":json.loads(gtpu.read_text()),
            "both_ue_icmp_recovered":True,
            "production_authorized":False,
            "production_decision":"NO_GO",
        }
        (self.out/"stage5i-runtime.json").write_text(json.dumps(record,indent=2)+"\n")
        self.record("STAGE5I","PASS",scope="both unchanged UEs: bounded SR retry, cause9->SUCI NAS/PDU and bidirectional F1U/NGU",production_decision="NO_GO")

    def cleanup(self):
        try:diagnostic(self)
        except Exception:pass
        original_cleanup()
    p.fault_and_recover=types.MethodType(fault,p)
    p.cleanup=types.MethodType(cleanup,p)
    return p

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for name in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+name,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    opts=p.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: real postfault F1-U and NG-U GTPU mandatory")
    return build(opts).run()

if __name__=="__main__":
    raise SystemExit(main())
