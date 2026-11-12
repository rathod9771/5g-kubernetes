#!/usr/bin/env python3
"""Stage 4Q: isolated controlled UE reattachment after real AMF failure.

The automatic-recovery test Stage 4P run 43219517357 demonstrably FAILED
to restore both UE PDU interfaces within 150 seconds. This experiment tests
explicit operational reattachment of the TWO disposable OAI UE simulator
processes, not automatic 5G service continuity and not production readiness.

Preconditions: two distinct UEs active, both DUs attached, real GTP-U;
AMF terminated and restarted; CU redial and fresh NG Setup; F1-C alive.
Then explicitly replace each synthetic UE process without touching AMF,
SMF, UPF, CU, DUs or any production system. Require new Registration Accept,
PDU Session Establishment Accept, unique UE IPs, 2-UE ICMP and bidirectional
F1-U/NG-U GTP-U packet evidence AFTER that intervention.
"""
import argparse
import pathlib
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4p"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4o"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4k"))
from run_dual_ue_amf_restart import Recovery, ALIVE, SMALL_IP
from run_two_ues import ipv4_pdu
from run_ue_registration import REG, PDU, NETWORK, IPS, name, sh

class ControlledRecovery(Recovery):
    def record(self,label,status,**data):
        if label=="STAGE4P":
            label="STAGE4Q"
            data["recovery_mode"]="controlled synthetic UE process recreation"
            data["automatic_ue_recovery"]="FAILED_IN_STAGE4P_RUN_43219517357"
        super().record(label,status,**data)

    def recreate_ue(self,label,ip,du_ip,config):
        file=self.priv/config
        if not file.exists() or not file.is_file():
            raise RuntimeError("Missing private synthetic UE configuration "+config)
        # Old UE containers' RRC/PDCP stacks were released on NGAP loss.
        # Recreate only disposable simulator processes; no real subscribers.
        sh("docker","rm","-f",name(label),timeout=25)
        sh("docker","run","-d","--name",name(label),
           "--network",NETWORK,"--ip",ip,
           "--cpus","1.5","--memory","3g",
           "--cap-add","NET_ADMIN","--device","/dev/net/tun",
           "-v",str(file)+":/opt/oai-nr-ue/etc/ue.conf:ro",
           "--entrypoint","/opt/oai-nr-ue/bin/nr-uesoftmodem",
           self.opts.ue_image,"-O","/opt/oai-nr-ue/etc/ue.conf",
           "--rfsim","-r","106","--numerology","1","--band","78",
           "-C","3450720000","--ssb","516",
           "--rfsimulator.serveraddr",du_ip)
        self.record("T35B-"+label+"-simulator-recreated","PASS",
                    du_ip=du_ip,ue_ip=ip,synthetic_credentials_only=True)

    def await_automatic_ue_service(self,baselines):
        # Do not claim PASS on automatic recovery: previous bounded run
        # proved it failed. Here measure deliberate re-registration separately.
        self.record("T35A-auto-UE-reattach","NOT_ASSERTED",
                    reason="Automatic reattach already failed Stage4P run 43219517357")
        self.capture("before-explicit-UE-restart")

        self.recreate_ue("ue",IPS["ue"],IPS["macro"],"ue.conf")
        base=("core","cu","macro","small","ue")
        self.wait_log("T35C-macro-UE-new-RegistrationAccept","ue",REG,180,base)
        self.wait_log("T35D-macro-UE-new-PDU-SessionAccept","ue",PDU,120,base)

        self.recreate_ue("ue-small","172.29.76.41",SMALL_IP,"small-ue.conf")
        both=base+("ue-small",)
        self.wait_log("T35E-small-UE-new-RegistrationAccept","ue-small",REG,180,both)
        self.wait_log("T35F-small-UE-new-PDU-SessionAccept","ue-small",PDU,120,both)
        macro=sh("docker","exec",name("ue"),"ip","-o","-4","addr","show",
                 timeout=20)
        small=sh("docker","exec",name("ue-small"),"ip","-o","-4","addr","show",
                 timeout=20)
        m_ip=ipv4_pdu(macro)
        s_ip=ipv4_pdu(small)
        if m_ip==s_ip:
            raise RuntimeError("Recreated UEs received duplicate PDU IPs")
        if "oaitun_ue1" not in macro or "oaitun_ue1" not in small:
            raise RuntimeError("Recreated UE PDU interfaces not present")
        self.demand(*ALIVE)
        self.record("T35G-controlled-dual-UE-PDU-services-restored","PASS",
                    macro_ue_pdu_ip=m_ip,small_ue_pdu_ip=s_ip,
                    requires_explicit_ue_restart=True)
        self.capture("after-operator-simulated-UE-restart")

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu required")
    return ControlledRecovery(args).run()

if __name__=="__main__":
    sys.exit(main())
