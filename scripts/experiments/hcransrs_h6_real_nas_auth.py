#!/usr/bin/env python3
"""H6 isolated, real OAI UE ↔ srsRAN authentication request/response pilot.

Runs ONLY on disposable GitHub-hosted runner; reuses the previously proven
H2/H3/H4 genuine NR UE + CU/dual-DU radio trial and H5 synthetic authentication
backend. Injects *one* test-only subscriber into a private MongoDB on the SAME
H1 bridge as the AMF, and injects matching ephemeral Ki/OPc into the UE.
No secrets, raw NAS PDUs, or UE/core logs are emitted or uploaded.
No Registration Accept/Complete or PDU claims without separate proof.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import hcransrs_ngf1 as h1
import hcransrs_real_ue_zmq_h2 as h2
import hcransrs_h5_core_preflight as h5

IP={**h1.IP,"mongo":"172.29.78.12","ausf":"172.29.78.13",
    "udm":"172.29.78.14","udr":"172.29.78.15"}
EXTRAS=("mongo","ausf","udm","udr")
PREFIX="hcransrs-h6"
NOT_VALIDATED=["NAS_REGISTRATION_ACCEPT","NAS_REGISTRATION_COMPLETE",
    "NAS_SECURITY_ACTIVATION","PDU_SESSION","F1U_TRAFFIC","NGU_TRAFFIC",
    "MOBILITY","QOS","RECOVERY","PRODUCTION_CONFORMANCE"]


def check_runner():
    if os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted":
        raise h1.EvidenceError("H6 requires disposable GitHub-hosted runner")
    if os.environ.get("HCRANSRS_H6_ISOLATED_AUTH")!="SYNTHETIC_ONE_UE_NO_DEPLOY":
        raise h1.EvidenceError("Explicit H6 synthetic-only authorization absent")


def nas_control_events(pcap,exec_fn=h1.cmd):
    """Independently decode direction and *only* the first 3 NAS PDU bytes.

    The rest of NAS PDU can contain private identifiers and is never
    returned, printed, logged or written to artifacts.
    """
    classes={
        "downlink":("ngap.DownlinkNASTransport_element",h1.IP["amf"],h1.IP["cu"]),
        "uplink":("ngap.UplinkNASTransport_element",h1.IP["cu"],h1.IP["amf"]),
    }
    receipt={"schema":"hcransrs-h6-nas-auth-wire-v1",
             "authentication_request_frames":[],
             "authentication_response_frames":[],
             "authentication_failure_frames":[],
             "other_plain_5gs_nas_frames":0,
             "protected_nas_candidates":0,
             "independent_nas_decoder":"NGAP_NAS_PDU_FIRST_THREE_OCTETS_ONLY",
             "registration_accept":"NOT_VERIFIED",
             "registration_complete":"NOT_VERIFIED"}
    if not pcap.is_file() or pcap.is_symlink() or not 128<=pcap.stat().st_size<=5*1024*1024:
        receipt["status"]="NO_BOUNDED_REAL_SCTP_CAPTURE"
        return receipt
    for direction,(protocol,src,dst) in classes.items():
        expr=f"{protocol} && ip.src == {src} && ip.dst == {dst}"
        out=exec_fn("tshark","-n","-r",str(pcap),"-Y",expr,
                    "-T","fields","-e","frame.number","-e","ngap.NAS_PDU",timeout=30)
        for line in out.splitlines():
            fields=line.split("\t")
            if len(fields)!=2 or not re.fullmatch("[1-9][0-9]*",fields[0]):
                raise h1.EvidenceError("NGAP NAS frame fields missing")
            frame=int(fields[0])
            payload=fields[1].lower().replace(":","")
            if len(payload)>8192 or len(payload)<6 or not re.fullmatch("[0-9a-f]+",payload):
                continue
            header=payload[:6]
            if header=="7e0056" and direction=="downlink":
                receipt["authentication_request_frames"].append(frame)
            elif header=="7e0057" and direction=="uplink":
                receipt["authentication_response_frames"].append(frame)
            elif header=="7e0059" and direction=="uplink":
                receipt["authentication_failure_frames"].append(frame)
            elif header.startswith("7e0") and header[2:4]!="00":
                receipt["protected_nas_candidates"]+=1
            else:
                receipt["other_plain_5gs_nas_frames"]+=1
    req=receipt["authentication_request_frames"]
    rsp=receipt["authentication_response_frames"]
    receipt["status"]=("AUTH_REQUEST_AND_RESPONSE_ON_REAL_NGAP_WIRE"
                       if req and rsp and min(req)<min(rsp)
                       else "NO_ORDERED_NAS_AUTH_REQUEST_RESPONSE")
    return receipt


def auth_gap_diagnostics(pcap, ue_log, events, exec_fn=h1.cmd):
    """Only integer frame counts and boolean source-grounded UE log markers.

    Captured NAS, IMSI/SUCI, Ki, OPc and actual logs are NEVER serialized.
    F1AP frames after the NGAP authentication request are directional
    transport candidates, not proof of UE NAS delivery.
    """
    result={"status":"UNQUALIFIED","ue_debug_received_auth_request":False,
            "ue_auth_decode_error":False,"ue_invalid_ksi":False,
            "ue_ul_nas_rrc_candidate":False,"ue_log_missing":True,
            "f1ap_dl_rrc_after_auth_request_frames":[],
            "f1ap_ul_rrc_after_auth_request_frames":[],
            "reason":"DIAGNOSTIC_ONLY_NOT_AUTHENTICATION_SUCCESS"}
    if ue_log.is_file() and not ue_log.is_symlink():
        # Bound the scan to the tail of a runner-private, never-uploaded log.
        with ue_log.open("rb") as stream:
            stream.seek(max(0,ue_log.stat().st_size-256*1024))
            data=stream.read(256*1024).decode(errors="replace")
        result["ue_log_missing"]=False
        patterns={
            "ue_debug_received_auth_request":r"Received NAS Authentication Request",
            "ue_auth_decode_error":r"decode_(?:5gmm_msg_header|nas_key_set_identifier) failure in NAS Authentication Request handling",
            "ue_invalid_ksi":r"Invalid NAS Key Set Identifier: send Authentication Failure",
            "ue_ul_nas_rrc_candidate":r"PDCP_DATA_REQ ULInformationTransfer \\(NAS",
        }
        for key,pattern in patterns.items():
            result[key]=bool(re.search(pattern,data))
    frames=events.get("authentication_request_frames") or []
    if not frames:
        result["status"]="NO_NGAP_AUTH_REQUEST"
        return result
    if not pcap.is_file() or pcap.is_symlink() or not 128<=pcap.stat().st_size<=5*1024*1024:
        result["status"]="NO_BOUNDED_CONTROL_CAPTURE"
        return result
    first=min(frames)
    filters={
        "f1ap_dl_rrc_after_auth_request_frames":
            f"f1ap.DLRRCMessageTransfer_element && ip.src=={h1.IP['cu']} && ip.dst=={h1.IP['macro']} && frame.number>{first}",
        "f1ap_ul_rrc_after_auth_request_frames":
            f"f1ap.ULRRCMessageTransfer_element && ip.src=={h1.IP['macro']} && ip.dst=={h1.IP['cu']} && frame.number>{first}",
    }
    for name,expr in filters.items():
        rows=exec_fn("tshark","-n","-r",str(pcap),"-Y",expr,
                     "-T","fields","-e","frame.number",timeout=20)
        numbers=rows.splitlines()
        if any(not re.fullmatch(r"[1-9][0-9]*",n) for n in numbers):
            raise h1.EvidenceError("Unexpected F1AP frame values")
        result[name]=sorted(set(int(n) for n in numbers))
    result["status"]="SANITIZED_POST_AUTH_F1_AND_UE_LOG_DIAGNOSTIC_ONLY"
    return result


def qualify(receipt,events,backend_pass):
    if not backend_pass:
        return "BLOCKED_ISOLATED_AUTH_BACKEND"
    if receipt.get("h4_candidate_qualification")!="ORDERED_F1AP_RRC_AND_5GS_NAS_REGISTRATION_REQUEST_CANDIDATE_ONLY":
        return "BLOCKED_H4_INITIAL_REGISTRATION"
    if events.get("independent_nas_decoder")!="NGAP_NAS_PDU_FIRST_THREE_OCTETS_ONLY":
        return "BLOCKED_NO_INDEPENDENT_NAS_EVIDENCE"
    if events.get("status")!="AUTH_REQUEST_AND_RESPONSE_ON_REAL_NGAP_WIRE":
        return "BLOCKED_AUTH_REQUEST_RESPONSE_WIRE"
    ngap=receipt.get("initial_nas_wire",{}).get("registration_request_ngap_frame_numbers") or []
    req=events.get("authentication_request_frames") or []
    rsp=events.get("authentication_response_frames") or []
    if not ngap or not req or not rsp or not max(ngap)<min(req)<min(rsp):
        return "BLOCKED_AUTH_NAS_FRAME_ORDER"
    return "REAL_NAS_AUTH_REQUEST_AND_RESPONSE_WIRE_CANDIDATE_ONLY"


class CoreBridge:
    """H6-only monkeypatch of H1 launch/close; canonical H1-H4 unchanged."""
    def __init__(self,out):
        self.out=Path(out).resolve()
        self.credentials=h5.subscriber()
        h5.check_subscriber_doc(self.credentials)
        self.backend_ok=False
        self.added=[]
        self.orig_launch=h1.Trial.launch
        self.orig_close=h1.Trial.close
        self.orig_ue=h2.ue_command
    def install(self):
        h5.IP=dict(IP)
        if self.credentials["imsi"]!=h5.IMSI:
            raise h1.EvidenceError("Synthetic identity mismatch")
        # Install ordinary class-callable adapters, not CoreBridge bound
        # methods: Trial must pass its own instance to launch/close.
        def launch_adapter(trial,role,image,binary,config):
            return self.launch(trial,role,image,binary,config)
        def close_adapter(trial):
            return self.close(trial)
        h1.Trial.launch=launch_adapter
        h1.Trial.close=close_adapter
        h2.ue_command=self.ue_command
    def restore(self):
        h1.Trial.launch=self.orig_launch
        h1.Trial.close=self.orig_close
        h2.ue_command=self.orig_ue
    def ue_command(self,binary):
        # CLI may be visible to local /proc; this is a disposable, isolated
        # GitHub runner. OAI logs Ki/OPc, therefore never export UE logs.
        base=self.orig_ue(binary)
        # Pinned OAI NAS uses LOG_D for "Received NAS Authentication Request".
        # Debug output stays runner-local; NEVER upload or print raw UE logs.
        return base+["--log_config.NAS_log_level","debug",
                     "--uicc0.key",self.credentials["security"]["k"],
                     "--uicc0.opc",self.credentials["security"]["opc"],
                     "--uicc0.amf","8000"]
    def launch(self,trial,role,image,binary,config):
        self.orig_launch(trial,role,image,binary,config)
        if role=="nrf":
            self.prepare_backend()
    def prepare_backend(self):
        work=self.out/"private-auth"
        work.mkdir(mode=0o700,exist_ok=False)
        h5.command("docker","run","-d","--pull=never","--name",PREFIX+"-mongo",
            "--network",h1.NET,"--ip",IP["mongo"],"--memory","2g",
            h5.MONGO,"mongod","--bind_ip_all","--quiet",timeout=30)
        self.added.append("mongo")
        ready=False
        for _ in range(35):
            try:
                out=h5.command("docker","exec",PREFIX+"-mongo","mongosh",
                    "--norc","--quiet","--eval","db.adminCommand({ping:1}).ok",
                    "admin",timeout=8)
                if out.strip().endswith("1"):
                    ready=True;break
            except Exception:
                pass
            time.sleep(1)
        if not ready:raise h1.EvidenceError("Private Mongo not ready")
        payload=("const d="+json.dumps(self.credentials,separators=(",",":"))+"; "
                 "if(db.subscribers.countDocuments({imsi:d.imsi})!==0) throw Error('duplicate');"
                 "const x=db.subscribers.insertOne(d);"
                 "if(!x.acknowledged) throw Error('insert');"
                 "print('ONE_SYNTHETIC_SUBSCRIBER_INSERTED');")
        confirmed=h5.command("docker","exec","-i",PREFIX+"-mongo",
            "mongosh","--norc","--quiet","open5gs",input_text=payload,timeout=20)
        if "ONE_SYNTHETIC_SUBSCRIBER_INSERTED" not in confirmed:
            raise h1.EvidenceError("Synthetic subscriber insertion not confirmed")
        del payload,confirmed
        for role in ("ausf","udm","udr"):
            cfg=work/(role+".yaml")
            cfg.write_text(h5.config(role))
            cfg.chmod(0o644)  # nonsecret NF routing; container can read
            extra=["-e","DB_URI=mongodb://"+IP["mongo"]+"/open5gs"] if role=="udr" else []
            h5.command("docker","run","-d","--pull=never",
                "--name",PREFIX+"-"+role,"--network",h1.NET,
                "--ip",IP[role],"--memory","1g",*extra,
                "-v",str(cfg)+":/tmp/h6-config.yaml:ro",
                "--entrypoint","/bin/sh",h5.CORE,
                "-ec",f"exec open5gs-{role}d -c /tmp/h6-config.yaml",timeout=25)
            self.added.append(role)
            if not h5.wait_socket(IP[role],7777,deadline=30):
                raise h1.EvidenceError("H6 NF not ready: "+role)
        discovered={}
        for role in ("ausf","udm","udr"):
            for _ in range(12):
                try:
                    if h5.nrf_discovery(role):
                        discovered[role]=True
                        break
                except (OSError,ValueError,KeyError,h5.PreflightError):
                    pass
                time.sleep(1)
        if set(discovered)!={"ausf","udm","udr"}:
            raise h1.EvidenceError("H6 auth NF discovery incomplete")
        if not h5.udr_test():
            raise h1.EvidenceError("H6 auth subscription not independently retrieved")
        self.backend_ok=True
    def close(self,trial):
        # Never call H1 close: H1 prints AMF/NRF/core logs, which may disclose
        # subscriber identifiers and security material in an auth experiment.
        if trial.capture is not None and trial.capture.poll() is None:
            try:
                trial.capture.send_signal(signal.SIGINT)
                trial.capture.wait(timeout=8)
            except (OSError,subprocess.TimeoutExpired):
                trial.capture.kill()
                trial.capture.wait(timeout=8)
        for name in reversed(trial.created):
            h5.command("docker","rm","-f",h1.NAMES[name],timeout=20,check=False)
        for name in reversed(self.added):
            h5.command("docker","rm","-f",PREFIX+"-"+name,timeout=20,check=False)
        if trial.network:
            h5.command("docker","network","rm",h1.NET,timeout=20,check=False)


def main():
    p=argparse.ArgumentParser()
    for name in ("rendered","stage3","out","srs-image","core-image","ue","library-path"):
        p.add_argument("--"+name,required=True)
    args=p.parse_args()
    check_runner()
    if args.core_image!=h5.CORE:
        raise h1.EvidenceError("Unpinned authentication core image")
    out=Path(args.out).resolve()
    if not out.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise h1.EvidenceError("Must retain only runner-temporary evidence")
    out.mkdir(parents=True,exist_ok=True)
    bridge=CoreBridge(out)
    result={"schema":"hcransrs-real-synthetic-auth-h6-v1",
            "status":"BLOCKED","backend_ready":False,
            "registration_accept":"NOT_VERIFIED",
            "registration_complete":"NOT_VERIFIED",
            "not_validated":NOT_VALIDATED,
            "hp_cluster_touched":False,
            "production_decision":"NO_GO"}
    try:
        bridge.install()
        rc=h2.run(args)
        h4=json.loads((out/"receipt.json").read_text())
        control=nas_control_events(out/"runner-local"/"setup.pcapng")
        diagnostic=auth_gap_diagnostics(
            out/"runner-local"/"setup.pcapng",out/"ue-runner-local.log",control)
        result["backend_ready"]=bridge.backend_ok
        result["h4"]=h4.get("h4_candidate_qualification")
        result["nas_wire"]=control
        result["auth_gap_diagnostics"]=diagnostic
        result["ue_runtime_seconds"]=h4.get("ue",{}).get("ran_seconds")
        result["ue_log_markers"]=h4.get("ue",{}).get("bounded_synthetic_ue_log_markers",[])
        result["status"]=qualify(h4,control,bridge.backend_ok) if rc==0 else "BLOCKED_RADIO_H4"
    except Exception as exc:
        result["error_type"]=type(exc).__name__
        result["status"]="BLOCKED_H6_TRIAL"
        print("H6 trial diagnostic class: "+type(exc).__name__,flush=True)
    finally:
        bridge.restore()
        bridge.credentials.clear()
        # Preserve H2 receipt in runner only; H6 is only uploaded artifact.
        (out/"h6-receipt.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"status":result["status"],"backend_ready":result["backend_ready"],
                      "nas_wire":result.get("nas_wire",{}),
                      "auth_gap_diagnostics":result.get("auth_gap_diagnostics",{}),
                      "production_decision":"NO_GO"},sort_keys=True))
    return 0 if result["status"]=="REAL_NAS_AUTH_REQUEST_AND_RESPONSE_WIRE_CANDIDATE_ONLY" else 1

if __name__=="__main__":
    raise SystemExit(main())
