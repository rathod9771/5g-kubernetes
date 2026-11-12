#!/usr/bin/env python3
"""Stage 4P experimental live E2SM-KPM indications with two attached RFsim UEs.

Uses Stage 4O real dual UE/PDU/user-plane baseline and matching FlexRIC
nearRT-RIC + E2-enabled CU. Must see genuine E2AP RICindication wire
messages, successful RAN function-2 xApp subscription and xApp callback
evidence. A green E2 Setup alone is insufficient. No HP or production access.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time
import traceback
import yaml

STAGE4=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STAGE4/"stage4o"))
sys.path.insert(0,str(STAGE4/"stage4k"))
sys.path.insert(0,str(STAGE4/"stage4"))
from run_two_ues import DualUEProbe
from run_ue_registration import IPS, NETWORK, name, sh
from e2ap_wire_evidence import parse_e2_setup_sctp

RIC_IP="172.29.76.1"
CU_IP=IPS["cu"]

def e2_indications(pcap):
    """Count CU->RIC SCTP DATA whose E2AP initiating procedure is RICindication.

    FlexRIC uses SCTP PPID 0; Wireshark does not auto-dissect these messages.
    This checks the bounded E2AP PER envelope and source+destination IPs.
    xApp callback evidence is separately mandatory before PASS.
    """
    import ipaddress
    import struct
    data=pathlib.Path(pcap).read_bytes()
    if len(data)<24 or data[:4]!=bytes.fromhex("d4c3b2a1"):
        raise RuntimeError("E2 SCTP packet capture unavailable or malformed")
    link=struct.unpack_from("<I",data,20)[0]
    if link!=276:
        raise RuntimeError("Expected tcpdump Linux SLL2 frames")
    off=24
    frames=0
    indications=[]
    while off+16<=len(data):
        _,_,size,_=struct.unpack_from("<IIII",data,off)
        off+=16
        if size>262144 or off+size>len(data):break
        frame=data[off:off+size];off+=size;frames+=1
        if len(frame)<52 or frame[:2]!=b"\x08\x00":continue
        ip=frame[20:]
        if ip[0]>>4!=4 or ip[9]!=132:continue
        if str(ipaddress.IPv4Address(ip[12:16]))!=CU_IP:continue
        if str(ipaddress.IPv4Address(ip[16:20]))!=RIC_IP:continue
        ihl=(ip[0]&15)*4
        sctp=ip[ihl:]
        pos=12
        while pos+4<=len(sctp):
            typ,_,ln=struct.unpack_from("!BBH",sctp,pos)
            if ln<4 or pos+ln>len(sctp):break
            if typ==0 and ln>=16:
                body=sctp[pos+4:pos+ln]
                ppid=int.from_bytes(body[8:12],"big")
                payload=body[12:]
                if ppid in (0,70) and len(payload)>=2 and payload[:2]==b"\x00\x05":
                    indications.append({"frame":frames,"bytes":len(payload)})
            pos+=(ln+3)&~3
    return {"candidate_ricindications":indications,"sctp_frames":frames}

def render_e2_cu(rendered):
    # Only edit ephemeral Helm-rendered ConfigMap, never charts or HP cluster.
    src=pathlib.Path(rendered)/"cu.yaml"
    docs=list(yaml.safe_load_all(src.read_text()))
    matches=[d for d in docs if d and d.get("kind")=="ConfigMap"
             and d.get("metadata",{}).get("name")=="hcran-oai-cu-config"]
    if len(matches)!=1:raise RuntimeError("Expected one H-CRAN CU ConfigMap")
    gnb=matches[0]["data"]["gnb.conf"]
    if "e2_agent" in gnb:raise RuntimeError("Refusing to duplicate e2_agent config")
    gnb+='\ne2_agent = {\n  near_ric_ip_addr = "'+RIC_IP+'";\n'
    gnb+='  sm_dir = "/usr/local/lib/flexric/";\n};\n'
    matches[0]["data"]["gnb.conf"]=gnb
    src.write_text(yaml.safe_dump_all(docs,sort_keys=False))

class KpmWithUEs(DualUEProbe):
    def __init__(self,args):
        super().__init__(args)
        self.ric=None
        self.ric_file=None
        self.e2_tcpdump=None
        self.e2_capture_file=None
        self.kpm_proc=None
        self.kpm_file=None
        self.pcap_starts=0

    def bootstrap(self):
        super().bootstrap()
        gateway=sh("docker","network","inspect","-f",
                   "{{(index .IPAM.Config 0).Gateway}}",NETWORK).strip()
        if gateway!=RIC_IP:
            raise RuntimeError("Docker bridge default gateway changed: "+gateway)
        self.e2_capture_file=(self.out/"e2-sctp-capture.stderr").open("w")
        self.e2_tcpdump=subprocess.Popen([
            "sudo","tcpdump","-i","any","-U","-s","0",
            "-w",str(self.out/"e2-sctp.pcap"),"sctp","port","36421"],
            stdout=subprocess.DEVNULL,stderr=self.e2_capture_file)
        time.sleep(.5)
        if self.e2_tcpdump.poll() is not None:
            raise RuntimeError("SCTP packet capture exited")
        self.ric_file=(self.out/"near-rt-ric.log").open("w")
        self.ric=subprocess.Popen(["stdbuf","-oL","-eL",self.opts.ric_binary,
                                   "-a",RIC_IP],
                                  stdout=self.ric_file,stderr=subprocess.STDOUT)
        time.sleep(2)
        if self.ric.poll() is not None:
            raise RuntimeError("nearRT-RIC crashed before CU startup")
        self.record("T31-matched-FlexRIC-nearRT-RIC-started","PASS",
                    gateway=RIC_IP,port=36421)

    def ran(self):
        super().ran() # CU and both DUs must complete F1 Setup, NGAP
        self.ric_file.flush()
        log=(self.out/"near-rt-ric.log").read_text(errors="replace")
        if "E2 SETUP-REQUEST rx" not in log or \
           "Accepting RAN function ID 2" not in log:
            raise RuntimeError("E2 Setup and KPM RAN function 2 not present in RIC log")
        deadline=time.monotonic()+35
        verified=False
        while time.monotonic()<deadline:
            try:
                capture=parse_e2_setup_sctp(self.out/"e2-sctp.pcap",CU_IP,RIC_IP)
                verified=(bool(capture["e2_setup_requests"]) and
                          bool(capture["e2_setup_responses"]))
                if verified:break
            except Exception:
                pass
            time.sleep(2)
        if not verified:
            raise RuntimeError("E2 Setup Response not proven on actual SCTP wire")
        (self.out/"E2-setup-wire.json").write_text(json.dumps(capture,indent=2)+"\n")
        self.record("T32-E2-Setup-and-KPM-function-2-accepted","PASS")

    def start_capture(self):
        self.pcap_starts+=1
        if self.pcap_starts==2:
            # Both UEs have received real NAS and PDU accepts at this point.
            self.kpm_file=(self.out/"live-ue-KPM-xapp.log").open("w")
            self.kpm_proc=subprocess.Popen(
                ["stdbuf","-oL","-eL",self.opts.xapp_kpm,"-a",RIC_IP],
                stdout=self.kpm_file,stderr=subprocess.STDOUT)
            time.sleep(2)
            if self.kpm_proc.poll() is not None:
                raise RuntimeError("KPM xApp exited before simultaneous UE traffic")
            self.record("T33-KPM-xApp-started-with-two-PDU-sessions","PASS")
        super().start_capture()

    def ue(self):
        super().ue() # Two actual UEs, distinct PDU IPs, concurrent GTP-U
        if self.kpm_proc is None:
            raise RuntimeError("KPM monitor never started during dual UE traffic")
        try:
            self.kpm_proc.wait(timeout=48)
        except subprocess.TimeoutExpired:
            self.kpm_proc.kill()
            self.kpm_proc.wait(timeout=8)
            raise RuntimeError("KPM monitor did not exit within bounded test")
        self.kpm_file.flush()
        log=(self.out/"live-ue-KPM-xapp.log").read_text(errors="replace")
        if self.kpm_proc.returncode!=0 or \
           "Successfully subscribed to RAN_FUNC_ID 2" not in log:
            raise RuntimeError("KPM subscription failed or monitor crashed: "+log[-1800:])
        self.record("T34-KPM-live-UE-subscription-accepted","PASS")
        wire=e2_indications(self.out/"e2-sctp.pcap")
        callbacks=re.findall(r"(?im)^(?=.*(?:KPM|RIC))(?=.*(?:indication|ind_msg|report)).*$",log)
        report={
            "measured_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "scope":"two RFsim UEs, dual DU, live KPM xApp, E2-enabled CU",
            "ric_indication_wire_count":len(wire["candidate_ricindications"]),
            "xapp_indication_callback_count":len(callbacks),
            "sctp_capture_frames":wire["sctp_frames"],
            "production_decision":"NO_GO",
        }
        (self.out/"live-KPM-indication-evidence.json").write_text(
            json.dumps(report,indent=2)+"\n")
        if len(wire["candidate_ricindications"])<3:
            raise RuntimeError("Fewer than 3 E2AP RICindication messages from E2 CU")
        if len(callbacks)<3:
            raise RuntimeError("KPM xApp did not log 3 recognizable indication callbacks")
        self.record("T35-real-KPM-indications-with-two-UEs","PASS",
                    e2_wire_indications=len(wire["candidate_ricindications"]),
                    kpm_callback_records=len(callbacks))
        self.record("STAGE4P","PASS",scope=(
            "Live E2 Setup and KPM indications with simultaneous H-CRAN dual UE traffic; "
            "RC control, sustained SLO, OSM rollback NOT verified"))

    def cleanup(self):
        for process in (self.kpm_proc,self.e2_tcpdump,self.ric):
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill()
        for handle in (self.kpm_file,self.e2_capture_file,self.ric_file):
            if handle is not None and not handle.closed:handle.close()
        super().cleanup()

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image",
                "ric-binary","xapp-kpm"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: real user-plane proof required")
    render_e2_cu(args.rendered)
    return KpmWithUEs(args).run()

if __name__=="__main__":
    raise SystemExit(main())
