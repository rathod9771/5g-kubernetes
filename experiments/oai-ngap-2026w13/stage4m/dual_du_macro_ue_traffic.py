#!/usr/bin/env python3
"""Stage 4M isolated test: both F1-C DUs concurrently while macro UE transmits.

Extends the Stage 4L proven single-UE/NAS/PDU/F1-U/NG-U packet test. Starts
the *second* H-CRAN DU ID 101 against the same CU prior to UE registration.
Checks it remains SCTP-associated while macro UE packets pass in both
directions. Does NOT imply a UE connected through the small DU or its F1-U.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "stage4k"))
from run_ue_registration import Probe, IPS, NETWORK, configmap, name, sh

SMALL_IP="172.29.76.31"
REQ=re.compile(r"Received F1 Setup Request from gNB_DU 101",re.I)
RESP=re.compile(r"DU 101 .*sending F1 Setup Response",re.I)

class DualDuProbe(Probe):
    def ran(self):
        super().ran()  # Actual NGAP and macro DU 100 F1 Setup already proven.
        rendered=pathlib.Path(self.opts.rendered)
        conf=configmap(rendered/"small.yaml","ConfigMap",
                       "hcran-oai-small-config")["data"]["gnb.conf"]
        conf=conf.replace("POD_IP",SMALL_IP).replace("CU_IP",IPS["cu"])
        if "gNB_DU_ID = 101;" not in conf or SMALL_IP not in conf:
            raise RuntimeError("Small DU H-CRAN identity or runtime IP invalid")
        file=self.priv/"small.conf"
        file.write_text(conf)
        sh("docker","run","-d","--name",name("small"),
           "--network",NETWORK,"--ip",SMALL_IP,
           "--cpus","1.25","--memory","2g",
           "-v",str(file)+":/opt/oai-gnb/etc/gnb.conf:ro",
           "--entrypoint","/opt/oai-gnb/bin/nr-softmodem",
           self.opts.du_image,"-O","/opt/oai-gnb/etc/gnb.conf","--rfsim")
        self.record("T14-small-DU-101-started","PASS")
        self.wait_log("T15-F1SetupRequest-from-DU-101","cu",REQ,100,
                      ("core","cu","macro","small"))
        self.wait_log("T16-F1SetupResponse-to-DU-101","cu",RESP,45,
                      ("core","cu","macro","small"))
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                 timeout=20)
        for label,peer in (("core",IPS["core"]),("macro",IPS["macro"]),("small",SMALL_IP)):
            if peer not in assoc:
                raise RuntimeError("Missing established CU SCTP peer "+label)
        self.record("T17-CU-three-SCTP-peers-before-UE","PASS",
                    peers=["AMF","macro-DU-100","small-DU-101"])
        self.capture("dual-f1-before-ue")

    def ue(self):
        self.demand("small")
        super().ue() # real UE registration, PDU and GTP-U + ICMP success
        self.demand("core","cu","macro","small","ue")
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",
                 timeout=20)
        if SMALL_IP not in assoc or IPS["macro"] not in assoc:
            raise RuntimeError("F1-C association was lost under active macro UE GTP-U")
        self.record("T18-dual-DU-F1-stable-during-macro-UE-traffic","PASS")
        self.capture("dual-f1-after-ue-data")
        self.record("STAGE4M","PASS",scope=(
                    "Two active F1-C DUs, one macro UE real NAS+PDU+"
                    "F1-U/NG-U+ICMP; small DU has NO UE/data traffic"))

    def capture(self,label):
        super().capture(label)
        if self.live("small"):
            (self.out/(label+"-small.log")).write_text(self.logs("small")[-60000:])
            (self.out/(label+"-small-sctp.txt")).write_text(
                sh("docker","exec",name("small"),"cat","/proc/net/sctp/assocs",
                   check=False,timeout=20))

    def cleanup(self):
        # Only disposable test container on the same isolated GitHub runner.
        sh("docker","rm","-f",name("small"),check=False,timeout=20)
        super().cleanup()

def main():
    p=argparse.ArgumentParser()
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu required")
    return DualDuProbe(args).run()

if __name__=="__main__":
    sys.exit(main())
