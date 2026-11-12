#!/usr/bin/env python3
"""Bounded Stage4T H-CRAN dual-UE sustained service validation, no deployment.

Start from the already proven real Stage4O two-UE test on isolated GitHub
infrastructure. During 6 sampled rounds, both macro and small UE send 10
concurrent ICMP requests through actual PDU TUN and keep BOTH F1-C peers
and NGAP associated. Capture 2-DU F1-U and 2-TEID NG-U GTP-U during the
soak period. Observe container restart counts, per-sample CPU/memory.

PASS is an ~90s bounded CI reliability smoke ONLY, not 3GPP conformance,
long-duration capacity, production SLO, autonomous recovery or OSM rollback.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time
import hashlib

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4o"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4k"))
from run_two_ues import DualUEProbe, SMALL_IP
from run_ue_registration import IPS, name, sh
import gtpu_wire

PROCESSES=("core","cu","macro","small","ue","ue-small")
ROUNDS=6
PROBES_PER_ROUND=10
SAMPLE_GAP_SEC=12

class Soak(DualUEProbe):
    def ue(self):
        super().ue()
        self.soak()

    def check_associations(self):
        table=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                 timeout=20)
        for peer in (IPS["core"],IPS["macro"],SMALL_IP):
            if peer not in table:
                raise RuntimeError("CU lost required AMF/F1-C SCTP association "+peer)

    def inspect_restarts(self):
        output={}
        for component in PROCESSES:
            raw=sh("docker","inspect","--format",
                   "{{.RestartCount}} {{.State.Running}} {{.State.OOMKilled}}",
                   name(component),timeout=20).strip().split()
            if len(raw)!=3:raise RuntimeError("Cannot inspect "+component)
            restart=int(raw[0])
            running=raw[1]=="true"
            oom=raw[2]=="true"
            output[component]={"restarts":restart,"running":running,"oom_killed":oom}
            if restart!=0 or not running or oom:
                raise RuntimeError(f"{component} restarted, stopped or OOM-killed during soak")
        return output

    def ping_round(self,round_id):
        attempts={}
        procs={}
        for label in ("ue","ue-small"):
            procs[label]=subprocess.Popen(
                ["docker","exec",name(label),"ping",
                 "-I","oaitun_ue1","-c",str(PROBES_PER_ROUND),
                 "-i","0.3","-W","2","10.45.0.1"],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        for label,p in procs.items():
            try:result,_=p.communicate(timeout=32)
            except subprocess.TimeoutExpired:
                p.kill()
                result,_=p.communicate(timeout=5)
                raise RuntimeError(f"{label} ping hung in round {round_id}")
            (self.out/f"soak-round-{round_id:02}-{label}.log").write_text(result)
            m=re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",result)
            sent=int(m.group(1)) if m else 0
            received=int(m.group(2)) if m else 0
            if p.returncode or sent!=PROBES_PER_ROUND or received!=PROBES_PER_ROUND:
                raise RuntimeError(f"{label} lost PDU traffic in round {round_id}: "
                                   f"{received}/{sent}; rc={p.returncode}")
            attempts[label]={"sent":sent,"received":received}
        return attempts

    def sample_resources(self,round_id):
        stats=sh("docker","stats","--no-stream",
                 "--format","{{json .}}",
                 *(name(p) for p in PROCESSES),
                 check=False,timeout=40)
        out=self.out/f"soak-round-{round_id:02}-docker-stats.jsonl"
        out.write_text(stats)
        if not stats.strip():
            raise RuntimeError("Cannot collect resource stats from Docker")
        return stats.count("\n")

    def verify_captured_packets(self,trace):
        gtpu_wire.DU=IPS["macro"]
        macro=gtpu_wire.analyze(trace)
        gtpu_wire.DU=SMALL_IP
        small=gtpu_wire.analyze(trace)
        for label,record in (("macro",macro),("small",small)):
            for key in ("f1u_ul","f1u_dl"):
                if record["G_PDU_counts"][key] < ROUNDS*PROBES_PER_ROUND//2:
                    raise RuntimeError(label+" "+key+" low G-PDU count in sustained traffic")
        for key in ("ngu_ul","ngu_dl"):
            if len(macro["TEIDs"][key])<2:
                raise RuntimeError("Missing separate active UE NG-U TEIDs "+key)
            if macro["G_PDU_counts"][key] < ROUNDS*PROBES_PER_ROUND:
                raise RuntimeError("NG-U "+key+" did not carry expected two-UE data")
        return {"macro":macro,"small":small}

    def soak(self):
        self.demand(*PROCESSES)
        self.check_associations()
        self.inspect_restarts()
        for old,new in (("gtpu.pcap","stage4o-baseline-gtpu.pcap"),
                        ("dual-ue-traffic-evidence.json","stage4o-baseline-traffic.json")):
            f=self.out/old
            if f.exists():f.rename(self.out/new)

        results=[]
        self.start_capture()
        start=time.monotonic()
        try:
            for i in range(1,ROUNDS+1):
                self.demand(*PROCESSES)
                self.check_associations()
                restarts=self.inspect_restarts()
                pings=self.ping_round(i)
                nstats=self.sample_resources(i)
                self.demand(*PROCESSES)
                self.check_associations()
                row={"sample":i,
                     "at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     "elapsed_sec":round(time.monotonic()-start,2),
                     "pings":pings,
                     "all_restarts_zero":all(v["restarts"]==0 for v in restarts.values()),
                     "docker_stat_rows":nstats}
                results.append(row)
                self.record(f"T50-dual-UE-soak-round-{i:02}","PASS",
                            each_ue_sent=PROBES_PER_ROUND,
                            each_ue_received=PROBES_PER_ROUND,
                            f1c_and_ngap_peers=3)
                if i<ROUNDS:
                    time.sleep(SAMPLE_GAP_SEC)
        finally:
            self.stop_capture()

        trace=self.out/"gtpu.pcap"
        if not trace.is_file() or trace.stat().st_size<100:
            raise RuntimeError("No actual GTP-U traffic capture during sustained dual-UE test")
        packet_data=self.verify_captured_packets(trace)
        data={
            "stage":"4T",
            "mode":"isolated dual H-CRAN OAI DU and 2 real RFsim UEs, no production",
            "utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "round_count":ROUNDS,"probes_per_UE_per_round":PROBES_PER_ROUND,
            "observed_duration_sec":round(time.monotonic()-start,2),
            "per_ue_pings_sent":ROUNDS*PROBES_PER_ROUND,
            "per_ue_pings_received":ROUNDS*PROBES_PER_ROUND,
            "samples":results,
            "packet_evidence":packet_data,
            "pcap_sha256":hashlib.sha256(trace.read_bytes()).hexdigest(),
            "production_decision":"NO_GO",
            "still_missing":["automated UE reattach after AMF loss",
                             "real RC control effect","independent observer",
                             "production load SLO","actual OSM staging rollback"]
        }
        (self.out/"dual-UE-soak-evidence.json").write_text(json.dumps(data,indent=2)+"\n")
        self.capture("soak-final")
        self.record("T51-sustained-dual-UE-PDU-and-GTPU","PASS",
                    rounds=ROUNDS,pings_per_UE=ROUNDS*PROBES_PER_ROUND,
                    duration_sec=data["observed_duration_sec"],
                    macro_gtpu=packet_data["macro"]["G_PDU_counts"],
                    small_gtpu=packet_data["small"]["G_PDU_counts"])
        self.record("STAGE4T","PASS",
                    scope="bounded dual-UE traffic/restart/sample smoke; no production SLO or rollback")

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu required for sustained user-plane checks")
    return Soak(args).run()

if __name__=="__main__":
    sys.exit(main())
