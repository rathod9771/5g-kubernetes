#!/usr/bin/env python3
"""Isolated real AMF failure with two H-CRAN UEs and both DU user planes.

Stage4O must pass first. Kill only the test Open5GS AMF daemon; keep NRF,
SMF, UPF, UDM and the other NFs running. Require patched CU re-dial, a NEW
NG Setup Response, both F1-C peers and bidirectional GTP-U with both UEs.
No production cluster, real subscriber or OSM interaction.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4o"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4k"))
import gtpu_wire
from run_two_ues import DualUEProbe, SMALL_IP
from run_ue_registration import IPS, name, sh

NGAP=re.compile(r"Received NGSetupResponse from AMF",re.I)
REDIAL=re.compile(r"Re-dial AMF|SCTP_STATE_UNREACHABLE|SCTP.*unreachable",re.I)
ALIVE=("core","cu","macro","small","ue","ue-small")

class Recovery(DualUEProbe):
    def ue(self):
        super().ue()  # two distinct subscribers, PDU sessions and GTP-U PASS
        self.fault_and_recover()

    def amf_count(self):
        command='n=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then n=$((n+1)); fi; done; echo "$n"'
        return int(sh("docker","exec",name("core"),"/bin/sh","-ec",
                      command,timeout=20).strip())

    def sctp_check(self,stage):
        listing=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                   timeout=20)
        for addr in (IPS["core"],IPS["macro"],SMALL_IP):
            if addr not in listing:
                raise RuntimeError(stage+" missing SCTP peer "+addr)
        self.record(stage,"PASS",peers=["AMF","DU-100","DU-101"])

    def stop_amf(self):
        command='found=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then pid=$(echo "$f" | cut -d/ -f3); kill -TERM "$pid"; found=1; fi; done; test "$found" = 1'
        sh("docker","exec",name("core"),"/bin/sh","-ec",command,timeout=25)
        for _ in range(18):
            self.demand(*ALIVE)
            if self.amf_count()==0:
                self.record("T31-AMF-actually-stopped","PASS")
                return
            time.sleep(1)
        raise RuntimeError("AMF did not terminate or another core process exited")

    def await_redial(self,baseline):
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            count=len(REDIAL.findall(self.logs("cu")))
            if count>baseline:
                self.record("T32-patched-CU-AMF-outage-redial","PASS",
                            new_attempts=count-baseline)
                return
            time.sleep(3)
        self.capture("AMF-redial-timeout")
        raise RuntimeError("No new patched CU AMF redial within 100 seconds")

    def start_amf(self):
        command="exec open5gs-amfd -c /tmp/stage4k-conf/amf.yaml >> /tmp/stage4k-log/amf-restarted.log 2>&1"
        sh("docker","exec","-d",name("core"),"/bin/sh","-c",command,timeout=30)
        for _ in range(22):
            self.demand(*ALIVE)
            if self.amf_count()==1:
                self.record("T33-AMF-process-restarted","PASS")
                return
            time.sleep(1)
        raise RuntimeError("AMF did not restart in isolated 5GC")

    def await_ngap(self,old_responses):
        deadline=time.monotonic()+140
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            count=len(NGAP.findall(self.logs("cu")))
            if count>old_responses:
                self.record("T34-new-NGSetupResponse-after-AMF-restart",
                            "PASS",before=old_responses,after=count)
                return
            time.sleep(3)
        self.capture("NGAP-recovery-timeout")
        raise RuntimeError("No NEW NG Setup Response within 140 seconds")

    def post_ping(self):
        streams={}
        procs={}
        for label in ("ue","ue-small"):
            procs[label]=subprocess.Popen(
                ["docker","exec",name(label),"ping","-I","oaitun_ue1",
                 "-c","6","-i","0.5","-W","2","10.45.0.1"],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        for label,proc in procs.items():
            try:out,_=proc.communicate(timeout=35)
            except subprocess.TimeoutExpired:
                proc.kill();out,_=proc.communicate(timeout=5)
                raise RuntimeError(label+" PDU ping hung after AMF restart")
            (self.out/(label+"-after-AMF-ping.txt")).write_text(out)
            m=re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",out)
            sent=int(m.group(1)) if m else 0
            received=int(m.group(2)) if m else 0
            streams[label]={"sent":sent,"received":received}
            if sent!=6 or received<5 or proc.returncode!=0:
                raise RuntimeError(label+" PDU connectivity NOT restored: "+out[-1500:])
        self.record("T36-both-UE-PDU-ICMP-restored","PASS",streams=streams)

    def check_post_gtpu(self):
        path=self.out/"gtpu.pcap"
        if not path.exists() or path.stat().st_size<120:
            raise RuntimeError("No post-AMF GTP-U capture")
        gtpu_wire.DU=IPS["macro"]
        macro=gtpu_wire.analyze(path)
        gtpu_wire.DU=SMALL_IP
        small=gtpu_wire.analyze(path)
        for label,result in (("macro",macro),("small",small)):
            for direction in ("f1u_ul","f1u_dl"):
                if result["G_PDU_counts"][direction]<3:
                    raise RuntimeError(label+" "+direction+" lacks restored GTP-U")
        for direction in ("ngu_ul","ngu_dl"):
            if macro["G_PDU_counts"][direction]<6 or len(macro["TEIDs"][direction])<2:
                raise RuntimeError(direction+" missing dual-UE NG-U T-PDUs/TEIDs")
        self.out.joinpath("post-AMF-gtpu-evidence.json").write_text(
            json.dumps({"macro":macro,"small":small,"production_decision":"NO_GO"},indent=2)+"\n")
        self.record("T37-both-F1U-and-NGU-data-paths-restored","PASS",
                    macro=macro["G_PDU_counts"],small=small["G_PDU_counts"])

    def fault_and_recover(self):
        self.demand(*ALIVE)
        self.sctp_check("T30-three-SCTP-peers-before-AMF-fault")
        before=len(NGAP.findall(self.logs("cu")))
        redials=len(REDIAL.findall(self.logs("cu")))
        if before<1 or self.amf_count()!=1:
            raise RuntimeError("AMF process or initial NG Setup not established")
        # Keep pre-fault evidence physically separate: cannot count it as
        # restored data-plane traffic after an outage.
        for old,new in (("gtpu.pcap","before-fault-dual-ue.pcap"),
                        ("dual-ue-traffic-evidence.json","before-fault-dual-ue.json")):
            f=self.out/old
            if f.exists():f.rename(self.out/new)
        self.stop_amf()
        self.await_redial(redials)
        self.start_amf()
        self.await_ngap(before)
        self.sctp_check("T35-both-F1C-DUs-survived-AMF-restart")
        self.start_capture()
        try:
            self.post_ping()
            time.sleep(2)
        finally:
            self.stop_capture()
        self.check_post_gtpu()
        self.demand(*ALIVE)
        self.sctp_check("T38-stable-F1C-and-NGAP-with-both-UEs-active")
        self.capture("after-AMF-restart")
        self.record("STAGE4P","PASS",scope=(
            "one isolated AMF failure, NGAP re-dial and recovery; "
            "both F1-C DUs plus dual-UE data-plane service restored; "
            "not a production or multi-cycle SLO acceptance"))

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu is mandatory")
    return Recovery(args).run()

if __name__=="__main__":
    sys.exit(main())
