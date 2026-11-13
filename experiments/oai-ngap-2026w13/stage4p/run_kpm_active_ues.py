#!/usr/bin/env python3
"""Stage 4P: fail-closed OAI H-CRAN KPM indications while both UEs transmit.

- CU with E2 enabled and matched pinned KPM/RC plugins.
- Real FlexRIC nearRT-RIC, real E2 Setup Request+Response on SCTP.
- Two synthetic UEs over independent H-CRAN macro/small DUs, each with
  real NAS, PDU, concurrent ICMP, F1-U and NG-U traffic (Stage 4O).
- A real KPM xApp subscribes while UEs remain registered and generate
  further PDU-tunnel traffic. At least 3 unique on-wire E2AP RIC Indication
  initiating PDUs (procedure 5) AND a real xApp indication callback required.
- No RC control is requested; RC behavior requires a separate acceptance.
- No production/staging connections, no real subscriber credentials.
"""
import argparse
import datetime
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "stage4o"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "stage4k"))
from run_two_ues import DualUEProbe
from run_ue_registration import IPS, SIM, sh, name
from e2ap_wire_evidence import parse_e2_setup_sctp

RIC_IP="172.29.76.1"
CU_IP=IPS["cu"]

def indicators_on_wire(path):
    """Count distinct SCTP TSNs carrying CU->RIC E2AP APER RICindication.

    Linux SLL2 classic little-endian PCAP from tcpdump -i any; procedure
    code 5 is the E2AP RIC Indication initiatingMessage (APER 00 05).
    Deliberately not a full ASN.1 validation; the xApp callback is a
    separate hard gate. Deduplicate retransmissions by SCTP TSN.
    """
    raw=pathlib.Path(path).read_bytes()
    if raw[:4]!=bytes.fromhex("d4c3b2a1") or len(raw)<24:
        raise RuntimeError("Invalid or missing little-endian classic SCTP PCAP")
    linktype=struct.unpack_from("<I",raw,20)[0]
    if linktype!=276:
        raise RuntimeError(f"Unexpected packet-capture link type {linktype}")
    offset=24
    seq=set()
    frames=[]
    ppids=set()
    while offset+16<=len(raw):
        _,_,incl,_=struct.unpack_from("<IIII",raw,offset)
        offset+=16
        if incl>262144 or offset+incl>len(raw):break
        packet=raw[offset:offset+incl]
        offset+=incl
        if len(packet)<44 or packet[:2]!=b"\x08\x00":continue
        ipv4=packet[20:]
        if (ipv4[0]>>4)!=4 or ipv4[9]!=132:continue
        src=str(ipaddress.IPv4Address(ipv4[12:16]))
        dst=str(ipaddress.IPv4Address(ipv4[16:20]))
        if src!=CU_IP or dst!=RIC_IP:continue
        ihl=(ipv4[0]&15)*4
        sctp=ipv4[ihl:]
        if len(sctp)<16:continue
        if struct.unpack_from("!H",sctp,2)[0]!=36421:continue
        pos=12
        while pos+4<=len(sctp):
            kind,flags,length=struct.unpack_from("!BBH",sctp,pos)
            if length<4 or pos+length>len(sctp):break
            if kind==0 and length>=16:
                data=sctp[pos+4:pos+length]
                tsn=struct.unpack_from("!I",data,0)[0]
                ppid=struct.unpack_from("!I",data,8)[0]
                payload=data[12:]
                ppids.add(ppid)
                if payload[:2]==b"\x00\x05" and tsn not in seq:
                    seq.add(tsn)
                    frames.append({
                       "tsn":tsn,
                       "payload_size":len(payload),
                       "sha256":hashlib.sha256(payload).hexdigest()
                    })
            pos+=(length+3)&~3
    return {"indications":len(frames),"packets":frames[:25],
            "sctp_ppids":sorted(ppids),"pcap_sha256":hashlib.sha256(raw).hexdigest()}

