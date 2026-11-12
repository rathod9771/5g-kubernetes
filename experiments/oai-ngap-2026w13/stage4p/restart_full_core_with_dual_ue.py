#!/usr/bin/env python3
"""Stage 4P: real Open5GS *entire core* restart with both H-CRAN UEs active.

This deliberately restarts all temporary core NFs (NOT AMF alone) because
Stage 4K uses a single core container. Checks recovered NG Setup, two F1-C
peers and post-restart UE data traffic without restarting either UE or DU.
Traffic continuity and resilience pass only on actual bidirectional GTP-U
G-PDUs per DU and 0% loss for both UE-to-UPF tunnels. Failure is evidence
of unmet service recovery, not grounds for a risky live-cluster experiment.
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
from run_two_ues import DualUEProbe, SMALL_IP, good_ping, IPS, name, sh

RSP=re.compile(r"Received NGSetupResponse from AMF",re.I)

class DualUeCoreRestart(DualUEProbe):
    def ue(self):
        super().ue()  # Required pre-fault real two-UE GTP-U baseline.
        self.demand("core","cu","macro","small","ue","ue-small")

        initial= len(RSP.findall(self.logs("cu")))
        if initial < 1:
            raise RuntimeError("No initial NG Setup Response; refusing fault injection")
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",timeout=20)
        for peer in (IPS["core"],IPS["macro"],SMALL_IP):
            if peer not in assoc:
                raise RuntimeError("Pre-fault NGAP/F1-C peer missing "+peer)
        self.record("T31-pre-fault-2-UE-2-DU-and-NGAP","PASS",
                    ngsetup_responses=initial)

        # Simulate isolated *full core* outage; do not label AMF-only.
        began=time.monotonic()
        sh("docker","restart","--time","3",name("core"),timeout=50)
        self.record("T32-disposable-5GC-restart","PASS",
                    outage_kind="ALL NFs in isolated Open5GS core container")
        deadline=time.monotonic()+150
        recovered=False
        while time.monotonic()<deadline:
            self.demand("core","cu","macro","small","ue","ue-small")
            current=len(RSP.findall(self.logs("cu")))
            if current>initial:
                recovered=True
                break
            time.sleep(3)
        if not recovered:
            self.capture("post-core-fault-no-ngap-reconnect")
            raise RuntimeError("No NEW NG Setup Response after entire 5GC restart")
        recovery_seconds=round(time.monotonic()-began,2)
        self.record("T33-post-full-5GC-restart-NGSetupResponse","PASS",
                    response_count=current,recovery_seconds=recovery_seconds)

        # Verify both DU SCTP associations survive the control plane outage.
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",timeout=20)
        for peer in (IPS["core"],IPS["macro"],SMALL_IP):
            if peer not in assoc:
                raise RuntimeError("Post-reconnect CU SCTP peer missing "+peer)
        self.record("T34-both-F1C-SCTP-peers-survive-core-restart","PASS")

        # Preserve old packet evidence; capture only traffic generated AFTER
        # real NGAP recovery. Prevent pre-fault packets from counting.
        old=self.out/"gtpu.pcap"
        if old.exists():
            old.rename(self.out/"pre-core-restart-gtpu.pcap")
        self.start_capture()
        probes={}
        launched=[]
        try:
            for ue in ("ue","ue-small"):
                p=subprocess.Popen(["docker","exec",name(ue),"ping",
                    "-I","oaitun_ue1","-c","6","-i","0.5","-W","2","10.45.0.1"],
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                launched.append((ue,p))
            for ue,p in launched:
                try:out,_=p.communicate(timeout=40)
                except subprocess.TimeoutExpired:
                    p.kill()
                    out,_=p.communicate(timeout=8)
                    raise RuntimeError("Timed out "+ue+" after 5GC restart: "+out[-650:])
                probes[ue]={"exit_code":p.returncode,
                            "loss_free":good_ping(out)}
                (self.out/(ue+"-post-core-restart-ICMP.txt")).write_text(out)
            time.sleep(2)
        finally:
            self.stop_capture()

        # Record accurate failure evidence before asserting success.
        if not all(x["exit_code"]==0 and x["loss_free"] for x in probes.values()):
            self.capture("post-core-fault-userplane-failed")
            raise RuntimeError("UE PDU service not automatically restored after "
                               "full 5GC restart without UE intervention: "+str(probes))
        self.record("T35-dual-UE-no-reattach-ICMP-after-core-restart","PASS",
                    each_sent=6,each_received=6)

        captured=self.out/"gtpu.pcap"
        if not captured.is_file() or captured.stat().st_size<120:
            raise RuntimeError("No post-fault PCAP from dual UE data")
        gtpu_wire.DU=IPS["macro"]
        macro=gtpu_wire.analyze(captured)
        gtpu_wire.DU=SMALL_IP
        small=gtpu_wire.analyze(captured)
        for label,result in (("macro",macro),("small",small)):
            for direction in ("f1u_ul","f1u_dl"):
                if result["G_PDU_counts"][direction]<3:
                    raise RuntimeError("Missing post-restart "+label+" "+direction+" G-PDU")
        for direction in ("ngu_ul","ngu_dl"):
            if macro["G_PDU_counts"][direction]<6:
                raise RuntimeError("Missing post-restart "+direction+" NG-U G-PDU")
            if len(macro["TEIDs"][direction])<2:
                raise RuntimeError("Missing two separate post-restart NG-U "
                                   "TEIDs for "+direction)
        self.record("T36-bidirectional-post-fault-F1U-NGU-for-both-DUs","PASS",
                    macro_ul=macro["G_PDU_counts"]["f1u_ul"],
                    macro_dl=macro["G_PDU_counts"]["f1u_dl"],
                    small_ul=small["G_PDU_counts"]["f1u_ul"],
                    small_dl=small["G_PDU_counts"]["f1u_dl"],
                    ngu_ul=macro["G_PDU_counts"]["ngu_ul"],
                    ngu_dl=macro["G_PDU_counts"]["ngu_dl"])
        self.capture("post-core-restart-success")
        self.out.joinpath("full-core-restart-evidence.json").write_text(
            json.dumps({
                "scope":"entire disposable 5GC restart with two UEs, no forced UE restart",
                "new_NGSetupResponse_count":current-initial,
                "NGAP_recovery_seconds":recovery_seconds,
                "independent_UEs":2, "connected_DUs":2,
                "ICMP":{"macro":probes["ue"],"small":probes["ue-small"]},
                "macro":macro,"small":small,
                "release_decision":"NO_GO",
                "non_go_reason":"not actual staging/OSM rollout, KPM/RC, sustained SLOs"
            },indent=2)+"\n")
        self.record("STAGE4P","PASS",
                    scope="2 active RFsim UEs, 2 DUs, full disposable core restart and restored PDU traffic")

def main():
    p=argparse.ArgumentParser()
    for arg in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+arg,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu mandatory")
    return DualUeCoreRestart(args).run()

if __name__=="__main__":
    sys.exit(main())
