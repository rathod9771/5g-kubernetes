#!/usr/bin/env python3
"""Stage 4O: two real OAI RFsim UEs concurrently on macro+small H-CRAN DUs.

Disposable GitHub-hosted Open5GS/Mongo with TWO distinct synthetic users.
Requires both NAS/PDU accepts, unique PDU IPs, simultaneous loss-free ICMP,
per-DU F1-U bidirectional GTPv1-U and >=2 NG-U TEIDs in each direction.
No real subscribers, RF hardware, staging credentials, or HP cluster.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4m"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"stage4k"))
import gtpu_wire
from dual_du_macro_ue_traffic import DualDuProbe, SMALL_IP
from run_ue_registration import IPS, SIM, REG, PDU, NETWORK, db_record, name, sh
from pymongo import MongoClient

SIM2={"imsi":"208930000000002",
      "key":"0f1e2d3c4b5a69788796a5b4c3d2e1f0",
      "opc":"fedcba98765432100123456789abcdef"}

def ipv4_pdu(interfaces):
    hits=re.findall(r"\binet\s+(10\.45\.\d+\.\d+)/\d+",interfaces)
    if len(hits)!=1:raise RuntimeError("expected exactly one allocated UE PDU IPv4")
    return hits[0]

def good_ping(data):
    match=re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",data)
    return bool(match and int(match.group(1))==6 and int(match.group(2))==6)

class DualUEProbe(DualDuProbe):
    def ue(self):
        super().ue()  # macro UE registered+PDU and data-plane passed with both DUs
        self.demand("core","cu","macro","small","ue")
        client=MongoClient(IPS["mongo"],27017,serverSelectionTimeoutMS=2500)
        table=client["open5gs"]["subscribers"]
        subscriber=db_record()
        subscriber["imsi"]=SIM2["imsi"]
        subscriber["security"]["k"]=SIM2["key"]
        subscriber["security"]["opc"]=SIM2["opc"]
        if table.count_documents({"imsi":SIM2["imsi"]}):
            raise RuntimeError("second synthetic subscriber already exists")
        table.insert_one(subscriber)
        if table.count_documents({"imsi":SIM2["imsi"]})!=1:
            raise RuntimeError("second synthetic subscriber database insertion failed")
        self.record("T24-second-independent-synthetic-subscriber","PASS",
                    identity="REDACTED",is_production=False)

        conf=self.priv/"small-ue.conf"
        conf.write_text('uicc0 = {\n'
          ' imsi = "'+SIM2["imsi"]+'";\n'
          ' key = "'+SIM2["key"]+'";\n'
          ' opc = "'+SIM2["opc"]+'";\n'
          ' pdu_sessions = ({ dnn = "internet"; nssai_sst = 1; nssai_sd = 0x010203; });\n};\n')
        conf.chmod(0o600)
        sh("docker","run","-d","--name",name("ue-small"),
           "--network",NETWORK,"--ip","172.29.76.41",
           "--cpus","1.5","--memory","3g",
           "--cap-add","NET_ADMIN","--device","/dev/net/tun",
           "-v",str(conf)+":/opt/oai-nr-ue/etc/ue.conf:ro",
           "--entrypoint","/opt/oai-nr-ue/bin/nr-uesoftmodem",
           self.opts.ue_image,"-O","/opt/oai-nr-ue/etc/ue.conf",
           "--rfsim","-r","106","--numerology","1","--band","78",
           "-C","3450720000","--ssb","516",
           "--rfsimulator.serveraddr",SMALL_IP)
        self.record("T25-small-UE-started-with-macro-UE-active","PASS")
        peers=("core","cu","macro","small","ue","ue-small")
        self.wait_log("T26-second-UE-NAS-RegistrationAccept","ue-small",REG,180,peers)
        self.wait_log("T27-second-UE-PDUSessionEstablishmentAccept","ue-small",PDU,120,peers)
        a=sh("docker","exec",name("ue"),"ip","-o","-4","addr","show",
             timeout=20)
        b=sh("docker","exec",name("ue-small"),"ip","-o","-4","addr","show",
             timeout=20)
        first=ipv4_pdu(a); second=ipv4_pdu(b)
        if first==second:raise RuntimeError("two UE PDU IPv4 addresses must differ")
        self.record("T28-distinct-PDU-IPv4-addresses","PASS",
                    macro_ue=first,small_ue=second)

        # Preserve the earlier single-UE packet trace as separate evidence.
        for original,new in (
            ("gtpu.pcap","macro-only-gtpu.pcap"),
            ("GTPU-GPDU-evidence.json","macro-only-gtpu-evidence.json"),
            ("UE-internal-ping.txt","macro-only-ICMP.txt"),
        ):
            old=self.out/original
            if old.exists():old.rename(self.out/new)

        self.start_capture()
        proc=[]
        try:
            for label in ("ue","ue-small"):
                proc.append((label,subprocess.Popen(
                    ["docker","exec",name(label),"ping",
                     "-I","oaitun_ue1","-c","6","-i","0.5","-W","2","10.45.0.1"],
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)))
            for label,p in proc:
                try:out,_=p.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    p.kill()
                    out,_=p.communicate(timeout=7)
                    raise RuntimeError(label+" concurrent ping timed out: "+out[-500:])
                (self.out/(label+"-concurrent-ping.txt")).write_text(out)
                if p.returncode!=0 or not good_ping(out):
                    raise RuntimeError(label+" PDU-ICMP lost replies: "+out[-1500:])
            self.record("T29-two-simultaneous-UE-ping-streams","PASS",
                        each_transmitted=6,each_received=6)
            time.sleep(2)
        finally:
            self.stop_capture()

        trace=self.out/"gtpu.pcap"
        if not trace.exists() or trace.stat().st_size<120:
            raise RuntimeError("missing concurrent GTP-U packet capture")
        gtpu_wire.DU=IPS["macro"]
        macro=gtpu_wire.analyze(trace)
        gtpu_wire.DU=SMALL_IP
        small=gtpu_wire.analyze(trace)
        for label,record in (("macro",macro),("small",small)):
            for direction in ("f1u_ul","f1u_dl"):
                if record["G_PDU_counts"][direction]<3:
                    raise RuntimeError(label+" failed real F1-U G-PDU "+direction)
        for direction in ("ngu_ul","ngu_dl"):
            if macro["G_PDU_counts"][direction]<6:
                raise RuntimeError("insufficient NG-U G-PDU "+direction)
            if len(macro["TEIDs"][direction])<2:
                raise RuntimeError("NG-U did not carry two distinct UE TEIDs: "+direction)
        details={
            "scope":"two concurrent synthetic UE PDU sessions over distinct H-CRAN DUs",
            "macro_ue_ip":first,"small_ue_ip":second,
            "macro_f1u":macro["G_PDU_counts"],
            "small_f1u":small["G_PDU_counts"],
            "ngu_teid_count_ul":len(macro["TEIDs"]["ngu_ul"]),
            "ngu_teid_count_dl":len(macro["TEIDs"]["ngu_dl"]),
            "capture_sha256":macro["pcap_sha256"],
            "production_release":"NO_GO",
        }
        (self.out/"dual-ue-traffic-evidence.json").write_text(json.dumps(details,indent=2)+"\n")
        self.record("T30-both-DUs-concurrent-F1U-and-two-NGU-TEIDs","PASS",
                    macro_ul=macro["G_PDU_counts"]["f1u_ul"],
                    macro_dl=macro["G_PDU_counts"]["f1u_dl"],
                    small_ul=small["G_PDU_counts"]["f1u_ul"],
                    small_dl=small["G_PDU_counts"]["f1u_dl"],
                    ngu_ul=macro["G_PDU_counts"]["ngu_ul"],
                    ngu_dl=macro["G_PDU_counts"]["ngu_dl"])
        self.demand(*peers)
        assoc=sh("docker","exec",name("cu"),"cat","/proc/net/sctp/assocs",timeout=20)
        if IPS["macro"] not in assoc or SMALL_IP not in assoc or IPS["core"] not in assoc:
            raise RuntimeError("F1-C/NGAP SCTP peer lost while both UEs active")
        self.capture("final-two-ues")
        self.record("STAGE4O","PASS",
                    scope="two UEs and two DUs; concurrent ICMP, real F1-U and NG-U G-PDUs")

    def capture(self,label):
        super().capture(label)
        if self.live("ue-small"):
            raw=self.logs("ue-small")
            for x in list(SIM.values())+list(SIM2.values()):
                raw=raw.replace(x,"[SYNTHETIC-REDACTED]")
            (self.out/(label+"-ue-small.log")).write_text(raw[-55000:])

    def cleanup(self):
        sh("docker","rm","-f",name("ue-small"),check=False,timeout=15)
        super().cleanup()

def main():
    p=argparse.ArgumentParser()
    for arg in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+arg,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    a=p.parse_args()
    if not a.verify_gtpu:raise SystemExit("FAIL CLOSED: --verify-gtpu mandatory")
    return DualUEProbe(a).run()

if __name__=="__main__":
    sys.exit(main())
