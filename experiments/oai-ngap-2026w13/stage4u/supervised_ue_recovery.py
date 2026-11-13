#!/usr/bin/env python3
"""Stage 4U: fail-closed supervised synthetic UE recovery after AMF outage.

Tests an explicitly orchestrator-assisted recovery mechanism in a disposable
GitHub-hosted Open5GS + dual-H-CRAN-DU + two synthetic NR-UE setup. Does not
claim native UE self-recovery or recommend automatic restarts of user devices.

Parent Stage 4P/4Q first demonstrates the real AMF outage, patched CU redial,
NEW NG Setup Response, and both F1-C peers remaining connected. The supervisor
then requires two consecutive missing UE PDU tunnel observations, checks
core NGAP stability, and allows at most one restart of each disposable UE.
It requires fresh NAS+PDU accepts, unique UE IPs, and post-fault GTP-U data.
"""
import argparse
import datetime
import json
import pathlib
import sys
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4q"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4p"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4o"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4k"))

from controlled_reattach import ControlledRecovery, SMALL_IP
from run_dual_ue_amf_restart import ALIVE
from run_two_ues import ipv4_pdu
from run_ue_registration import REG,PDU,IPS,name,sh

RESTART_POLICY={
  "observe_interval_seconds":3,
  "consecutive_missing_pdu_samples":2,
  "max_synthetic_ue_restarts_per_cycle":1,
  "per_ue_nas_timeout_seconds":180,
  "per_ue_pdu_timeout_seconds":120,
}

