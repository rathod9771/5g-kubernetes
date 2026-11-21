#!/usr/bin/env python3
"""Fail-closed real srsUE/ZMQ/NGAP initial-UE smoke evidence. No subscriber credentials emitted."""
import json
import pathlib
import re
import subprocess
import sys
import hashlib

def require(x,code):
    if not x:
        raise ValueError(code)

def run(pcap,ue_log,gnb_log):
    require(24 < pcap.stat().st_size < 6_000_000, "PCAP_MISSING_OR_TOO_LARGE")
    fields = ["ngap.NGSetupRequest_element","ngap.NGSetupResponse_element",
              "ngap.NGSetupFailure_element","ngap.InitialUEMessage_element"]
    cmd = ["tshark","-n","-r",str(pcap),"-Y","ngap","-T","fields",
           "-E","separator=/t"]
    for f in fields:
        cmd.extend(["-e", f])
    out = subprocess.run(cmd, capture_output=True,text=True,timeout=30)
    require(out.returncode == 0 and len(out.stdout) < 500000, "NGAP_DECODER_UNAVAILABLE")
    signals = {k:0 for k in fields}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        require(len(parts) == len(fields), "BAD_NGAP_FIELD_INVENTORY")
        flags = [i for i,p in enumerate(parts) if p]
        if len(flags) == 1:
            signals[fields[flags[0]]] += 1
    ue = ue_log.read_text(errors="replace")[:250000] if ue_log.is_file() else ""
    gnb = gnb_log.read_text(errors="replace")[:250000] if gnb_log.is_file() else ""
    rrc_marker = bool(re.search(r"RRC\s*Connected|RRC\s*Setup\s*Complete|RRCSetupComplete",ue,re.I))
    gnb_rrc_marker = bool(re.search(r"RRC\s*Setup\s*Complete|RRCSetupComplete",gnb,re.I))
    ng_setup = all(signals["ngap."+x+"_element"] > 0 for x in ("NGSetupRequest","NGSetupResponse"))
    initial = signals["ngap.InitialUEMessage_element"] > 0
    pcap_sha256 = hashlib.sha256(pcap.read_bytes()).hexdigest()
    verified = ng_setup and initial and (rrc_marker or gnb_rrc_marker)
    return {
        "schema":1,"classification":"PASS_REAL_SRSUE_RRC_AND_INITIAL_NGAP_MESSAGE_ONLY" if verified else "NOT_VERIFIED_RRC_OR_INITIAL_UE_MESSAGE",
        "source":"ISOLATED_SRSRAN_GNB_SRSUE_ZMQ_OPEN5GS_AMF_NRF",
        "ngap_setup_request_observed":signals["ngap.NGSetupRequest_element"]>0,
        "ngap_setup_response_observed":signals["ngap.NGSetupResponse_element"]>0,
        "ngap_setup_failure_observed":signals["ngap.NGSetupFailure_element"]>0,
        "ngap_initial_ue_message_observed":initial,
        "srsue_rrc_setup_completion_log_observed":rrc_marker,
        "gnb_rrc_setup_completion_log_observed":gnb_rrc_marker,
        "ngap_initial_ue_messages":signals["ngap.InitialUEMessage_element"],
        "pcap_sha256":pcap_sha256,
        "rrc_ts_38_331":"LIMITED_INITIAL_ACCESS_OBSERVATION_ONLY" if verified else "NOT_VERIFIED",
        "ts_23_502_ue_registration_accept":"NOT_TESTED_FULL_5GC_ABSENT",
        "ts_23_502_pdu_session":"NOT_TESTED_FULL_5GC_ABSENT",
        "ngap_pdu_session_resource_procedures":"NOT_TESTED_FULL_5GC_ABSENT",
        "ts_28_552":"NOT_VERIFIED_NO_VALIDATED_COUNTERS",
        "full_3gpp_conformance":"NOT_CLAIMED",
        "production_approval":"NO_GO",
        "main_hp_oai_hcran_modified":False
    }

def main():
    require(len(sys.argv) == 5, "USAGE: report_ue_rrc.py PCAP UE_LOG GNB_LOG OUTPUT")
    paths = [pathlib.Path(p) for p in sys.argv[1:]]
    require(not paths[3].exists(),"OUTPUT_EXISTS")
    r = run(*paths[:3])
    paths[3].write_text(json.dumps(r,indent=2,sort_keys=True)+"\n")
    print(r["classification"])
    require(r["classification"].startswith("PASS_"),"PROTOCOL_RRC_EVIDENCE_NOT_ESTABLISHED")

if __name__ == "__main__":
    try:
        main()
    except (ValueError,OSError,subprocess.TimeoutExpired) as e:
        print("SRSUE_RRC_EVIDENCE_BLOCKED:"+type(e).__name__+":"+str(e),file=sys.stderr)
        sys.exit(1)
