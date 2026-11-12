#!/usr/bin/env python3
"""Isolated H-CRAN small DU 101 UE/PDU/GTP-U with macro DU 100 also connected.

Extends Stage 4M dual-DU setup, but UE RFsim connects to SMALL DU.
Packet classifier is adjusted to require F1-U peer 172.29.76.31, never macro.
This is ONE small-cell UE, not two simultaneous UEs or production approval.
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "stage4m"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "stage4k"))
import gtpu_wire
from dual_du_macro_ue_traffic import DualDuProbe, SMALL_IP
from run_ue_registration import sh, name, SIM, REG, PDU, IPS

class SmallCellUEProbe(DualDuProbe):
    def ue(self):
        self.demand("core","cu","macro","small")
        # Stage 4L's GTP-U parser must require the small-cell DU peer IP.
        gtpu_wire.DU=SMALL_IP
        self.start_capture()
        ueconf=self.priv/"ue.conf"
        ueconf.write_text('uicc0 = {\n'
            '  imsi = "'+SIM["imsi"]+'";\n'
            '  key = "'+SIM["key"]+'";\n'
            '  opc = "'+SIM["opc"]+'";\n'
            '  pdu_sessions = ({ dnn = "internet"; nssai_sst = 1; nssai_sd = 0x010203; });\n};\n')
        ueconf.chmod(0o600)
        sh("docker","run","-d","--name",name("ue"),
           "--network","oai-ngap-stage4k",
           "--ip",IPS["ue"],
           "--cpus","2","--memory","3g",
           "--cap-add","NET_ADMIN","--device","/dev/net/tun",
           "-v",str(ueconf)+":/opt/oai-nr-ue/etc/ue.conf:ro",
           "--entrypoint","/opt/oai-nr-ue/bin/nr-uesoftmodem",
           self.opts.ue_image,"-O","/opt/oai-nr-ue/etc/ue.conf",
           "--rfsim","-r","106","--numerology","1","--band","78",
           "-C","3450720000","--ssb","516",
           "--rfsimulator.serveraddr",SMALL_IP)
        self.record("T19-real-small-DU-RFsim-UE-started","PASS",du_id=101)
        peers=("core","cu","macro","small","ue")
        self.wait_log("T20-small-UE-NAS-RegistrationAccept","ue",REG,180,peers)
        self.wait_log("T21-small-UE-PDUSessionEstablishmentAccept","ue",PDU,120,peers)
        tun=sh("docker","exec",name("ue"),"/bin/sh","-c",
               "ip -o -4 addr show | grep -E 'oaitun|uesimtun' || true",
               check=False,timeout=20)
        if not tun.strip():
            raise RuntimeError("Small-UE PDU Accept without UE TUN IP")
        (self.out/"UE-PDU-interface.txt").write_text(tun)
        self.record("T22-small-UE-assigned-IP","PASS")
        self.verify_gtpu() # Captured F1-U must be SMALL_IP <-> CU, NG-U unchanged
        report_path=self.out/"GTPU-GPDU-evidence.json"
        report=json.loads(report_path.read_text())
        report["f1u_du_id"]=101
        report["f1u_du_ip"]=SMALL_IP
        report["macro_du_connected"]=True
        report_path.write_text(json.dumps(report,indent=2)+"\n")
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                 timeout=20)
        for p in (IPS["core"],IPS["macro"],SMALL_IP):
            if p not in assoc:raise RuntimeError("CU lost NGAP or one F1-C SCTP peer during small UE traffic: "+p)
        self.demand(*peers)
        self.record("T23-both-DUs-F1C-alive-during-small-UE-traffic","PASS")
        self.capture("small-UE-final")
        self.record("STAGE4N","PASS",scope=(
            "dual DU F1-C alive; small DU UE Registration+PDU+UPF ping"
            "+F1-U and NG-U G-PDU; macro DU has no UE traffic"))

def main():
    parser=argparse.ArgumentParser()
    for arg in ("rendered","output","cu-image","du-image","ue-image"):
        parser.add_argument("--"+arg,required=True)
    parser.add_argument("--verify-gtpu",action="store_true")
    opts=parser.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu mandatory")
    return SmallCellUEProbe(opts).run()

if __name__=="__main__":
    sys.exit(main())
