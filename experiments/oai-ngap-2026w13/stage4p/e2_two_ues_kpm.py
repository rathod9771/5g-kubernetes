#!/usr/bin/env python3
"""Stage 4P: real FlexRIC E2 Setup + KPM xApp and two real RFsim UE sessions.

All traffic comes from distinct synthetic MongoDB subscribers, macro DU 100
and small DU 101. The patched *E2-enabled* OAI CU image is built from the
matching pinned FlexRIC KPM/RC libraries. Do not treat subscriptions as
indications. Fail closed on <3 actual KPM indications.

No live Kubernetes, no OSM, no hardware access, no real subscriber secrets.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import traceback

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"stage4o"))
sys.path.insert(0,str(ROOT/"stage4"))
sys.path.insert(0,str(ROOT/"stage4k"))
from run_two_ues import DualUEProbe
from run_ue_registration import IPS, name, sh
from e2ap_wire_evidence import parse_e2_setup_sctp

RIC_IP="172.29.76.1"

def genuine_kpm_indication_lines(log):
    """Require event receive/measurement, never service-model advertisement."""
    candidates=[]
    for line in log.splitlines():
        # Subscription setup/definitions are NOT received indications.
        if re.search(r"(?:SUBSCRIPTION|subscrib|RAN_FUNC|SM ID|def =|E2SM-KPM)", line,re.I):
            continue
        if re.search(r"(?:RIC[_ -]?INDICATION\s*(?:rx|received)|"
                     r"(?:KPM|meas(?:urement)?)\s*indication\s*(?:rx|received)|"
                     r"indication\s*callback)",line,re.I):
            candidates.append(line[:400])
    return candidates

class LiveE2Probe(DualUEProbe):
    def __init__(self,args):
        args.ric_ip=RIC_IP
        super().__init__(args)
        self.ric=None
        self.ric_fp=None
        self.e2cap=None
        self.e2err=None

    def bootstrap(self):
        super().bootstrap()
        bridge=sh("docker","network","inspect", "-f",
                  "{{(index .IPAM.Config 0).Gateway}}", "oai-ngap-stage4k",
                  timeout=20).strip()
        if bridge!=RIC_IP:
            raise RuntimeError("E2 RIC gateway mismatch: "+bridge)
        self.ric_fp=(self.out/"pinned-nearRT-RIC.log").open("w",encoding="utf-8")
        self.e2err=(self.out/"sctp-tcpdump.stderr.txt").open("w",encoding="utf-8")
        self.e2cap=subprocess.Popen(
            ["sudo","tcpdump","-i","any","-nn","-s","0","-U",
             "-w",str(self.out/"e2ap-setup-sctp.pcap"),"sctp"],
            stdout=subprocess.DEVNULL,stderr=self.e2err)
        time.sleep(1)
        if self.e2cap.poll() is not None:
            raise RuntimeError("SCTP capture cannot start on GitHub runner")
        self.ric=subprocess.Popen(
            ["stdbuf","-oL","-eL",self.opts.ric_binary,"-a",RIC_IP],
            stdout=self.ric_fp,stderr=subprocess.STDOUT)
        time.sleep(3)
        if self.ric.poll() is not None:
            raise RuntimeError("FlexRIC nearRT-RIC failed to start (see RIC log)")
        self.record("T31-pinned-FlexRIC-nearRT-RIC-started","PASS",gateway=RIC_IP)

    def require_ric(self):
        if self.ric is None or self.ric.poll() is not None:
            raise RuntimeError("Pinned FlexRIC nearRT-RIC exited during test")

    def ran(self):
        self.require_ric()
        super().ran() # Real Open5GS NGAP + both H-CRAN F1-C DUs
        deadline=time.monotonic()+80
        wire=None
        while time.monotonic()<deadline:
            self.require_ric()
            self.demand("core","cu","macro","small")
            wire=parse_e2_setup_sctp(self.out/"e2ap-setup-sctp.pcap",
                                    IPS["cu"],RIC_IP)
            if wire["e2_setup_requests"] and wire["e2_setup_responses"]:
                break
            time.sleep(2)
        if not wire or not wire["e2_setup_requests"] or not wire["e2_setup_responses"]:
            raise RuntimeError("No real E2AP Setup Request and successfulOutcome on SCTP")
        sctp=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",timeout=20)
        if RIC_IP not in sctp:
            raise RuntimeError("E2 CU SCTP peer to nearRT-RIC not established")
        (self.out/"e2-setup-wire-evidence.json").write_text(json.dumps(wire,indent=2)+"\n")
        self.record("T32-E2-CU-real-Setup-Request-and-Response","PASS",
                    request_frames=len(wire["e2_setup_requests"]),
                    response_frames=len(wire["e2_setup_responses"]))
        self.capture("e2-and-dual-f1")

    def ue(self):
        self.require_ric()
        super().ue() # Two distinct UEs, both F1-U, NG-U, PDU + concurrent ICMP
        self.require_ric()
        self.demand("core","cu","macro","small","ue","ue-small")
        app_log=self.out/"KPM-live-xapp.log"
        traffic=[]
        try:
            for ue in ("ue","ue-small"):
                traffic.append(subprocess.Popen(
                    ["docker","exec",name(ue),"ping","-I","oaitun_ue1",
                     "-c","48","-i","0.6","-W","2","10.45.0.1"],
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True))
            with app_log.open("w",encoding="utf-8") as fp:
                try:
                    p=subprocess.run(
                        ["stdbuf","-oL","-eL",self.opts.xapp_kpm,"-a",RIC_IP],
                        stdout=fp,stderr=subprocess.STDOUT,
                        timeout=80,check=False)
                    code=p.returncode
                except subprocess.TimeoutExpired:
                    code=124
            self.require_ric()
            logs=app_log.read_text(encoding="utf-8",errors="replace")
            subscribed=("Successfully subscribed to RAN_FUNC_ID 2" in logs
                        and "SUBSCRIPTION RESPONSE rx" in logs)
            indications=genuine_kpm_indication_lines(logs)
            self.record("T33-KPM-xApp-subscription","PASS" if subscribed else "FAIL",
                        exit_code=code,subscriptions_observed=subscribed)
            # Real indications are a separate, explicitly positive criterion.
            report={"kpm_subscription":subscribed,
                    "indication_lines":indications[:50],
                    "indication_count":len(indications),
                    "xapp_exit":code,
                    "e2_ric_ip":RIC_IP,
                    "two_ues_active":True,
                    "production_release":"NO_GO"}
            (self.out/"KPM-indication-assessment.json").write_text(
                json.dumps(report,indent=2)+"\n")
            if code!=0 or not subscribed:
                raise RuntimeError("KPM xApp subscription/execution failed; read KPM-live-xapp.log")
            if len(indications)<3:
                raise RuntimeError("KPM xApp subscribed but fewer than 3 actual indications "
                                   f"were evidenced ({len(indications)}). Do not claim telemetry.")
            self.record("T34-real-KPM-indications-with-two-active-UEs","PASS",
                        count=len(indications))
            self.record("STAGE4P","PASS",
                        scope="E2-enabled CU + dual DU/UE traffic and >=3 live KPM indications")
        finally:
            for proc in traffic:
                if proc.poll() is None:proc.terminate()
                try:
                    proc.communicate(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=5)

    def cleanup(self):
        try:
            self.capture("e2-telemetry-cleanup")
        except Exception:
            pass
        if self.ric is not None and self.ric.poll() is None:
            self.ric.terminate()
            try:self.ric.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.ric.kill()
                self.ric.wait(timeout=5)
        if self.e2cap is not None and self.e2cap.poll() is None:
            self.e2cap.terminate()
            try:self.e2cap.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.e2cap.kill()
                self.e2cap.wait(timeout=5)
        if self.ric_fp is not None:self.ric_fp.close()
        if self.e2err is not None:self.e2err.close()
        super().cleanup()

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image","ric-binary","xapp-kpm"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    opts=p.parse_args()
    if not opts.verify_gtpu:raise SystemExit("FAIL CLOSED: --verify-gtpu mandatory")
    return LiveE2Probe(opts).run()

if __name__=="__main__":raise SystemExit(main())
