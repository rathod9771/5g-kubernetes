#!/usr/bin/env python3
"""Stage 4R: actual E2SM-KPM indications with dual H-CRAN UE user-plane.

Combines *pinned matching FlexRIC KPM+RC service models* and the working
Stage 4O two-UE/2-DU Open5GS test on one isolated GitHub-hosted runner.
Builds a real SCTP/E2AP CU-to-RIC link and requires wire-observed E2 Setup.
Then each DU carries an actual UE PDU; KPM xApp subscribes during active
traffic. At least 3 callback-produced KPM indications are mandatory.
No RC control, HP cluster access, OSM, real IMSIs, or production promotion.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import yaml

ROOT=pathlib.Path(__file__).resolve().parents[1]
for part in ("stage4o","stage4m","stage4k","stage4"):
    sys.path.insert(0,str(ROOT/part))
from run_two_ues import DualUEProbe, SMALL_IP
from run_ue_registration import IPS, name, sh
from e2ap_wire_evidence import parse_e2_setup_sctp

RIC_IP="172.29.76.1"
KPM_IND=re.compile(r"\bKPM ind_msg latency\s*=\s*\d+", re.I)

class KpmDualUeProbe(DualUEProbe):
    def bootstrap(self):
        super().bootstrap()
        self.ric_process=None
        self.ric_file=None
        self.e2_capture=None

        # Only a rendered disposable Helm manifest is changed; tracked CU
        # Helm profiles and production reference image policy stay unchanged.
        temp=pathlib.Path(self.opts.rendered)/"cu.yaml"
        docs=list(yaml.safe_load_all(temp.read_text()))
        cmlist=[d for d in docs if isinstance(d,dict) and
                d.get("kind")=="ConfigMap" and
                d.get("metadata",{}).get("name")=="hcran-oai-cu-config"]
        if len(cmlist)!=1:
            raise RuntimeError("Canonical H-CRAN CU ConfigMap missing")
        cm=cmlist[0]
        conf=cm["data"]["gnb.conf"]
        if "e2_agent" in conf:
            raise RuntimeError("Unexpected E2 block in canonical H-CRAN CU")
        cm["data"]["gnb.conf"]=conf+f'''
e2_agent = {{
  near_ric_ip_addr = "{RIC_IP}";
  sm_dir = "/usr/local/lib/flexric/";
}};
'''
        temp.write_text(yaml.safe_dump_all(docs,sort_keys=False))
        self.record("T40-canonical-CU-ephemeral-E2-configuration","PASS",
                    ric_ip=RIC_IP,source="temporary Helm-render only")

        self.e2_capture=subprocess.Popen(
           ["sudo","tcpdump","-i","any","-s","0","-U","-w",
            str(self.out/"e2ap-sctp.pcap"),"sctp"],
           stdout=subprocess.DEVNULL,
           stderr=(self.out/"e2-tcpdump.log").open("w"),
           start_new_session=True)
        time.sleep(1)
        if self.e2_capture.poll() is not None:
            raise RuntimeError("E2 capture could not start")

        self.ric_file=(self.out/"near-rt-ric-live.log").open("w")
        self.ric_process=subprocess.Popen(
            ["stdbuf","-oL","-eL",self.opts.ric_binary,"-a",RIC_IP],
            stdout=self.ric_file,stderr=subprocess.STDOUT,
            start_new_session=True)
        time.sleep(3)
        if self.ric_process.poll() is not None:
            self.ric_file.flush()
            raise RuntimeError("FlexRIC terminated before CU startup: "+
                               (self.out/"near-rt-ric-live.log").read_text()[-2400:])
        self.record("T41-pinned-FlexRIC-near-RT-RIC-started","PASS",e2_port=36421)

    def ric_alive(self):
        if self.ric_process is None or self.ric_process.poll() is not None:
            raise RuntimeError("near-RT RIC terminated during E2/UE experiment")

    def ran(self):
        # Starts patched E2-enabled CU, H-CRAN DU 100 and DU 101.
        super().ran()
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            self.ric_alive()
            self.demand("core","cu","macro","small")
            wire=parse_e2_setup_sctp(self.out/"e2ap-sctp.pcap",IPS["cu"],RIC_IP)
            sctp=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                    timeout=20)
            if wire["e2_setup_requests"] and wire["e2_setup_responses"] and RIC_IP in sctp:
                self.record("T42-real-CU-RIC-E2-Setup-Request-and-Response","PASS",
                            request_frames=len(wire["e2_setup_requests"]),
                            response_frames=len(wire["e2_setup_responses"]))
                (self.out/"e2ap-wire-evidence.json").write_text(
                    json.dumps(wire,indent=2)+"\n")
                return
            time.sleep(3)
        raise RuntimeError("E2 Setup Request/Response or RIC SCTP assoc missing in dual-DU environment")

    def ue(self):
        super().ue()  # Two real NAS/PDU sessions and both F1-U and NG-U.
        self.ric_alive()
        self.demand("core","cu","macro","small","ue","ue-small")
        self.record("T43-dual-UE-PDU-and-four-direction-GTPU-with-E2","PASS")

        # Trigger actual user-plane traffic for a 30s xApp KPM sampling
        # window. Do not infer KPM indications from accepted subscription.
        traffic=[]
        for who in ("ue","ue-small"):
            traffic.append((who,subprocess.Popen(
                ["docker","exec",name(who),"ping","-I","oaitun_ue1",
                 "-c","48","-i","0.5","-W","2","10.45.0.1"],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)))
        env=os.environ.copy()
        env["XAPP_DURATION"]="30"
        stdout_file=self.out/"KPM-live-dual-UE-xApp.log"
        try:
            with stdout_file.open("w") as out:
                p=subprocess.run(["stdbuf","-oL","-eL",self.opts.xapp_kpm,
                                  "-a",RIC_IP],
                                 stdout=out,stderr=subprocess.STDOUT,
                                 env=env,timeout=85,check=False)
            rc=p.returncode
        finally:
            for who,ping in traffic:
                try:out,_=ping.communicate(timeout=40)
                except subprocess.TimeoutExpired:
                    ping.kill()
                    out,_=ping.communicate(timeout=8)
                (self.out/(who+"-KPM-window-ping.txt")).write_text(out[-4500:])
        self.ric_alive()
        logs=stdout_file.read_text(errors="replace")
        subscriptions=logs.count("Successfully subscribed to RAN_FUNC_ID 2")
        indications=KPM_IND.findall(logs)
        self.record("T44-KPM-xApp-subscription", "PASS" if subscriptions>=1 and rc==0 else "FAIL",
                    subscribed=subscriptions,exit_code=rc)
        # Intentionally reject log messages merely mentioning KPM support.
        # These callbacks must include numeric latency from a real
        # successfully decoded RIC INDICATION event.
        evidence={
            "scope":"real pinned FlexRIC KPM observations under two concurrent H-CRAN UE sessions",
            "RIC_ip":RIC_IP, "CU_image":self.opts.cu_image,
            "KPM_indication_callbacks":len(indications),
            "KPM_subscription_responses":subscriptions,
            "KPM_xapp_exit_code":rc,
            "actual_UE_count":2,"actual_DU_count":2,
            "production_decision":"NO_GO",
            "limitations":["RC control effect untested",
                           "staging rollback untested",
                           "sustained SLO not demonstrated"],
        }
        (self.out/"dual-ue-kpm-evidence.json").write_text(json.dumps(evidence,indent=2)+"\n")
        if rc!=0 or subscriptions<1:
            raise RuntimeError("KPM xApp failed or did not subscribe: "+logs[-2400:])
        if len(indications)<3:
            raise RuntimeError("No >=3 actual UE-level KPM indications; "
                               "E2 Setup and subscription are insufficient: "+
                               f"received {len(indications)}; xApp tail={logs[-2000:]}")
        self.record("T45-real-KPM-indications-with-both-UEs","PASS",
                    count=len(indications))
        self.demand("core","cu","macro","small","ue","ue-small")
        self.capture("dual-UE-KPM-final")
        self.record("STAGE4R","PASS",
                    scope="real dual UE data + E2 Setup + >=3 KPM indications, no RC control")

    def cleanup(self):
        if getattr(self,"ric_process",None) is not None and self.ric_process.poll() is None:
            self.ric_process.terminate()
            try:self.ric_process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.ric_process.kill()
        if getattr(self,"ric_file",None) is not None:self.ric_file.close()
        if getattr(self,"e2_capture",None) is not None and self.e2_capture.poll() is None:
            self.e2_capture.terminate()
            try:self.e2_capture.wait(timeout=8)
            except subprocess.TimeoutExpired:self.e2_capture.kill()
        super().cleanup()

def main():
    p=argparse.ArgumentParser()
    for arg in ("rendered","output","cu-image","du-image","ue-image",
                "ric-binary","xapp-kpm"):
        p.add_argument("--"+arg,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    opts=p.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu mandatory")
    return KpmDualUeProbe(opts).run()
if __name__=="__main__":
    sys.exit(main())
