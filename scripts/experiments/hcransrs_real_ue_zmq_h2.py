#!/usr/bin/env python3
"""H-CRAN H2 research: actual pinned srsRAN macro DU to actual pinned OAI NR UE.

Run exclusively on disposable GitHub-hosted runner. Reuses H1 CU/AMF/F1
harness, overlays only macro ZMQ endpoint addresses in runner-local config,
starts actual OAI NR UE binary on runner, and inspects actual TCP payloads.
No subscriber provisioned; no actual NAS/PDU, mobility, recovery claims.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

import hcransrs_ngf1 as h1

BRIDGE_ADDRESS="172.29.78.1"
DU_ADDRESS=h1.IP["macro"]
ZMQ_DL_PORT=2000
ZMQ_UL_PORT=2001
UE_SECONDS=65
NOT_VALIDATED=[
    "NR_SIB1_DECODE", "RRC_ESTABLISHMENT", "NAS_REGISTRATION",
    "PDU_SESSION_ESTABLISHMENT", "F1U_TRAFFIC", "NGU_TRAFFIC",
    "INTER_DU_MOBILITY", "RECOVERY", "QOS_SESSION_MODIFICATION",
]


def overlay_config(cfg,role):
    if role != "macro":
        return cfg
    args=cfg["ru_sdr"]["device_args"]
    exact="tx_port=tcp://127.0.0.1:2000,rx_port=tcp://127.0.0.1:2001"
    if args != exact:
        raise h1.EvidenceError("Unexpected canonical macro DU ZMQ config")
    cfg["ru_sdr"]["device_args"]=(
        "tx_port=tcp://0.0.0.0:2000,rx_port=tcp://172.29.78.1:2001"
    )
    return cfg


def ue_command(executable):
    return [
        str(executable), "-r","51","--numerology","1","--band","78",
        "-C","3489420000","--ssb","0","-E",
        "--uicc0.imsi","208930100001100",
        "--device.name","oai_zmqdevif",
        "--zmq.[0].tx_channels","tcp://0.0.0.0:2001",
        "--zmq.[0].rx_channels",f"tcp://{DU_ADDRESS}:2000",
    ]


def frames(pcap, expr):
    if not pcap.is_file() or pcap.is_symlink() or pcap.stat().st_size < 96:
        return []
    result=subprocess.run(
        ["tshark","-n","-r",str(pcap),"-Y",expr,"-T","fields",
         "-e","frame.number","-e","tcp.len"],
        text=True,capture_output=True,timeout=30,check=True)
    rows=[]
    for line in result.stdout.splitlines():
        cols=line.split("\t")
        if len(cols)!=2 or not all(re.fullmatch("[0-9]+",s) for s in cols):
            raise h1.EvidenceError("Invalid captured wire frame")
        rows.append((int(cols[0]),int(cols[1])))
    return rows


def wire_summary(path):
    dl=frames(path,f"ip.src=={DU_ADDRESS} && tcp.srcport=={ZMQ_DL_PORT} && tcp.len>1024")
    ul=frames(path,f"ip.dst=={DU_ADDRESS} && tcp.srcport=={ZMQ_UL_PORT} && tcp.len>1024")
    return {
        "actual_du_to_ue_large_tcp_payload_frames":len(dl),
        "actual_ue_to_du_large_tcp_payload_frames":len(ul),
        "actual_du_to_ue_first_frame":dl[0][0] if dl else None,
        "actual_ue_to_du_first_frame":ul[0][0] if ul else None,
        "capture_sha256":hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        "wire_evidence":"BIDIRECTIONAL_LARGE_ZMQ_TCP_PAYLOAD" if dl and ul else
                        "UNQUALIFIED_OR_ONE_WAY_ZMQ_TCP_PAYLOAD",
        "meaning":"Large TCP payload frames are transport evidence only; raw IQ and UE RRC/NAS conformance not independently decoded.",
    }


def protocol_wire_summary(pcap):
    """Independently decode candidate UE-associated F1AP/NGAP message frames.

    This function never asserts an actual RRC procedure from mere ZeroMQ
    payload or UE log strings; it reports independent control-plane frames.
    """
    filt = {
        "macro_f1ap_ul_rrc_message_transfer":
            f"f1ap.ULRRCMessageTransfer_element && ip.src == {DU_ADDRESS} && ip.dst == {h1.IP['cu']}",
        "macro_f1ap_dl_rrc_message_transfer":
            f"f1ap.DLRRCMessageTransfer_element && ip.src == {h1.IP['cu']} && ip.dst == {DU_ADDRESS}",
        "ngap_initial_ue_message":
            f"ngap.InitialUEMessage_element && ip.src == {h1.IP['cu']} && ip.dst == {h1.IP['amf']}",
    }
    if not pcap.is_file() or pcap.is_symlink() or pcap.stat().st_size < 128:
        return {
            "status": "NO_CONTROL_WIRE_CAPTURE",
            "frame_counts": {name: 0 for name in filt},
            "first_frame_numbers": {name: None for name in filt},
            "evidence_kind": "INDEPENDENT_SCTP_PACKET_DECODING",
            "protocol_validation": "NOT_ESTABLISHED",
        }
    counts={}
    first={}
    for name,expr in filt.items():
        output=h1.cmd(
            "tshark","-n","-r",str(pcap),"-Y",expr,
            "-T","fields","-e","frame.number",timeout=20
        )
        values=output.splitlines()
        if any(not re.fullmatch(r"[1-9][0-9]*",v) for v in values):
            raise h1.EvidenceError("Invalid independent F1AP or NGAP frame values")
        numbers=sorted(set(int(v) for v in values))
        counts[name]=len(numbers)
        first[name]=numbers[0] if numbers else None
    if all(counts.values()):
        status="UE_ASSOCIATED_F1AP_NGAP_WIRE_CANDIDATES"
    elif counts["macro_f1ap_ul_rrc_message_transfer"] and counts["macro_f1ap_dl_rrc_message_transfer"]:
        status="BIDIRECTIONAL_UE_ASSOCIATED_F1AP_WIRE_CANDIDATES"
    else:
        status="NO_BIDIRECTIONAL_UE_ASSOCIATED_F1AP_WIRE"
    return {
        "status":status,
        "frame_counts":counts,
        "first_frame_numbers":first,
        "control_capture_sha256":hashlib.sha256(pcap.read_bytes()).hexdigest(),
        "evidence_kind":"INDEPENDENT_SCTP_PACKET_DECODING",
        "protocol_validation":"CANDIDATE_ONLY",
    }


def assess_h3(receipt):
    """Fail closed on missing UE log and independently decoded F1AP evidence."""
    wire=receipt.get("protocol_wire") or {}
    ue=receipt.get("ue") or {}
    markers=set(ue.get("bounded_synthetic_ue_log_markers") or [])
    counts=wire.get("frame_counts") or {}
    if receipt.get("status")!="REAL_OAI_UE_SRS_DU_ZMQ_TRANSPORT_CANDIDATE_ONLY":
        return "BLOCKED_TRANSPORT"
    if wire.get("evidence_kind")!="INDEPENDENT_SCTP_PACKET_DECODING":
        return "BLOCKED_NO_INDEPENDENT_CONTROL_WIRE"
    if "SIB1_LOG_CANDIDATE" not in markers:
        return "BLOCKED_SIB1_LOG_EVIDENCE"
    if not counts.get("macro_f1ap_ul_rrc_message_transfer",0):
        return "BLOCKED_F1AP_UL_RRC_EVIDENCE"
    if not counts.get("macro_f1ap_dl_rrc_message_transfer",0):
        return "BLOCKED_F1AP_DL_RRC_EVIDENCE"
    return "SIB1_LOG_AND_BIDIRECTIONAL_F1AP_RRC_TRANSFER_CANDIDATES"


def initial_nas_wire_summary(pcap):
    """Independent NGAP and 5GS NAS initial Registration Request wire audit.

    Never retain NAS payload, IMSI/SUCI, keys, or unredacted decoded traces.
    7e 00 41 is the plain 5GS mobility Registration Request header.
    """
    output={
        "status":"NO_5GS_REGISTRATION_REQUEST_WIRE",
        "initial_ul_f1ap_frame_numbers":[],
        "registration_request_ngap_frame_numbers":[],
        "redacted_nas_header_match_count":0,
        "evidence_kind":"INDEPENDENT_NGAP_NAS_PDU_WIRE_DISSECTION",
        "registration_accept_observed":False,
        "registration_complete_observed":False,
        "nas_registration_complete":"NOT_VALIDATED",
    }
    if not pcap.is_file() or pcap.is_symlink() or pcap.stat().st_size < 128:
        return output
    filt=f"f1ap.InitialULRRCMessageTransfer_element && ip.src == {DU_ADDRESS} && ip.dst == {h1.IP['cu']}"
    initial=h1.cmd("tshark","-n","-r",str(pcap),"-Y",filt,
        "-T","fields","-e","frame.number",timeout=20)
    values=initial.splitlines()
    if any(not re.fullmatch(r"[1-9][0-9]*",v) for v in values):
        raise h1.EvidenceError("Invalid initial UL F1AP wire frame")
    output["initial_ul_f1ap_frame_numbers"]=sorted(set(int(v) for v in values))
    ngap_expr=(f"ngap.InitialUEMessage_element && ip.src == {h1.IP['cu']} "
               f"&& ip.dst == {h1.IP['amf']}")
    decoded=h1.cmd("tshark","-n","-r",str(pcap),"-Y",ngap_expr,
        "-T","fields","-e","frame.number","-e","ngap.NAS_PDU",timeout=20)
    initial_ue=[]
    for line in decoded.splitlines():
        cols=line.split("\t")
        if len(cols)!=2 or not re.fullmatch(r"[1-9][0-9]*",cols[0]):
            raise h1.EvidenceError("Missing independent NAS PDU alongside NGAP InitialUEMessage")
        # Treat all payload bytes as sensitive; inspect only the first 3
        # hexadecimal octets in-memory. Never print or persist the PDU.
        nas=cols[1].replace(":","").lower()
        if not re.fullmatch("[0-9a-f]*",nas) or len(nas)>8192 or len(nas)<6:
            continue
        if nas.startswith("7e0041"):
            initial_ue.append(int(cols[0]))
    output["registration_request_ngap_frame_numbers"]=sorted(set(initial_ue))
    output["redacted_nas_header_match_count"]=len(output["registration_request_ngap_frame_numbers"])
    if output["initial_ul_f1ap_frame_numbers"] and initial_ue:
        output["status"]="F1AP_INITIAL_UL_AND_5GS_REGISTRATION_REQUEST_WIRE_CANDIDATES"
    elif initial_ue:
        output["status"]="5GS_REGISTRATION_REQUEST_NGAP_ONLY_CANDIDATE"
    return output


def assess_h4(receipt):
    """Initial 5GS NAS request candidate only; registration acceptance is out of scope."""
    if receipt.get("h3_candidate_qualification")!="SIB1_LOG_AND_BIDIRECTIONAL_F1AP_RRC_TRANSFER_CANDIDATES":
        return "BLOCKED_H3_RRC_CANDIDATE"
    nas=receipt.get("initial_nas_wire") or {}
    if nas.get("evidence_kind")!="INDEPENDENT_NGAP_NAS_PDU_WIRE_DISSECTION":
        return "BLOCKED_NO_INDEPENDENT_NAS_DECODER"
    if not nas.get("initial_ul_f1ap_frame_numbers"):
        return "BLOCKED_F1AP_INITIAL_UL_RRC"
    reg=nas.get("registration_request_ngap_frame_numbers") or []
    if not reg or not nas.get("redacted_nas_header_match_count"):
        return "BLOCKED_NAS_REGISTRATION_REQUEST_PDU"
    first=receipt["protocol_wire"].get("first_frame_numbers") or {}
    dl=first.get("macro_f1ap_dl_rrc_message_transfer")
    ul=first.get("macro_f1ap_ul_rrc_message_transfer")
    ng=first.get("ngap_initial_ue_message")
    init=min(nas["initial_ul_f1ap_frame_numbers"])
    if not all(type(x) is int and x>0 for x in (init,dl,ul,ng)):
        return "BLOCKED_MISSING_ORDERED_CONTROL_FRAMES"
    if not (init<dl<ul<=ng and ng in reg):
        return "BLOCKED_INCORRECT_INITIAL_RRC_NAS_FRAME_ORDER"
    if nas.get("registration_accept_observed") is not False or nas.get("registration_complete_observed") is not False:
        return "BLOCKED_UNSUPPORTED_COMPLETED_NAS_CLAIM"
    return "ORDERED_F1AP_RRC_AND_5GS_NAS_REGISTRATION_REQUEST_CANDIDATE_ONLY"


def run_ue(out,ue,library_path):
    result={"started":False,"exit_code":None,"ran_seconds":0,
            "bounded_synthetic_ue_log_markers":[]}
    logfile=out/"ue-runner-local.log"
    end=time.monotonic()+65
    while time.monotonic()<end:
        try:
            running=h1.cmd("docker","inspect","--format","{{.State.Running}}",
                           h1.NAMES["macro"],check=False,timeout=6).strip()
            if running=="true":
                break
        except (OSError,subprocess.TimeoutExpired):
            pass
        time.sleep(1)
    else:
        result["reason"]="MACRO_DU_NOT_RUNNING"
        return result

    env=os.environ.copy()
    env["LD_LIBRARY_PATH"]=str(library_path)+":"+env.get("LD_LIBRARY_PATH","")
    env["CPM_SOURCE_CACHE"]=str(out/"ue-cpm-inert")
    command=ue_command(ue)
    started=time.monotonic()
    with logfile.open("wb") as stream:
        p=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,
                           env=env,start_new_session=True)
        result["started"]=True
        try:
            result["exit_code"]=p.wait(timeout=UE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGTERM)
            try:
                result["exit_code"]=p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL)
                result["exit_code"]=p.wait(timeout=5)
            result["timed_out_as_designed"]=True
    result["ran_seconds"]=round(time.monotonic()-started,1)
    data=logfile.read_bytes()[-128*1024:].decode(errors="replace")
    for label,pattern in (
        ("SIB1_LOG_CANDIDATE",r"(?i)SIB1.*decod"),
        ("MIB_LOG_CANDIDATE",r"(?i)MIB.*decod"),
        ("RRC_LOG_CANDIDATE",r"(?i)RRC.*(Setup|Connection)"),
        ("ZMQ_LOG_CANDIDATE",r"(?i)ZMQ.*(connected|socket|bound)"),
        ("DEVICE_LOAD_ERROR",r"(?i)(device.*not found|cannot open shared object|undefined symbol)"),
        ("CRASH_LOG_CANDIDATE",r"(?i)(segmentation fault|assertion failed|fatal)"),
    ):
        if re.search(pattern,data):
            result["bounded_synthetic_ue_log_markers"].append(label)
    result["log_sha256"]=hashlib.sha256(logfile.read_bytes()).hexdigest()
    return result


def run(a):
    if os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted":
        raise h1.EvidenceError("Requires isolated GitHub-hosted runner")
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=True)
    executable=Path(a.ue).resolve()
    if not executable.is_file() or not os.access(executable,os.X_OK):
        raise h1.EvidenceError("Missing source-built pinned OAI UE executable")
    if not Path(a.library_path).is_dir():
        raise h1.EvidenceError("Missing OAI UE library path")

    base_render=h1.render_config
    def with_radio(path,role):
        return overlay_config(base_render(path,role),role)
    h1.render_config=with_radio

    trial=h1.Trial(a)
    outcome={"schema":"hcransrs-real-pinned-ue-zmq-h2-v1",
             "scenario":"srsran-hcran-macro-to-oai-nrue",
             "oai_source_sha":"dfb296ab867050a7bde81b393fa31b757ada2c83",
             "srsran_source_sha":"11c9bbabb69873752500d676f55e0034f6caa5c5",
             "status":"INCOMPLETE","not_validated":NOT_VALIDATED,
             "hp_cluster_touched":False,"production_decision":"NO_GO"}
    pcap=out/"runner-local-radio.pcap"
    radio_capture=None
    thread=None
    ue_result={}
    h1_result={}
    error=None
    try:
        def worker():
            try:
                ue_result.update(run_ue(out,executable,Path(a.library_path)))
            except Exception as ex:
                ue_result.update({"started":False,"error_type":type(ex).__name__})
        thread=threading.Thread(target=worker,daemon=False)
        thread.start()
        # Wait for the private bridge created by the H1 runtime.
        def capture_worker():
            nonlocal radio_capture
            until=time.monotonic()+40
            while time.monotonic()<until and not Path("/sys/class/net/"+h1.BRIDGE).exists():
                time.sleep(.5)
            if not Path("/sys/class/net/"+h1.BRIDGE).exists():
                return
            stream=pcap.open("wb")
            log=(out/"runner-local-capture.log").open("w")
            radio_capture=subprocess.Popen(
                ["sudo","-n","dumpcap","-i",h1.BRIDGE,
                 "-f","tcp port 2000 or tcp port 2001",
                 "-a","duration:100","-a","filesize:4096","-w","-"],
                stdout=stream,stderr=log)
        capture_thread=threading.Thread(target=capture_worker,daemon=False)
        capture_thread.start()
        try:
            h1_result=trial.run()
        finally:
            trial.close()
        capture_thread.join(timeout=110)
        thread.join(timeout=95)
        if thread.is_alive():
            raise h1.EvidenceError("Unbounded UE monitoring thread")
        if radio_capture is not None:
            radio_capture.wait(timeout=12)
        if h1_result.get("status")!="HCRANSRS_NGAP_BOTH_F1AP_WIRE_PASS_UE_NOT_TESTED":
            raise h1.EvidenceError("Existing NG/F1 H1 wire acceptance regression")
        outcome["h1_dual_f1_wire"]=h1_result["status"]
        outcome["ue"]=ue_result
        outcome["radio_wire"]=wire_summary(pcap)
        outcome["protocol_wire"]=protocol_wire_summary(out/"runner-local"/"setup.pcapng")
        outcome["initial_nas_wire"]=initial_nas_wire_summary(out/"runner-local"/"setup.pcapng")
        if not ue_result.get("started"):
            outcome["status"]="BLOCKED_UE_START"
        elif outcome["radio_wire"]["wire_evidence"]=="BIDIRECTIONAL_LARGE_ZMQ_TCP_PAYLOAD":
            outcome["status"]="REAL_OAI_UE_SRS_DU_ZMQ_TRANSPORT_CANDIDATE_ONLY"
        else:
            outcome["status"]="BLOCKED_BIDIRECTIONAL_ZMQ_IQ_TRAFFIC"
    except Exception as ex:
        error=type(ex).__name__
        outcome["status"]="BLOCKED_RESEARCH_TRIAL"
        outcome["error_type"]=error
    finally:
        if radio_capture is not None and radio_capture.poll() is None:
            radio_capture.terminate()
            try:radio_capture.wait(timeout=7)
            except subprocess.TimeoutExpired:radio_capture.kill()
        if thread and thread.is_alive():
            thread.join(timeout=10)
        if "ue" not in outcome:outcome["ue"]=ue_result
        if "radio_wire" not in outcome:outcome["radio_wire"]=wire_summary(pcap)
        if "protocol_wire" not in outcome:
            outcome["protocol_wire"]={"status":"UNAVAILABLE", "protocol_validation":"NOT_ESTABLISHED"}
        outcome["h3_candidate_qualification"]=assess_h3(outcome)
        outcome["h4_candidate_qualification"]=assess_h4(outcome)
        (out/"receipt.json").write_text(json.dumps(outcome,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"status":outcome["status"],"radio_wire":outcome["radio_wire"],
                      "production_decision":"NO_GO"},sort_keys=True))
    return 0 if outcome["status"]=="REAL_OAI_UE_SRS_DU_ZMQ_TRANSPORT_CANDIDATE_ONLY" else 1


def main():
    p=argparse.ArgumentParser()
    for name in ("rendered","stage3","out","srs-image","core-image","ue","library-path"):
        p.add_argument("--"+name,required=True)
    return run(p.parse_args())

if __name__=="__main__":
    raise SystemExit(main())
