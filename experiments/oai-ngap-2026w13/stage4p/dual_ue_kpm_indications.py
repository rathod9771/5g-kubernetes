#!/usr/bin/env python3
"""Stage 4P: real FlexRIC E2 Setup + KPM indications during two-UE user-plane.

Uses the proven Stage 4O test on a disposable GitHub runner, but substitutes
a pinned E2-enabled CU rebuilt with service-model plugins matching FlexRIC.
Real dual-DU NAS/PDU/ICMP/F1-U/NG-U gates MUST still pass. The KPM gate
requires 3+ actual indication callback records, not just E2 Setup or xApp
subscription. No HP cluster, radio device, OSM, production subscriber data.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import traceback
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"stage4o"), str(ROOT/"stage4k"), str(ROOT/"stage4")]
from run_two_ues import DualUEProbe
from run_ue_registration import IPS, name, sh
from e2ap_wire_evidence import parse_e2_setup_sctp

RIC_IP="172.29.76.1"
CU_IP=IPS["cu"]
REQ="E2 Setup Request"
# Only actual indications in xApp output count; subscription response alone
# MUST NOT satisfy this. An xApp can subscribe with no UE metrics delivered.
INDICATION_LINE=re.compile(
    r"(?:\bRIC[_ -]?INDICATION(?:[_ -]?rx|[_ -]?received|[_ -]?message)?\b"
    r"|\bKPM[_ -]?indication(?:[_ -]?received)?\b"
    r"|\bKPM[_ -]?callback\b)",re.I)

class DualUEKPM(DualUEProbe):
    def __init__(self,options):
        super().__init__(options)
        self.ric=None
        self.ric_out=None
        self.sctp_capture=None

    def ran(self):
        rendered=pathlib.Path(self.opts.rendered)
        shadow=self.priv/"e2-rendered"
        shadow.mkdir(mode=0o700,exist_ok=True)
        for cell in ("cu","macro","small"):
            shutil.copy2(rendered/(cell+".yaml"),shadow/(cell+".yaml"))
        docs=list(yaml.safe_load_all((shadow/"cu.yaml").read_text()))
        configs=[x for x in docs if isinstance(x,dict) and
                 x.get("kind")=="ConfigMap" and
                 x.get("metadata",{}).get("name")=="hcran-oai-cu-config"]
        if len(configs)!=1:
            raise RuntimeError("Expected canonical H-CRAN CU ConfigMap once")
        cfg=configs[0]["data"]["gnb.conf"]
        if "e2_agent" in cfg:
            raise RuntimeError("E2 settings already exist in profile; fail closed")
        configs[0]["data"]["gnb.conf"] = cfg + (
            '\ne2_agent = {\n'
            f'  near_ric_ip_addr = "{RIC_IP}";\n'
            '  sm_dir = "/usr/local/lib/flexric/";\n'
            '};\n')
        (shadow/"cu.yaml").write_text(yaml.safe_dump_all(docs,sort_keys=False))
        self.opts.rendered=str(shadow)
        self.ric_out=(self.out/"near-rt-ric.log").open("w",encoding="utf-8")
        self.ric=subprocess.Popen(
            ["stdbuf","-oL","-eL",self.opts.ric_binary,"-a",RIC_IP],
            stdout=self.ric_out,stderr=subprocess.STDOUT,
            start_new_session=True)
        time.sleep(3)
        if self.ric.poll() is not None:
            raise RuntimeError("Pinned FlexRIC near-RT RIC failed before CU startup")
        self.record("T31-pinned-nearRT-RIC-started","PASS",ip=RIC_IP)
        self.sctp_capture=subprocess.Popen(
            ["sudo","tcpdump","-i","any","-s","0","-U","-w",
             str(self.out/"e2-setup-sctp.pcap"),"sctp"],
            stdout=subprocess.DEVNULL,stderr=(self.out/"tcpdump-e2.log").open("w"),
            start_new_session=True)
        time.sleep(1)
        if self.sctp_capture.poll() is not None:
            raise RuntimeError("E2 SCTP packet capture could not start")
        super().ran() # real NG Setup + both DU F1 Setup
        limit=time.monotonic()+45
        last={}
        while time.monotonic()<limit:
            self.demand("core","cu","macro","small")
            if self.ric.poll() is not None:
                raise RuntimeError("FlexRIC exited during dual-DU E2 Setup")
            try:
                last=parse_e2_setup_sctp(self.out/"e2-setup-sctp.pcap",CU_IP,RIC_IP)
            except Exception as exc:
                raise RuntimeError("E2 Setup wire decoder unavailable: "+str(exc))
            if last["e2_setup_requests"] and last["e2_setup_responses"]:
                self.record("T32-real-E2AP-Setup-Request-and-Response","PASS",
                            request_count=len(last["e2_setup_requests"]),
                            response_count=len(last["e2_setup_responses"]))
                (self.out/"e2-setup-wire-evidence.json").write_text(
                    json.dumps(last,indent=2)+"\n")
                self.stop_sctp_capture()
                return
            time.sleep(2)
        raise RuntimeError("Failed to observe E2AP Setup Request and successful Response on real SCTP")

    def stop_sctp_capture(self):
        if self.sctp_capture and self.sctp_capture.poll() is None:
            self.sctp_capture.terminate()
            try:self.sctp_capture.wait(timeout=7)
            except subprocess.TimeoutExpired:self.sctp_capture.kill()
        self.sctp_capture=None

    def ue(self):
        super().ue() # both simultaneous UEs and F1-U/NG-U data MUST pass
        self.demand("core","cu","macro","small","ue","ue-small")
        if self.ric is None or self.ric.poll() is not None:
            raise RuntimeError("nearRT-RIC not running after real dual-UE data path")
        out=self.out/"kpm-indications-xapp.log"
        env=os.environ.copy()
        env["DB_DIR"]=str(self.out)
        env["XAPP_DURATION"]="20"
        with out.open("w",encoding="utf-8") as sink:
            xapp=subprocess.Popen(
                ["stdbuf","-oL","-eL",self.opts.kpm_xapp,"-a",RIC_IP],
                stdout=sink,stderr=subprocess.STDOUT,env=env,
                start_new_session=True)
            try:
                time.sleep(2)
                # Keep both registered UEs actively transmitting while
                # monitoring runs; no external network or UE secrets.
                pings=[]
                for label in ("ue","ue-small"):
                    pings.append((label,subprocess.Popen(
                        ["docker","exec",name(label),"ping","-I","oaitun_ue1",
                         "-c","14","-i","0.7","-W","2","10.45.0.1"],
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)))
                for label,proc in pings:
                    data,_=proc.communicate(timeout=30)
                    (self.out/(label+"-during-KPM-ICMP.txt")).write_text(data)
                    if proc.returncode!=0 or not re.search(
                        r"14 packets transmitted, 14 (?:packets )?received",data):
                        raise RuntimeError(label+" lost user-plane ICMP during KPM xApp")
                self.record("T33-real-dual-UE-traffic-during-KPM-monitor","PASS",
                            each_packets=14,each_replies=14)
                xapp.wait(timeout=60)
            finally:
                if xapp.poll() is None:
                    xapp.terminate()
                    try:xapp.wait(timeout=7)
                    except subprocess.TimeoutExpired:xapp.kill()
        log=out.read_text(encoding="utf-8",errors="replace")
        if xapp.returncode!=0:
            raise RuntimeError("KPM xApp failed: "+log[-2000:])
        if not re.search(r"SUBSCRIPTION RESPONSE rx|Successfully subscribed",log,re.I):
            raise RuntimeError("KPM RIC Subscription Response absent")
        self.record("T34-KPM-subscription-with-two-active-UEs","PASS")
        lines=[line for line in log.splitlines() if INDICATION_LINE.search(line)]
        result={"stage":"4P","scope":"real dual-UE H-CRAN KPM",
                "xapp_exit":xapp.returncode,
                "actual_indication_lines":lines[:100],
                "observed_indication_count":len(lines),
                "two_active_ues":True,
                "production_approval":"NO_GO",
                "notes":"Indication callbacks required; subscription alone is not proof"}
        (self.out/"kpm-evidence.json").write_text(json.dumps(result,indent=2)+"\n")
        if len(lines)<3:
            raise RuntimeError(
                f"KPM actual indications not demonstrated: {len(lines)} < 3, "
                "despite xApp subscription; see redacted KPM log")
        self.record("T35-KPM-indication-callbacks-with-two-UEs","PASS",
                    count=len(lines))
        self.capture("after-real-KPM")
        self.record("STAGE4P","PASS",
                    scope="E2 Setup + dual-UE data + 3 real KPM indication callbacks")

    def cleanup(self):
        self.stop_sctp_capture()
        if self.ric and self.ric.poll() is None:
            self.ric.terminate()
            try:self.ric.wait(timeout=6)
            except subprocess.TimeoutExpired:self.ric.kill()
        if self.ric_out:self.ric_out.close()
        super().cleanup()

def main():
    parser=argparse.ArgumentParser()
    for name_ in ("rendered","output","cu-image","du-image","ue-image",
                  "ric-binary","kpm-xapp"):
        parser.add_argument("--"+name_,required=True)
    parser.add_argument("--verify-gtpu",action="store_true")
    args=parser.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: real GTP-U proof required")
    return DualUEKPM(args).run()

if __name__=="__main__":
    sys.exit(main())
