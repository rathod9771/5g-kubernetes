#!/usr/bin/env python3
"""Stage 4Q: assisted UE re-registration and real data after full 5GC outage.

Uses Stage 4O proven dual UE/F1-U/NG-U baseline, restarts the disposable
full Open5GS core and waits for patched CU NG Setup recovery. Stage 4P
proved automatic data recovery did not occur because NGAP deregistration
caused RRC Release/UE context + DRB teardown. This run explicitly RESTARTS
both disposable RFsim UE containers (assisted recovery), requires NEW NAS
Registration Accept and NEW PDU Session Establishment Accept for each,
then simultaneous ICMP and actual F1-U/NG-U traffic with two distinct TEIDs.

A passing run is NOT autonomous recovery or production readiness.
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
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4m"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4k"))
import gtpu_wire
from run_two_ues import DualUEProbe, SMALL_IP, good_ping, ipv4_pdu
from run_ue_registration import IPS, REG, PDU, name, sh

NGAP=re.compile(r"Received NGSetupResponse from AMF",re.I)

class AssistedRecovery(DualUEProbe):
    def new_message(self,component,expression,baseline,timeout_s,label):
        end=time.monotonic()+timeout_s
        while time.monotonic()<end:
            self.demand("core","cu","macro","small","ue","ue-small")
            hits=len(expression.findall(self.logs(component)))
            if hits>baseline:
                self.record(label,"PASS",new_messages=hits-baseline)
                return
            time.sleep(3)
        raise RuntimeError(f"Timeout: no *new* {label} after UE restart")

    def ue(self):
        super().ue()
        self.demand("core","cu","macro","small","ue","ue-small")
        before=len(NGAP.findall(self.logs("cu")))
        if before<1:
            raise RuntimeError("initial NG Setup missing before full-core fault")
        counts={
            comp:{"reg":len(REG.findall(self.logs(comp))),
                  "pdu":len(PDU.findall(self.logs(comp)))}
            for comp in ("ue","ue-small")
        }
        start=time.monotonic()
        sh("docker","restart","--time","3",name("core"),timeout=55)
        self.record("T31-isolated-entire-5GC-restart","PASS",
                    fault="full disposable Open5GS core only")
        deadline=time.monotonic()+135
        while time.monotonic()<deadline:
            self.demand("core","cu","macro","small","ue","ue-small")
            if len(NGAP.findall(self.logs("cu")))>before:
                break
            time.sleep(3)
        else:
            raise RuntimeError("No patched CU NG Setup recovery after full core restart")
        ngap_recovery=round(time.monotonic()-start,2)
        self.record("T32-NGAP-reconnect-after-full-5GC-outage","PASS",
                    seconds_from_fault=ngap_recovery)
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                 timeout=20)
        for peer in (IPS["core"],IPS["macro"],SMALL_IP):
            if peer not in assoc:
                raise RuntimeError("SCTP peer missing after full 5GC restart "+peer)
        self.record("T33-both-F1C-SCTP-peers-preserved","PASS")

        # Observe documented Stage 4P UE context teardown; do NOT claim
        # this is automatic UE recovery. OAI UE containers are restarted
        # by the test, a visible and explicit operator-controlled action.
        assisted_start=time.monotonic()
        for component in ("ue","ue-small"):
            sh("docker","restart","--time","3",name(component),timeout=50)
        self.record("T34-two-RFsim-UE-containers-restarted","PASS",
                    recovery_type="ASSISTED, not autonomous")
        for component in ("ue","ue-small"):
            self.new_message(component,REG,counts[component]["reg"],115,
                             "T35-new-"+component+"-NAS-RegistrationAccept")
            self.new_message(component,PDU,counts[component]["pdu"],105,
                             "T36-new-"+component+"-PDU-SessionEstablishmentAccept")
        assisted_seconds=round(time.monotonic()-assisted_start,2)

        addr={}
        for component in ("ue","ue-small"):
            state=sh("docker","exec",name(component),"ip","-o","-4",
                     "addr","show",timeout=20)
            addr[component]=ipv4_pdu(state)
        if addr["ue"]==addr["ue-small"]:
            raise RuntimeError("Post-assist UE IPs collided")
        self.record("T37-two-separate-PDU-IPv4-after-UE-restart","PASS",
                    macro_ue=addr["ue"],small_ue=addr["ue-small"])

        pre=self.out/"gtpu.pcap"
        if pre.exists():
            pre.rename(self.out/"pre-full-core-restart-gtpu.pcap")
        self.start_capture()
        results={}
        spawned=[]
        try:
            for component in ("ue","ue-small"):
                p=subprocess.Popen(["docker","exec",name(component),"ping",
                       "-I","oaitun_ue1","-c","6","-i","0.5","-W","2",
                       "10.45.0.1"],
                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                spawned.append((component,p))
            for component,p in spawned:
                try:out,_=p.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    p.kill()
                    out,_=p.communicate(timeout=7)
                    raise RuntimeError(component+" post-assist ping timed out")
                results[component]={"exit_code":p.returncode,
                                    "loss_free":good_ping(out)}
                (self.out/(component+"-assisted-recovery-ping.txt")).write_text(out)
            time.sleep(2)
        finally:
            self.stop_capture()
        if not all(r["exit_code"]==0 and r["loss_free"] for r in results.values()):
            self.capture("assisted-recovery-ping-failed")
            raise RuntimeError("Assisted PDU packet service did not recover: "+str(results))
        self.record("T38-two-UE-ICMP-post-assist-6of6-each","PASS",
                    recovered_via="explicit RFsim UE process restart")
        pcap=self.out/"gtpu.pcap"
        if not pcap.exists() or pcap.stat().st_size<120:
            raise RuntimeError("No post-assist user-plane capture")
        gtpu_wire.DU=IPS["macro"]
        macro=gtpu_wire.analyze(pcap)
        gtpu_wire.DU=SMALL_IP
        small=gtpu_wire.analyze(pcap)
        for label,result in (("macro",macro),("small",small)):
            for direction in ("f1u_ul","f1u_dl"):
                if result["G_PDU_counts"][direction]<3:
                    raise RuntimeError("Missing post-assist F1-U "+label+" "+direction)
        for direction in ("ngu_ul","ngu_dl"):
            if macro["G_PDU_counts"][direction]<6 or len(macro["TEIDs"][direction])<2:
                raise RuntimeError("Post-assist NG-U misses distinct TEIDs or data in "+direction)
        self.demand("core","cu","macro","small","ue","ue-small")
        self.record("T39-two-DU-F1U-and-two-TEID-NGU-after-restart","PASS",
                    macro=macro["G_PDU_counts"],small=small["G_PDU_counts"])
        self.capture("assisted-recovery-complete")
        result={
            "experiment":"Stage 4Q assisted H-CRAN UE recovery",
            "scope":"Two DUs and two synthetic UEs, full disposable 5GC restart",
            "NGAP_reconnect_seconds":ngap_recovery,
            "assisted_UE_re_register_PDU_seconds":assisted_seconds,
            "automated_UE_recovery":"FAIL in Stage 4P; not re-tested as pass",
            "operator_action":"explicit restart of two simulated UE containers",
            "post_assist_UE_IPs":addr,
            "post_assist_ICMP":results,
            "post_assist_F1_U":{"macro":macro["G_PDU_counts"],
                               "small":small["G_PDU_counts"]},
            "post_assist_NG_U_TEIDs":macro["TEIDs"],
            "production_decision":"NO_GO",
            "limitations":["not autonomous UE recovery",
                           "not OSM/K8s staging rollback",
                           "not FlexRIC KPM indication or RC control",
                           "not fault-free sustained load test"],
        }
        (self.out/"assisted-recovery-evidence.json").write_text(json.dumps(result,indent=2)+"\n")
        self.record("STAGE4Q","PASS",
                    scope="assisted dual-UE service recovery after complete disposable 5GC restart")

def main():
    parser=argparse.ArgumentParser()
    for arg in ("rendered","output","cu-image","du-image","ue-image"):
        parser.add_argument("--"+arg,required=True)
    parser.add_argument("--verify-gtpu",action="store_true")
    args=parser.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu required")
    return AssistedRecovery(args).run()

if __name__=="__main__":
    sys.exit(main())
