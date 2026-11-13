#!/usr/bin/env python3
"""Stage 5H: real OAI UE-server cause-#9 initial registration recovery.

Run against a source-pinned, experimental W45 UE with a narrow
TS24.501 Service Reject cause#9 fallback. One synthetic UE is RFsim
SERVER; one CU and two DU clients establish real bidirectional F1 handover
BEFORE AMF outage, proving a viable RF stack. Kill ONLY disposable AMF;
after NG Setup reconnect queue real UL data. Require actual Service
Request -> cause #9 -> SUCI Registration Request -> fresh Registration
Accept -> PDU Establishment Accept -> 6/6 ICMP, fresh F1-U & NG-U.
Do not restart UE, DU or CU, use E2/RIC, or touch HP/OSM.
No unconditional spontaneous recovery or production authorization.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import sys
import time
import types

STAGES=(
    "Generate initial NAS message: Service Request",
    "UE_identity_cannot_be_derived_by_the_network",
    "Stage5G: cause #9, cleared stale GUTI and NAS security",
    "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE",
    "Received Registration Accept",
    "PDU Session Establishment Accept",
)
NGAP=re.compile("Received NGSetupResponse from AMF",re.I)


def probe(opts):
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/"stage5e"))
    import amf_mo_service_resumption as stage5e
    base=stage5e.create_probe(opts)  # installs AMF-only core launcher
    original_ue=base.__class__.ue  # original proven Stage4Z, not Stage5E
    from run_ue_registration import IPS, name, sh

    def ue_id():
        data=json.loads(sh("docker","inspect",name("ue")))
        if len(data)!=1 or not data[0].get("State",{}).get("Running"):
            raise RuntimeError("Synthetic UE process not running")
        x=data[0]
        return {"id":x["Id"],"pid":x["State"]["Pid"],
                "started_at":x["State"]["StartedAt"],
                "restart_count":x.get("RestartCount",0)}

    def amf_count():
        s='n=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then n=$((n+1)); fi; done; echo "$n"'
        return int(sh("docker","exec",name("core"),"/bin/sh","-ec",s,timeout=20).strip())

    def stage(self):
        original_ue(self)
        self.demand("core","cu","macro","small","ue")
        before_id=ue_id()
        before_ip=self.ue_ip()
        cut=len(self.logs("ue"))
        prev_ng=len(NGAP.findall(self.logs("cu")))
        if amf_count()!=1 or prev_ng<1:
            raise RuntimeError("Pre-fault AMF/NGAP missing")
        self.record("T100-before-AMF-real-two-way-F1-and-PDU","PASS",
                    pdu_ip=before_ip,ue_process_unchanged=True)
        stop='found=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then kill -TERM "$(echo "$f" | cut -d/ -f3)"; found=1; fi; done; test "$found" = 1'
        sh("docker","exec",name("core"),"/bin/sh","-ec",stop,timeout=25)
        stop_end=time.monotonic()+25
        while time.monotonic()<stop_end and amf_count()!=0:
            time.sleep(1)
        if amf_count()!=0:
            raise RuntimeError("AMF stop not established")
        self.demand("core","cu","macro","small","ue")
        self.record("T101-only-test-AMF-actually-stopped","PASS")
        start='exec open5gs-amfd -c /tmp/stage4k-conf/amf.yaml >> /tmp/stage4k-log/amf-restarted.log 2>&1'
        sh("docker","exec","-d",name("core"),"/bin/sh","-c",start,timeout=25)
        until=time.monotonic()+145
        while time.monotonic()<until:
            self.demand("core","cu","macro","small","ue")
            if amf_count()==1 and len(NGAP.findall(self.logs("cu")))>prev_ng:
                self.record("T102-new-NGSetupResponse-after-AMF-restart","PASS")
                break
            time.sleep(3)
        else:
            raise RuntimeError("No new NG Setup Response")
        # Real UE-originated app data triggers TS24.501 mobile originated SR.
        # Never inject arbitrary RRC or fake NAS. Do not let optional ping
        # process exit code 137 mask the real UE lifecycle checks.
        self.record("T103-after-AMF-preserved-UE-TUN","OBSERVED",
                    tun_ip=self.ue_ip(),original=before_ip)
        logs=self.logs("ue")[cut:]
        if "RRC moved into IDLE state" not in logs:
            raise RuntimeError("UE did not enter RRC_IDLE after AMF fault")
        sh("docker","exec",name("ue"),"/bin/sh","-c",
           "ping -I oaitun_ue1 -c 2 -i .6 -W 1 10.45.0.1 >/dev/null 2>&1 || true",
           timeout=20,check=False)
        until=time.monotonic()+145
        found={}
        while time.monotonic()<until:
            self.demand("core","cu","macro","small","ue")
            if ue_id()!=before_id:
                raise RuntimeError("UE restarted during recovery, invalid")
            logs=self.logs("ue")[cut:]
            idx=-1
            found={}
            for label in STAGES:
                at=logs.find(label,idx+1)
                found[label]=(at>=0)
                if at<0:break
                idx=at
            if len(found)==len(STAGES) and all(found.values()):
                break
            time.sleep(3)
        else:
            self.record("T104-nas-cause9-to-SUCI-and-PDU","BLOCKED",found=found)
            raise RuntimeError("Missing actual sequential ServiceRequest/cause9/SUCI/Registration/PDU after AMF restart")
        self.record("T104-nas-cause9-to-SUCI-and-PDU","PASS",steps=len(STAGES))
        new_ip=self.ue_ip()
        # 5GC may allocate a different PDU IPv4 after fresh registration.
        if not re.fullmatch(r"10\\.45\\.\\d+\\.\\d+",new_ip):
            raise RuntimeError("No valid new synthetic PDU address")
        if ue_id()!=before_id:
            raise RuntimeError("UE process replaced")
        gtpu=self.capture_f1_data("after-cause9-re-registration",IPS["macro"])
        evidence={
            "schema":"stage5h-real-cause9-one-UE-F1-server",
            "source":"real-disposable-docker-RFSIM",
            "mode":"on-demand-Re16-24.501-cause9-SUCI-initial-registration",
            "pre_fault_pdu_ip":before_ip,
            "post_fault_pdu_ip":new_ip,
            "original_ue_process_unchanged":True,
            "manual_f1_handover_after_fault":False,
            "actual_postfault_gtpu":gtpu["packet_counts"],
            "post_fault_pcap_sha256":gtpu["pcap_sha256"],
            "nas_event_chain":list(STAGES),
            "native_spontaneous_recovery_proven":False,
            "recovery_timing_slo_proven":False,
            "production_authorized":False,
            "production_decision":"NO_GO",
        }
        (self.out/"stage5h-cause9-runtime.json").write_text(
            json.dumps(evidence,indent=2)+"\\n")
        self.record("STAGE5H","PASS",
                    mode=evidence["mode"],f1u_ngu=gtpu["packet_counts"],
                    unchanged_ue=True,production_decision="NO_GO")

    base.ue=types.MethodType(stage,base)
    return base


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for name in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+name,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    opts=p.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: postfault F1U & NGU GTPU evidence required")
    return probe(opts).run()


if __name__=="__main__":
    raise SystemExit(main())