class KPMProbe(DualUEProbe):
    def __init__(self,options):
        super().__init__(options)
        self.ric=None
        self.ric_output=None
        self.e2_capture=None

    def bootstrap(self):
        super().bootstrap()
        if os.getenv("HCRAN_CI_E2_RIC")!=RIC_IP:
            raise RuntimeError("isolated E2 RIC env is not pinned to CI Docker bridge")
        self.ric_output=(self.out/"near-rt-ric.log").open("w",encoding="utf-8")
        self.ric=subprocess.Popen(
            ["stdbuf","-oL","-eL",self.opts.ric_binary,"-a",RIC_IP],
            stdout=self.ric_output,stderr=subprocess.STDOUT,
            start_new_session=True)
        time.sleep(3)
        if self.ric.poll() is not None:
            raise RuntimeError("FlexRIC failed before CU startup: "+
                               (self.out/"near-rt-ric.log").read_text()[-2000:])
        self.e2_capture=subprocess.Popen(
            ["sudo","tcpdump","-i","any","-s","0","-U",
             "-w",str(self.out/"e2ap-live.pcap"),"sctp"],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(1)
        if self.e2_capture.poll() is not None:
            raise RuntimeError("real E2 SCTP capture failed")
        self.record("T31-real-FlexRIC-started","PASS",ric_ip=RIC_IP)

    def riclog(self):
        if self.ric_output:
            self.ric_output.flush()
        path=self.out/"near-rt-ric.log"
        return path.read_text(errors="replace") if path.exists() else ""

    def ran(self):
        super().ran()
        deadline=time.monotonic()+55
        wire={}
        while time.monotonic()<deadline:
            self.demand("core","cu","macro","small")
            if self.ric.poll() is not None:
                raise RuntimeError("FlexRIC exited during E2 setup: "+self.riclog()[-1800:])
            pcap=self.out/"e2ap-live.pcap"
            if pcap.exists() and pcap.stat().st_size>24:
                wire=parse_e2_setup_sctp(pcap,CU_IP,RIC_IP)
                a=bool(wire["e2_setup_requests"])
                b=bool(wire["e2_setup_responses"])
                c="E2 SETUP-REQUEST rx" in self.riclog()
                assoc=sh("docker","exec",name("cu"),"cat",
                         "/proc/net/sctp/assocs",check=False,timeout=15)
                if a and b and c and RIC_IP in assoc:
                    self.out.joinpath("e2-setup-wire.json").write_text(
                        json.dumps(wire,indent=2)+"\n")
                    self.record("T32-real-E2AP-Setup-both-directions","PASS",
                                request=a,response=b,sctp=True)
                    return
            time.sleep(2)
        self.out.joinpath("e2-setup-wire.json").write_text(json.dumps(wire,indent=2)+"\n")
        raise RuntimeError("E2AP Setup Request/Response not confirmed on real wire")

    def ue(self):
        # All Stage 4O gates must pass first: two subscribers, concurrent
        # PDU, ICMP and GTP-U traffic over both DUs.
        super().ue()
        self.demand("core","cu","macro","small","ue","ue-small")
        if self.ric is None or self.ric.poll() is not None:
            raise RuntimeError("FlexRIC exited before active UE KPM xApp")
        self.record("T33-real-dual-UE-baseline-before-KPM","PASS")
        path=self.out/"active-UE-KPM-xapp.log"
        env=os.environ.copy()
        env["XAPP_DURATION"]="32"
        env["DB_DIR"]=str(self.out)
        with path.open("w",encoding="utf-8") as file:
            app=subprocess.Popen(
                ["stdbuf","-oL","-eL",self.opts.xapp_kpm,"-a",RIC_IP],
                stdout=file,stderr=subprocess.STDOUT,env=env)
            try:
                deadline=time.monotonic()+18
                while time.monotonic()<deadline:
                    file.flush()
                    msg=path.read_text(errors="replace")
                    if "Successfully subscribed to RAN_FUNC_ID 2" in msg:
                        self.record("T34-real-KPM-subscription-accepted","PASS")
                        break
                    if app.poll() is not None:
                        raise RuntimeError("KPM xApp exited before subscription: "+msg[-1800:])
                    time.sleep(1)
                else:
                    raise RuntimeError("KPM xApp did not receive subscription response")

                # Generate traffic AFTER successful KPM subscription so
                # correlation cannot be confused with UE startup messages.
                processes={}
                for ue in ("ue","ue-small"):
                    processes[ue]=subprocess.Popen(
                       ["docker","exec",name(ue),"ping","-I","oaitun_ue1",
                        "-c","12","-i","0.6","-W","2","10.45.0.1"],
                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                for ue,p in processes.items():
                    try:out,_=p.communicate(timeout=40)
                    except subprocess.TimeoutExpired:
                        p.kill();out,_=p.communicate(timeout=5)
                        raise RuntimeError(ue+" ping timeout "+out[-600:])
                    (self.out/(ue+"-KPM-traffic-ping.txt")).write_text(out)
                    if p.returncode!=0 or "12 received" not in out:
                        raise RuntimeError(ue+" ping failed under subscribed KPM: "+out[-900:])
                self.record("T35-two-UE-traffic-after-KPM-subscription","PASS",
                            two_ue_ping_replies=24)
                try:app.wait(timeout=50)
                except subprocess.TimeoutExpired:
                    app.terminate()
                    try:app.wait(timeout=6)
                    except subprocess.TimeoutExpired:app.kill()
                    raise RuntimeError("KPM xApp did not exit in bounded duration")
            finally:
                if app.poll() is None:
                    app.terminate()
                    try:app.wait(timeout=5)
                    except subprocess.TimeoutExpired:app.kill()

        output=path.read_text(errors="replace")
        if app.returncode!=0:
            raise RuntimeError("KPM xApp nonzero exit status: "+
                               str(app.returncode)+"; "+output[-1400:])
        if self.e2_capture.poll() is not None:
            raise RuntimeError("E2 packet capture exited before KPM review")
        self.e2_capture.terminate()
        self.e2_capture.wait(timeout=8)
        evidence=indicators_on_wire(self.out/"e2ap-live.pcap")
        # Strict xApp callback marker; a subscription acknowledgement,
        # E2 Setup or a library description is NEVER counted.
        callbacks=re.findall(
            r"(?im)^.*(?:RIC[ _-]?INDICATION(?: RX| RECEIVED)?|KPM[ _-]?INDICATION(?: RX| RECEIVED)?|INDICATION REPORT).*",
            output)
        evidence.update({
            "xapp_callback_lines":len(callbacks),
            "xapp_exit":app.returncode,
            "two_active_UE_PDU":True,
            "production_decision":"NO_GO",
            "note":"E2AP initiating procedure 5 on wire plus xApp callback, not RC control"
        })
        (self.out/"kpm-indication-evidence.json").write_text(
            json.dumps(evidence,indent=2)+"\n")
        if evidence["indications"]<3 or len(callbacks)<3:
            raise RuntimeError(
                f"KPM indications unverified: {evidence['indications']} "
                f"unique CU->RIC messages, {len(callbacks)} xApp indication callbacks")
        self.record("T36-real-KPM-indications-with-two-UEs","PASS",
                    e2ap_indications=evidence["indications"],
                    xapp_callbacks=len(callbacks))
        self.demand("core","cu","macro","small","ue","ue-small")
        self.capture("after-active-KPM")
        self.record("STAGE4P","PASS",scope=(
            "two distinct UEs active on both DUs; KPM RIC Indication delivery; "
            "NO RC effect or production rollout validation"))

    def cleanup(self):
        try:
            if self.e2_capture is not None and self.e2_capture.poll() is None:
                self.e2_capture.terminate()
                self.e2_capture.wait(timeout=8)
        except Exception:
            pass
        try:
            if self.ric is not None and self.ric.poll() is None:
                self.ric.terminate()
                self.ric.wait(timeout=8)
        except Exception:
            pass
        if self.ric_output is not None:
            self.ric_output.close()
        super().cleanup()

def main():
    parser=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image",
                "ric-binary","xapp-kpm"):
        parser.add_argument("--"+key,required=True)
    args=parser.parse_args()
    return KPMProbe(args).run()

if __name__=="__main__":
    sys.exit(main())
