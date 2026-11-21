#!/usr/bin/env python3
"""Phase 1B2: real srsRAN N2 and F1-C setup with independent wire decoding.
No UE, ZMQ peer, NAS, PDU, traffic, fault injection, HP or deployment.
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
import time
import yaml

NET = "phase1b2-srsran-ngf1"
BRIDGE = "br-p1b2srs"
IP = {"nrf": "172.29.74.11", "amf": "172.29.74.10",
      "cu": "172.29.74.20", "du": "172.29.74.30"}
NAMES = {k: NET + "-" + k for k in IP}
MESSAGES = {
    "NGSetupRequest": ("ngap", "cu", "amf", 38412),
    "NGSetupResponse": ("ngap", "amf", "cu", 38412),
    "F1SetupRequest": ("f1ap", "du", "cu", 38472),
    "F1SetupResponse": ("f1ap", "cu", "du", 38472),
}
NOT_RUN = ["VIRTUAL_RADIO_UE", "NAS_REGISTRATION", "PDU_SESSION",
           "F1U_TRAFFIC", "NGU_TRAFFIC", "FAULT_RECOVERY"]


class EvidenceError(RuntimeError):
    pass


def cmd(*args, timeout=30, check=True):
    p = subprocess.run([str(x) for x in args], capture_output=True,
                       text=True, timeout=timeout)
    if check and p.returncode:
        raise EvidenceError(f"Command {args[0]} {args[1:3]} failed ({p.returncode}): {p.stderr[-1500:]}")
    return p.stdout


def filter_expr(message, protocol, src, dst, port):
    return (f"{protocol}.{message}_element && ip.src == {IP[src]} && "
            f"ip.dst == {IP[dst]} && (sctp.srcport == {port} || sctp.dstport == {port})")


def decode(pcap, runner=cmd):
    if not pcap.is_file() or pcap.is_symlink() or not 128 <= pcap.stat().st_size <= 5*1024*1024:
        raise EvidenceError("Missing, invalid or oversized actual wire capture")
    def frames(expr):
        out = runner("tshark", "-n", "-r", pcap, "-Y", expr,
                     "-T", "fields", "-e", "frame.number", timeout=20)
        values = out.splitlines()
        if any(not re.fullmatch(r"[1-9][0-9]*", s) for s in values):
            raise EvidenceError("Unexpected decoded frame data")
        return sorted(set(map(int, values)))
    observed = {msg: frames(filter_expr(msg, *spec)) for msg, spec in MESSAGES.items()}
    failed = frames(
        "(" + filter_expr("NGSetupFailure", "ngap", "amf", "cu", 38412)
        + ") || (" + filter_expr("F1SetupFailure", "f1ap", "cu", "du", 38472) + ")")
    validate(observed, failed)
    return {"message_frame_counts": {k: len(v) for k,v in observed.items()},
            "first_frame_numbers": {k: min(v) for k,v in observed.items()},
            "capture_sha256": hashlib.sha256(pcap.read_bytes()).hexdigest()}


def validate(observed, failed):
    if failed:
        raise EvidenceError("Decoded explicit setup failure")
    if set(observed) != set(MESSAGES):
        raise EvidenceError("Missing required message classes")
    if any(not isinstance(v, list) or not v or any(type(n) is not int or n < 1 for n in v)
           for v in observed.values()):
        raise EvidenceError("Missing or invalid authentic setup frames")
    for req, rsp in (("NGSetupRequest","NGSetupResponse"),("F1SetupRequest","F1SetupResponse")):
        if min(observed[req]) >= min(observed[rsp]):
            raise EvidenceError("Setup response did not follow its request")


def render_config(path, role):
    docs = list(yaml.safe_load_all(path.read_text()))
    full = "cran-srsran-" + role
    def one(kind, name):
        matches = [d for d in docs if isinstance(d,dict) and d.get("kind")==kind
                   and d.get("metadata",{}).get("name")==name]
        if len(matches)!=1:
            raise EvidenceError(f"Expected one {kind} {name}")
        return matches[0]
    dep = one("Deployment", full)
    if dep["metadata"]["labels"]["ran-type"] != "cran" or dep["spec"]["template"]["metadata"]["labels"]["ran-type"] != "cran":
        raise EvidenceError("Not the intended C-RAN profile")
    raw = one("ConfigMap", full+"-config")["data"][role+".yml"]
    raw = raw.replace("POD_IP",IP[role]).replace("AMF_IP",IP["amf"]).replace("CU_IP",IP["cu"])
    if any(v in raw for v in ("POD_IP","AMF_IP","CU_IP")):
        raise EvidenceError("Unresolved test addresses")
    cfg = yaml.safe_load(raw)
    cfg["log"]["filename"] = "stdout"
    cfg["log"]["all_level"] = "info"
    if role == "cu":
        if (cfg["cu_cp"]["amf"]["addr"] != IP["amf"] or cfg["cu_cp"]["amf"]["bind_addr"] != IP["cu"]
                or cfg["cu_cp"]["f1ap"]["bind_addr"] != IP["cu"] or cfg["gnb_id"] != 3585):
            raise EvidenceError("Wrong CU interfaces")
    else:
        if (cfg["f1ap"]["cu_cp_addr"] != IP["cu"] or cfg["f1ap"]["bind_addr"] != IP["du"]
                or cfg["ru_sdr"]["device_driver"] != "zmq" or cfg["gnb_du_id"] != 100):
            raise EvidenceError("Wrong DU transport")
    return cfg


class Trial:
    def __init__(self,a):
        self.a=a
        self.out=Path(a.out).resolve()
        self.out.mkdir(parents=True,exist_ok=True)
        self.created=[]
        self.network=False
        self.capture=None
        self.stage="PREPARE"
        self.start=time.monotonic()

    def live(self):
        for role in self.created:
            if cmd("docker","inspect","--format","{{.State.Running}}",NAMES[role],check=False).strip()!="true":
                raise EvidenceError(f"Container {role} exited")

    def launch(self,role,image,binary,config):
        args = ["-c","/tmp/phase1b2-config.yml"]
        if role in ("amf","nrf"):
            entry="/bin/sh"
            args=["-ec","exec open5gs-"+role+"d -c /tmp/phase1b2-config.yml"]
        else:
            entry=binary
        cmd("docker","run","-d","--pull=never","--name",NAMES[role],"--network",NET,
            "--ip",IP[role],"--memory","4g",
            "-v",str(config)+":/tmp/phase1b2-config.yml:ro",
            "--entrypoint",entry,image,*args)
        self.created.append(role)

    def run(self):
        private=self.out/"runner-local"
        private.mkdir(mode=0o700)
        for role in ("amf","nrf"):
            raw=(Path(self.a.stage3)/(role+".yaml")).read_text().replace("172.29.73.","172.29.74.")
            raw=raw.replace('mcc: "001"','mcc: "208"').replace('mnc: "01"','mnc: "93"')
            if role=="amf":
                raw=raw.replace("        - sst: 1",'        - sst: 1\n          sd: "010203"')
                if 'sd: "010203"' not in raw:
                    raise EvidenceError("Missing test slice")
            (private/(role+".yaml")).write_text(raw)
        for role in ("cu","du"):
            cfg=render_config(Path(self.a.rendered)/(role+".yaml"),role)
            (private/(role+".yaml")).write_text(yaml.safe_dump(cfg,sort_keys=False))
        self.stage="ISOLATED_BRIDGE"
        cmd("docker","network","create","--driver","bridge","--internal",
            "--subnet","172.29.74.0/24","--opt","com.docker.network.bridge.name="+BRIDGE,NET)
        self.network=True
        self.stage="START_CAPTURE"
        capture=private/"setup.pcapng"
        with (private/"capture.log").open("w") as log:
            self.capture=subprocess.Popen(
                ["sudo","-n","tshark","-n","-i",BRIDGE,"-f","sctp",
                 "-a","duration:100","-a","filesize:4096","-w",str(capture)],
                stdout=subprocess.DEVNULL,stderr=log)
            time.sleep(3)
            if self.capture.poll() is not None:
                raise EvidenceError("Packet observer exited before workload startup")
            self.stage="CORE_START"
            for role in ("nrf","amf"):
                self.launch(role,self.a.core_image,"/usr/bin/open5gs-"+role+"d",private/(role+".yaml"))
            time.sleep(4)
            self.live()
            self.stage="CU_START"
            self.launch("cu",self.a.srs_image,"/usr/local/bin/srscu",private/"cu.yaml")
            time.sleep(4)
            self.live()
            self.stage="DU_START"
            self.launch("du",self.a.srs_image,"/usr/local/bin/srsdu",private/"du.yaml")
            self.stage="WIRE_OBSERVATION"
            deadline=time.monotonic()+105
            while self.capture.poll() is None and time.monotonic()<deadline:
                self.live()
                time.sleep(2)
            if self.capture.poll()!=0:
                raise EvidenceError("Capture incomplete or observer failed")
        self.live()
        cmd("sudo","-n","chown",f"{os.getuid()}:{os.getgid()}",capture)
        self.stage="DECODE"
        result=decode(capture)
        self.stage="LIVE_ASSOCIATIONS"
        assoc=cmd("docker","exec",NAMES["cu"],"cat","/proc/net/sctp/assocs")
        for role in ("amf","du"):
            if not re.search(r"(?<![\d.])"+re.escape(IP[role])+r"(?![\d.])",assoc):
                raise EvidenceError("CU missing live SCTP peer "+role)
        self.live()
        return {"schema":"phase1b2-cran-srsran-wire-v1","scenario":"cran-srsran",
                "status":"NGAP_F1AP_WIRE_PASS_UE_NOT_TESTED",
                "scope":"one source-built CU and one DU; real SCTP NG and F1 setup",
                "wire":result,"all_four_containers_alive":True,
                "amf_du_sctp_peers_observed":True,"not_run":NOT_RUN,
                "trial_seconds":round(time.monotonic()-self.start,2),
                "hp_cluster_touched":False,"production_decision":"NO_GO"}

    def close(self):
        if self.capture and self.capture.poll() is None:
            try:
                self.capture.send_signal(signal.SIGINT)
                self.capture.wait(timeout=8)
            except (OSError,subprocess.TimeoutExpired):
                self.capture.kill()
        for role in reversed(self.created):
            try:
                output=cmd("docker","logs","--tail","60",NAMES[role],check=False,timeout=15)
                if output:
                    print(role+" bounded synthetic signaling log:\n"+output[-4000:],flush=True)
            except Exception:
                pass
            cmd("docker","rm","-f",NAMES[role],check=False,timeout=20)
        if self.network:
            cmd("docker","network","rm",NET,check=False,timeout=20)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("rendered","stage3","out","srs-image","core-image"):
        p.add_argument("--"+k,required=True)
    a=p.parse_args()
    if os.getenv("GITHUB_ACTIONS")!="true" or os.getenv("RUNNER_ENVIRONMENT")!="github-hosted":
        p.error("Disposable GitHub-hosted runner required")
    t=Trial(a)
    rc=1
    try:
        receipt=t.run()
        rc=0
    except Exception as exc:
        receipt={"schema":"phase1b2-cran-srsran-wire-v1","scenario":"cran-srsran",
                 "status":"FAIL","stage":t.stage,"error_type":type(exc).__name__,
                 "not_run":NOT_RUN,"hp_cluster_touched":False,"production_decision":"NO_GO"}
        print(str(exc)[:2000],file=sys.stderr,flush=True)
    finally:
        try:
            t.close()
        except Exception as exc:
            print("Scoped cleanup failed: "+type(exc).__name__,file=sys.stderr)
            rc=1
            receipt={"schema":"phase1b2-cran-srsran-wire-v1","scenario":"cran-srsran",
                     "status":"FAIL","stage":"CLEANUP","not_run":NOT_RUN,
                     "hp_cluster_touched":False,"production_decision":"NO_GO"}
    (t.out/"receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    print(json.dumps(receipt,sort_keys=True),flush=True)
    return rc


if __name__=="__main__":
    raise SystemExit(main())
