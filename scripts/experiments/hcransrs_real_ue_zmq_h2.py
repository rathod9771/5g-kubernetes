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
