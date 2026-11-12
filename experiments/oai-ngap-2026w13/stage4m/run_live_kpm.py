#!/usr/bin/env python3
"""Real OAI H-CRAN UE/5GC plus FlexRIC E2SM-KPM indication attempt.

Starts no Kubernetes/OSM, no physical RF and no real subscribers. The
indication gate fails unless actual KPM monitor output contains >=3
indication events AFTER subscription while an attached UE sends traffic.
"""
import argparse, datetime, json, os
from pathlib import Path
import re, subprocess, sys, time, traceback
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"stage4k"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"stage4"))
from run_ue_registration import Probe, IPS, sh
from e2ap_wire_evidence import parse_e2_setup_sctp

RIC_IP="172.29.76.1"
PAT_IND=re.compile(r"(?:RIC[_ -]?INDICATION(?: rx| received| message)|KPM indication(?: received)?|indication.*RAN_FUNC_ID)",re.I)
SUB_OK=re.compile(r"Successfully subscribed to RAN_FUNC_ID 2|SUBSCRIPTION RESPONSE rx",re.I)

class E2Trial:
    def __init__(self,args):
        self.args=args
        self.probe=Probe(argparse.Namespace(
            rendered=args.rendered,output=args.output,cu_image=args.cu_image,
            du_image=args.du_image,ue_image=args.ue_image,
            verify_gtpu=False,ric_ip=RIC_IP))
        self.out=self.probe.out
        self.ric=None
        self.ric_file=None
        self.pcap=None
        self.pcap_file=None
    def start_ric(self):
        self.ric_file=(self.out/"nearRT-RIC.log").open("w")
        self.ric=subprocess.Popen(["stdbuf","-oL","-eL",self.args.ric_binary,
                                   "-a",RIC_IP],
                                  stdout=self.ric_file,stderr=subprocess.STDOUT)
        time.sleep(3)
        if self.ric.poll() is not None:
            raise RuntimeError("FlexRIC RIC exited before E2 CU startup")
        self.pcap_file=(self.out/"e2-tcpdump.log").open("w")
        self.pcap=subprocess.Popen(["sudo","tcpdump","-i","any","-s","0","-U",
                                    "-w",str(self.out/"e2-sctp.pcap"),"sctp"],
                                   stdout=subprocess.DEVNULL,stderr=self.pcap_file)
        time.sleep(1)
        if self.pcap.poll() is not None:
            raise RuntimeError("E2 SCTP pcap capture did not start")
        self.probe.record("T0-pinned-FlexRIC-started","PASS")
    def wait_e2(self):
        until=time.monotonic()+45
        observed=None
        while time.monotonic()<until:
            if self.ric.poll() is not None:
                raise RuntimeError("FlexRIC exited before E2 Setup completed")
            self.probe.demand("cu","macro","core")
            try:
                observed=parse_e2_setup_sctp(self.out/"e2-sctp.pcap",IPS["cu"],RIC_IP)
            except Exception:
                time.sleep(2)
                continue
            if observed["e2_setup_requests"] and observed["e2_setup_responses"]:
                (self.out/"E2AP-wire-evidence.json").write_text(
                    json.dumps(observed,indent=2)+"\n")
                self.probe.record("T1-real-E2Setup-Request-and-Response","PASS",
                                  request_frames=len(observed["e2_setup_requests"]),
                                  response_frames=len(observed["e2_setup_responses"]))
                return
            time.sleep(2)
        (self.out/"E2AP-wire-evidence.json").write_text(json.dumps(observed,indent=2)+"\n"
                                                    if observed else "{}\n")
        raise RuntimeError("No confirmed real E2 Setup Request+Response with attached macro DU")
    def kpm_test(self):
        # xApp is intentionally started only after UE has registered and
        # established a PDU session, unlike the earlier UE-free smoke test.
        log=self.out/"kpm-xapp-with-ue.log"
        env=os.environ.copy()
        env["DB_DIR"]=str(self.out)
        env["XAPP_DURATION"]="24"
        with log.open("w") as fp:
            app=subprocess.Popen(["stdbuf","-oL","-eL",self.args.kpm_binary,
                                  "-a",RIC_IP],env=env,stdout=fp,
                                 stderr=subprocess.STDOUT)
            time.sleep(4)
            self.probe.demand("core","cu","macro","ue")
            ping=sh("docker","exec","oai-ngap-stage4k-ue","/bin/sh","-c",
                    "ping -I oaitun_ue1 -c 12 -i 1 -W 2 10.45.0.1",
                    check=False,timeout=50)
            (self.out/"UE-ping-during-KPM.txt").write_text(ping[-4500:])
            try:status=app.wait(timeout=65)
            except subprocess.TimeoutExpired:
                app.terminate()
                status=app.wait(timeout=10)
        output=log.read_text(errors="replace")
        subscribe=bool(SUB_OK.search(output))
        # Count only indications that occur after a real subscription.
        lines=output.splitlines()
        start=next((i for i,x in enumerate(lines) if SUB_OK.search(x)),len(lines))
        indications=[x[:400] for x in lines[start+1:] if PAT_IND.search(x)]
        report={"xapp_exit_code":status,"subscription_accepted":subscribe,
                "indication_count":len(indications),"indication_samples":indications[:8],
                "ping_zero_loss":"0% packet loss" in ping,
                "e2_function":"E2SM-KPM RAN_FUNC_ID 2",
                "rc_control_effect":"NOT_VERIFIED"}
        (self.out/"KPM-indications-evidence.json").write_text(json.dumps(report,indent=2)+"\n")
        if status!=0 or not subscribe:
            raise RuntimeError(f"KPM xApp failed to subscribe, exit code={status}")
        self.probe.record("T2-real-KPM-xApp-subscription-with-UE","PASS")
        if "0% packet loss" not in ping:
            raise RuntimeError("UE->UPF traffic failed during KPM observation")
        self.probe.record("T3-bidirectional-UE-traffic-during-KPM","PASS")
        if len(indications)<3:
            raise RuntimeError("Fewer than three genuine KPM indication events: "+
                               str(len(indications)))
        self.probe.record("T4-at-least-three-real-KPM-indications","PASS",
                          indications=len(indications))
    def run(self):
        try:
            self.probe.bootstrap()
            self.start_ric()
            self.probe.ran()
            self.wait_e2()
            self.probe.ue()
            self.kpm_test()
            self.probe.record("STAGE4M","PASS",
                              scope="real attached UE, E2 Setup, KPM subscription & indications; RC effect untested")
            return 0
        except Exception as e:
            self.probe.record("STAGE4M","FAIL",reason=str(e)[-3000:])
            (self.out/"exception-stage4m.txt").write_text(traceback.format_exc()[-7000:])
            return 1
        finally:
            if self.pcap is not None and self.pcap.poll() is None:
                self.pcap.terminate()
                try:self.pcap.wait(timeout=7)
                except subprocess.TimeoutExpired:self.pcap.kill()
            if self.pcap_file:self.pcap_file.close()
            if self.ric is not None and self.ric.poll() is None:
                self.ric.terminate()
                try:self.ric.wait(timeout=7)
                except subprocess.TimeoutExpired:self.ric.kill()
            if self.ric_file:self.ric_file.close()
            self.probe.cleanup()

def main():
    p=argparse.ArgumentParser()
    for field in ("rendered","output","cu-image","du-image","ue-image",
                  "ric-binary","kpm-binary"):
        p.add_argument("--"+field,required=True)
    return E2Trial(p.parse_args()).run()
if __name__=="__main__":sys.exit(main())
