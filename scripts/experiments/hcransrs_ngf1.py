#!/usr/bin/env python3
"""H-CRAN srsRAN H1: real srsRAN N2 and F1-C setup with independent wire decoding.
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

NET = "hcransrs-ngf1"
BRIDGE = "br-hcransrs"
IP = {"nrf": "172.29.78.11", "amf": "172.29.78.10",
      "cu": "172.29.78.20", "macro": "172.29.78.30",
      "small": "172.29.78.31"}
NAMES = {k: NET + "-" + k for k in IP}
MESSAGES = {
    "NGSetupRequest": ("ngap", "cu", "amf", 38412),
    "NGSetupResponse": ("ngap", "amf", "cu", 38412),
    "F1SetupRequest_macro": ("f1ap", "macro", "cu", 38472),
    "F1SetupResponse_macro": ("f1ap", "cu", "macro", 38472),
    "F1SetupRequest_small": ("f1ap", "small", "cu", 38472),
    "F1SetupResponse_small": ("f1ap", "cu", "small", 38472),
}
NOT_RUN = ["VIRTUAL_RADIO_UE", "NAS_REGISTRATION", "PDU_SESSION",
           "F1U_TRAFFIC", "NGU_TRAFFIC", "FAULT_RECOVERY",
           "INTER_DU_MOBILITY", "DRB_QOS", "RF_CONFORMANCE"]


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
    observed = {key: frames(filter_expr(key.split("_")[0], *spec))
                for key, spec in MESSAGES.items()}
    failures = [filter_expr("NGSetupFailure", "ngap", "amf", "cu", 38412)]
    failures.extend(filter_expr("F1SetupFailure", "f1ap", "cu", role, 38472)
                    for role in ("macro", "small"))
    failed = frames(" || ".join("(" + expr + ")" for expr in failures))
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
    for req, rsp in (("NGSetupRequest","NGSetupResponse"),("F1SetupRequest_macro","F1SetupResponse_macro"),
                     ("F1SetupRequest_small","F1SetupResponse_small")):
        if min(observed[req]) >= min(observed[rsp]):
            raise EvidenceError("Setup response did not follow its request")


def render_config(path, role):
    docs = list(yaml.safe_load_all(path.read_text()))
    full = "hcran-srsran-" + role
    chart_role = "cu" if role == "cu" else "du"
    def one(kind, name):
        matches = [d for d in docs if isinstance(d,dict) and d.get("kind")==kind
                   and d.get("metadata",{}).get("name")==name]
        if len(matches)!=1:
            raise EvidenceError(f"Expected one {kind} {name}")
        return matches[0]
    dep = one("Deployment", full)
    if dep["metadata"]["labels"]["ran-type"] != "hcran" or dep["spec"]["template"]["metadata"]["labels"]["ran-type"] != "hcran":
        raise EvidenceError("Not the intended H-CRAN profile")
    raw = one("ConfigMap", full+"-config")["data"][chart_role+".yml"]
    raw = raw.replace("POD_IP",IP[role]).replace("AMF_IP",IP["amf"]).replace("CU_IP",IP["cu"])
    if any(v in raw for v in ("POD_IP","AMF_IP","CU_IP")):
        raise EvidenceError("Unresolved test addresses")
    cfg = yaml.safe_load(raw)
    cfg["log"]["filename"] = "stdout"
    cfg["log"]["all_level"] = "info"
    if role == "cu":
        if (cfg["cu_cp"]["amf"]["addr"] != IP["amf"]
                or cfg["cu_cp"]["amf"]["bind_addr"] != IP["cu"]
                or cfg["cu_cp"]["f1ap"]["bind_addr"] != IP["cu"]
                or cfg["gnb_id"] != 3585 or cfg["cu_cp"].get("max_nof_dus") != 2):
            raise EvidenceError("Invalid H-CRAN CU N2/F1-C/two-DU capacity")
        areas = cfg["cu_cp"]["amf"]["supported_tracking_areas"]
        if areas != [{"tac": 1, "plmn_list": [{"plmn": "20893",
                "tai_slice_support_list": [{"sst": 1, "sd": 66051}]}]}]:
            raise EvidenceError("CU 3GPP identity/slice mismatch")
        if cfg["cu_up"]["f1u"]["socket"] != [{"bind_addr": IP["cu"]}]:
            raise EvidenceError("CU F1-U must bind routable address")
        # H1 is signaling-only. NG-U and F1-U both default to UDP/2152;
        # binding both to the CU Pod IP crashes srscu before the DUs start.
        # Keep F1-U routable for later stages, but isolate unused NG-U on
        # loopback for this disposable, explicitly UE-free H1 trial ONLY.
        # H2+ MUST replace this with an independently routable N3 address.
        if "ngu" in cfg["cu_up"]:
            raise EvidenceError("Unexpected canonical NG-U override; review required")
        cfg["cu_up"]["ngu"] = {"socket": [{"bind_addr": "127.0.3.1"}]}
        if cfg["cu_up"]["ngu"]["socket"][0]["bind_addr"] == IP["cu"]:
            raise EvidenceError("NG-U/F1-U port 2152 bind collision")
    else:
        ident = {"macro": (100, 101, 1, 20, 75),
                 "small": (101, 102, 2, 10, 35)}[role]
        cell = cfg["cell_cfg"]
        got = (cfg["gnb_du_id"], cell["pci"], cell["sector_id"],
               cell["channel_bandwidth_MHz"], cfg["ru_sdr"]["tx_gain"])
        if got != ident or cfg["gnb_id"] != 3585:
            raise EvidenceError("Wrong DU ID / sector / PCI / role")
        if (cfg["f1ap"]["cu_cp_addr"] != IP["cu"]
                or cfg["f1ap"]["bind_addr"] != IP[role]
                or cfg["f1u"]["socket"] != [{"bind_addr": IP[role]}]
                or cfg["f1u"]["bind_port"] != 2152
                or cfg["ru_sdr"]["device_driver"] != "zmq"):
            raise EvidenceError("Wrong DU F1 transport")
        if (cell["plmn"] != "20893" or cell["tac"] != 1
                or cell["dl_arfcn"] != 632628 or cell["band"] != 78
                or cell["common_scs"] != 30):
            raise EvidenceError("DU PLMN/TAC/ARFCN/SCS drift")
        if "tx_port=tcp://127.0.0.1:2000,rx_port=tcp://127.0.0.1:2001" not in cfg["ru_sdr"]["device_args"]:
            raise EvidenceError("Unreviewed ZMQ RF transport")
        if role == "small":
            # Research-only H1 overlay for 10 MHz / 30 kHz n78 cell.
            # The pinned srsRAN DU rejects its default PUCCH resource
            # budget (>50% of 24-PRB BWP). Reduce HARQ/SR/CSI resources
            # without changing the canonical Helm profile or cell identity.
            # This is a startup candidate, NOT RF/UE capacity validation.
            if "pucch" in cell:
                raise EvidenceError("Canonical small-DU PUCCH configuration changed; review required")
            cell["pucch"] = {
                "nof_ue_res_harq_per_set": 2,
                "f0_or_f1_nof_cell_res_sr": 2,
                "f2_or_f3_or_f4_nof_cell_res_csi": 2,
            }
    if "e2" in cfg or "ric" in cfg:
        raise EvidenceError("H-CRAN research forbids Near-RT RIC/E2")
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
        args = ["-c","/tmp/hcransrs-config.yml"]
        if role in ("amf","nrf"):
            entry="/bin/sh"
            args=["-ec","exec open5gs-"+role+"d -c /tmp/hcransrs-config.yml"]
        else:
            entry=binary
        cmd("docker","run","-d","--pull=never","--name",NAMES[role],"--network",NET,
            "--ip",IP[role],"--memory","4g",
            "-v",str(config)+":/tmp/hcransrs-config.yml:ro",
            "--entrypoint",entry,image,*args)
        self.created.append(role)

    def run(self):
        private=self.out/"runner-local"
        private.mkdir(mode=0o700)
        for role in ("amf","nrf"):
            raw=(Path(self.a.stage3)/(role+".yaml")).read_text().replace("172.29.73.","172.29.78.")
            raw=raw.replace('mcc: "001"','mcc: "208"').replace('mnc: "01"','mnc: "93"')
            if role=="amf":
                raw=raw.replace("        - sst: 1",'        - sst: 1\n          sd: "010203"')
                if 'sd: "010203"' not in raw:
                    raise EvidenceError("Missing test slice")
            (private/(role+".yaml")).write_text(raw)
        for role in ("cu", "macro", "small"):
            cfg=render_config(Path(self.a.rendered)/(role+".yaml"),role)
            (private/(role+".yaml")).write_text(yaml.safe_dump(cfg,sort_keys=False))
        self.stage="ISOLATED_BRIDGE"
        cmd("docker","network","create","--driver","bridge","--internal",
            "--subnet","172.29.78.0/24","--opt","com.docker.network.bridge.name="+BRIDGE,NET)
        self.network=True
        self.stage="START_CAPTURE"
        capture=private/"setup.pcapng"
        # Unprivileged runner opens the private PCAP. dumpcap is permitted
        # to write only to inherited stdout, avoiding its prior EACCES on
        # private output paths while preserving a bounded 4MiB autostop.
        with capture.open("wb") as capture_stream, (private/"capture.log").open("w") as log:
            self.capture=subprocess.Popen(
                ["sudo","-n","dumpcap","-i",BRIDGE,"-f","sctp",
                 "-a","duration:100","-a","filesize:4096","-w","-"],
                stdout=capture_stream,stderr=log)
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
            self.stage="MACRO_START"
            self.launch("macro",self.a.srs_image,"/usr/local/bin/srsdu",private/"macro.yaml")
            time.sleep(3)
            self.live()
            self.stage="SMALL_START"
            self.launch("small",self.a.srs_image,"/usr/local/bin/srsdu",private/"small.yaml")
            self.stage="DUAL_F1_WIRE_OBSERVATION"
            deadline=time.monotonic()+105
            while self.capture.poll() is None and time.monotonic()<deadline:
                self.live()
                time.sleep(2)
            if self.capture.poll()!=0:
                raise EvidenceError("Capture incomplete or observer failed")
        self.live()
        self.stage="DECODE"
        result=decode(capture)
        self.stage="LIVE_ASSOCIATIONS"
        assoc=cmd("docker","exec",NAMES["cu"],"cat","/proc/net/sctp/assocs")
        for role in ("amf","macro","small"):
            if not re.search(r"(?<![\d.])"+re.escape(IP[role])+r"(?![\d.])",assoc):
                raise EvidenceError("CU missing live SCTP peer "+role)
        self.live()
        return {"schema":"hcransrs-dual-f1-wire-v1","scenario":"hcran-srsran",
                "status":"HCRANSRS_NGAP_BOTH_F1AP_WIRE_PASS_UE_NOT_TESTED",
                "scope":"one source-built CU and two independent DUs; NGAP plus both F1AP setups",
                "wire":result,"all_four_containers_alive":True,
                "amf_both_du_sctp_peers_observed":True,"not_run":NOT_RUN,
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
        receipt={"schema":"hcransrs-dual-f1-wire-v1","scenario":"hcran-srsran",
                 "status":"FAIL","stage":t.stage,"error_type":type(exc).__name__,
                 "not_run":NOT_RUN,"hp_cluster_touched":False,"production_decision":"NO_GO"}
        print(str(exc)[:2000],file=sys.stderr,flush=True)
    finally:
        try:
            t.close()
        except Exception as exc:
            print("Scoped cleanup failed: "+type(exc).__name__,file=sys.stderr)
            rc=1
            receipt={"schema":"hcransrs-dual-f1-wire-v1","scenario":"hcran-srsran",
                     "status":"FAIL","stage":"CLEANUP","not_run":NOT_RUN,
                     "hp_cluster_touched":False,"production_decision":"NO_GO"}
    (t.out/"receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    print(json.dumps(receipt,sort_keys=True),flush=True)
    return rc


if __name__=="__main__":
    raise SystemExit(main())