class SupervisedRecovery(ControlledRecovery):
    def record(self,label,status,**extra):
        if label in ("STAGE4P","STAGE4Q"):
            label="STAGE4U"
            extra["recovery_mode"]="automated supervised synthetic UE restart"
            extra["native_ue_autonomous_recovery"]="NOT_VERIFIED"
        super().record(label,status,**extra)

    def await_automatic_ue_service(self,baselines):
        """Automation is tested, NEVER mistaken for self-reattachment.

        Preconditions inherited: actual AMF process restart, CU re-dial,
        new NGSetupResponse and CU->DU 100+101 SCTP associations present.
        The lab supervisor is deliberately not installed in HP Kubernetes.
        """
        start=time.monotonic()
        self.demand(*ALIVE)
        self.sctp_check("T60-supervisor-NGAP-and-two-F1C-peers-before-action")
        if self.amf_count()!=1:
            raise RuntimeError("AMF process is not stable; suppress UE remediation")

        prior={}
        for label in ("ue","ue-small"):
            logs=self.logs(label)
            previous_reg,previous_pdu=baselines[label]
            now_reg=len(REG.findall(logs))
            now_pdu=len(PDU.findall(logs))
            prior[label]={
                "registration_accepts":now_reg,
                "pdu_accepts":now_pdu,
                "new_accepts":now_reg>previous_reg and now_pdu>previous_pdu,
            }

        misses={label:0 for label in ("ue","ue-small")}
        snapshots=[]
        for i in range(3):
            self.demand(*ALIVE)
            self.sctp_check("T61-supervisor-SCTP-check-"+str(i+1))
            sample={"sample":i+1,"elapsed_sec":round(time.monotonic()-start,1),
                    "amf_process_count":self.amf_count(),"ues":{}}
            if sample["amf_process_count"]!=1:
                raise RuntimeError("Unexpected AMF instability: suppress automated UE restart")
            for label in misses:
                state=sh("docker","exec",name(label),"ip","-o","-4","addr","show",
                         check=False,timeout=20)
                tunnel=("oaitun_ue1" in state and "10.45." in state)
                sample["ues"][label]={"has_active_pdu_tunnel":tunnel}
                misses[label]=(0 if tunnel else misses[label]+1)
            snapshots.append(sample)
            if i<2:time.sleep(RESTART_POLICY["observe_interval_seconds"])

        self.out.joinpath("supervisor-preaction-observations.json").write_text(
            json.dumps(snapshots,indent=2)+"\n")
        if all(misses[label]==0 for label in misses):
            # If the UEs have actually self-recovered after NGAP, require
            # fresh NAS and PDU evidence; do not recreate healthy UEs.
            fresh={}
            for label in misses:
                before_reg,before_pdu=baselines[label]
                content=self.logs(label)
                fresh[label]=len(REG.findall(content))>before_reg and (
                    len(PDU.findall(content))>before_pdu)
            if not all(fresh.values()):
                raise RuntimeError(
                    "Both tunnels present but new NAS/PDU accepts missing after outage")
            self.record("T62-intrinsic-UE-recovery","PASS",intervention=False)
            return

        # Only two synthetic test UEs from this run are permissible targets.
        # Missing tunnel must persist for >=2 consecutive samples.
        needs_restart=[label for label in misses if misses[label]>=2]
        if not needs_restart:
            raise RuntimeError("Uncertain UE service state; hysteresis gate prevents restart")
        if len(needs_restart)>2:
            raise RuntimeError("Out-of-scope restart target rejected")
        self.record("T62-supervisor-detected-persistent-service-loss","PASS",
                    missing_ue_tunnels=needs_restart,
                    samples=len(snapshots),hysteresis=RESTART_POLICY["consecutive_missing_pdu_samples"])
        self.capture("supervised-recovery-before-action")
        restarted={}
        specs={
            "ue":(IPS["ue"],IPS["macro"],"ue.conf"),
            "ue-small":("172.29.76.41",SMALL_IP,"small-ue.conf")
        }
        for label in needs_restart:
            if restarted.get(label,0)>=RESTART_POLICY["max_synthetic_ue_restarts_per_cycle"]:
                raise RuntimeError("Per-UE supervised restart cap exceeded")
            self.sctp_check("T63-before-"+label+"-recreate-SCTP-gate")
            if self.amf_count()!=1:
                raise RuntimeError("AMF failed again: abort remediation safely")
            self.recreate_ue(label,*specs[label])
            restarted[label]=restarted.get(label,0)+1
            peers=("core","cu","macro","small",label)
            self.wait_log("T64-"+label+"-fresh-NAS-registration","{}".format(label),REG,
                          RESTART_POLICY["per_ue_nas_timeout_seconds"],peers)
            self.wait_log("T65-"+label+"-fresh-PDU-session","{}".format(label),PDU,
                          RESTART_POLICY["per_ue_pdu_timeout_seconds"],peers)

        # Do not silently accept a non-restarted UE that has lost service.
        result={}
        for label in ("ue","ue-small"):
            output=sh("docker","exec",name(label),"ip","-o","-4","addr","show",timeout=20)
            if "oaitun_ue1" not in output:
                raise RuntimeError(label+" still has no PDU tunnel")
            result[label]=ipv4_pdu(output)
        if result["ue"]==result["ue-small"]:
            raise RuntimeError("Duplicate PDU IP after supervised service recovery")
        self.demand(*ALIVE)
        self.sctp_check("T66-both-F1C-and-NGAP-survived-supervised-UE-recovery")
        self.out.joinpath("supervised-ue-recovery-policy.json").write_text(
            json.dumps({
                "policy":RESTART_POLICY,
                "trigger":"two successive absent PDU TUN observations after fresh NG Setup",
                "affected_disposable_UEs":needs_restart,
                "synthetic_ue_restarts":restarted,
                "new_pdu_ipv4":result,
                "intervention_duration_sec":round(time.monotonic()-start,1),
                "production_decision":"NO_GO",
                "native_ue_autonomous_recovery":"NOT_PROVEN",
                "not_tested":"real handsets, staging OSM rollback, sustained load SLO"
            },indent=2)+"\n")
        self.record("T67-two-synthetic-UE-PDU-services-supervised-restored","PASS",
                    recovered_pdu_ips=result,restarted_UEs=restarted,
                    intervention_sec=round(time.monotonic()-start,1))
        self.capture("after-supervised-UE-recovery")
        # Inherited parent fault_and_recover() now requires 2-UE ICMP +
        # bidirectional GTP-U per F1-U and NG-U, with TEIDs and SCTP alive.

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu is mandatory")
    return SupervisedRecovery(args).run()

if __name__=="__main__":
    sys.exit(main())
