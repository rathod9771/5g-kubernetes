#!/usr/bin/env python3
"""Isolated Stage 5A: AMF outage + DU reconnection, both UEs stay running.

Stage 4P proved NO spontaneous UE re-registration/PDU within 150s after
isolated AMF daemon restart. This deliberately tests whether subsequent
DU radio-link disruption can cause NAS/PDU recovery without restarting
the OAI UE *process*. DU restart is a controlled EXTRA FAULT, not native
recovery following AMF loss. PASS never clears that production gate.

Import from a PINNED Stage4P source checkout (not main or HP).
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time

def make_probe(source, args):
    root=pathlib.Path(source).resolve()
    for section in ("stage4o","stage4k","stage4l","stage4p"):
        if not (root/section).is_dir():
            raise RuntimeError("Pinned experiment baseline lacks "+section)
        sys.path.insert(0,str(root/section))
    from run_dual_ue_amf_restart import Recovery, ALIVE, NGAP, REDIAL
    from run_ue_registration import IPS, REG, PDU, name, sh
    from run_two_ues import SMALL_IP

    class Stage5A(Recovery):
        def ue_identity(self):
            identity={}
            for role in ("ue","ue-small"):
                info=json.loads(sh("docker","inspect",name(role)))
                if len(info)!=1 or not info[0].get("State",{}).get("Running"):
                    raise RuntimeError(role+" must have one live process")
                state=info[0]["State"]
                identity[role]={
                    "container_id":info[0]["Id"],
                    "pid":state["Pid"],
                    "started":state.get("StartedAt"),
                    "restart_count":info[0].get("RestartCount",0),
                }
            return identity

        def wait_fresh_du_setup(self, expected_du, previous_count):
            # A previously successful F1 Setup cannot count as reconnection.
            pattern=re.compile("Received F1SetupRequest",re.I)
            deadline=time.monotonic()+110
            while time.monotonic()<deadline:
                self.demand(*ALIVE)
                curr=len(pattern.findall(self.logs("cu")))
                if curr>previous_count:
                    self.record("T58-"+expected_du+"-DU-rejoined-F1", "OBSERVED",
                                prior=previous_count,now=curr)
                    return
                time.sleep(3)
            raise RuntimeError("No NEW F1 setup observed after "+expected_du+" DU restart")

        def probe_ue_state(self, label, before):
            records={}
            for role in ("ue","ue-small"):
                out=self.logs(role)
                addr=sh("docker","exec",name(role),"ip","-o","-4","addr","show",
                        check=False,timeout=20)
                regs=len(REG.findall(out))
                pdus=len(PDU.findall(out))
                baseline=before[role]
                records[role]={
                    "fresh_registration":regs>baseline[0],
                    "fresh_pdu_accept":pdus>baseline[1],
                    "has_pdu_tun":"oaitun_ue1" in addr and "10.45." in addr,
                }
            self.out.joinpath(label+"-NAS-PDU-state.json").write_text(
                json.dumps({"state":records,"decision":"NO_GO"},indent=2)+"\n")
            return records

        def fault_and_recover(self):
            self.demand(*ALIVE)
            self.sctp_check("T50-initial-AMF-and-both-F1-C-peers")
            before_ng=len(NGAP.findall(self.logs("cu")))
            before_redial=len(REDIAL.findall(self.logs("cu")))
            before_nas={
                role:(len(REG.findall(self.logs(role))),len(PDU.findall(self.logs(role))))
                for role in ("ue","ue-small")
            }
            if before_ng<1 or self.amf_count()!=1:
                raise RuntimeError("Baseline AMF/NGAP absent")
            initial=self.ue_identity()
            for old,new in (("gtpu.pcap","before-fault-gtpu.pcap"),
                            ("dual-ue-traffic-evidence.json","before-fault-data.json")):
                file=self.out/old
                if file.exists(): file.rename(self.out/new)
            self.stop_amf()
            self.await_redial(before_redial)
            self.start_amf()
            self.await_ngap(before_ng)
            self.sctp_check("T55-NGAP-and-F1-survived-restart")
            # Read-only observe first; do not claim a spontaneous recovery
            # unless fresh NAS/PDU service actually happens.
            self.record("T56-native-recovery-unproven-before-radio-fault","OBSERVED",
                        state=self.probe_ue_state("after-amf-only",before_nas))
            # Deliberately restart ONLY the DUs to test OAI UE radio recovery.
            # This is not transparent AMF failover. No UE process restart.
            f1setup=re.compile("Received F1SetupRequest",re.I)
            for label in ("macro","small"):
                previous=len(f1setup.findall(self.logs("cu")))
                sh("docker","restart","-t","6",name(label),timeout=45)
                self.record("T57-"+label+"-DU-process-restarted","OBSERVED")
                self.wait_fresh_du_setup(label,previous)
                self.demand(*ALIVE)
            deadline=time.monotonic()+155
            states=None
            while time.monotonic()<deadline:
                self.demand(*ALIVE)
                states=self.probe_ue_state("after-radio-link-fault",before_nas)
                if all(all(fields.values()) for fields in states.values()):
                    break
                time.sleep(7)
            after=self.ue_identity()
            if any(initial[x]!=after[x] for x in initial):
                raise RuntimeError("UE container/process restarted; experiment invalid")
            if not states or not all(all(s.values()) for s in states.values()):
                self.record("T59-radio-triggered-UE-recovery","BLOCKED",
                            mode="DU restart only; same UE process",
                            states=states,ue_processes_unchanged=True)
                raise RuntimeError("NAS/PDU did not recover after controlled DU disruption; no UE restart attempted")
            self.record("T59-radio-triggered-UE-recovery","PASS",
                        mode="DU restart only; same UE process",
                        ue_processes_unchanged=True)
            self.start_capture()
            try:
                self.post_ping()
                time.sleep(2)
            finally:
                self.stop_capture()
            self.check_post_gtpu()
            self.record("STAGE5A","PASS",
                        **{"production_decision":"NO_GO",
                           "native_amf_recovery_proven":False,
                           "recovery_mode":"additional controlled DU restart; no UE process restart"})

    return Stage5A(args)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    opts=p.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: real wire GTP-U verification required")
    return make_probe(opts.baseline,opts).run()

if __name__=="__main__":
    raise SystemExit(main())
