#!/usr/bin/env python3
"""Stage 4L: fail-closed real F1-U and NG-U traffic test, isolated H-CRAN macro DU.

Build on Stage 4K's real Open5GS/UE registration + PDU. Source proof: ICMP
echo/replies over OAI UE TUN and both directional GTPv1-U T-PDUs on F1-U
(UDP/38472) and NG-U (UDP/2152), captured from a disposable Docker bridge.
Never accesses HP Kubernetes, its subscriber data, or live radio hardware.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
import traceback

from scapy.all import IP, UDP, PcapReader

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "stage4k"))
from run_ue_registration import Probe, sh, name, IPS, NETWORK

GATEWAY="10.45.0.1"
# Trust only the exact expected CI bridge peers, not unrelated background GTP.
PATHS={
  "f1u_du_to_cu":(IPS["macro"],IPS["cu"],38472),
  "f1u_cu_to_du":(IPS["cu"],IPS["macro"],38472),
  "ngu_cu_to_upf":(IPS["cu"],IPS["core"],2152),
  "ngu_upf_to_cu":(IPS["core"],IPS["cu"],2152),
}

def inspect_capture(path):
    captures={k:{"packets":0,"bytes":0,"teids":[]} for k in PATHS}
    captured=0
    with PcapReader(str(path)) as reader:
        for packet in reader:
            captured+=1
            if IP not in packet or UDP not in packet:
                continue
            ipv4=packet[IP]
            udp=packet[UDP]
            raw=bytes(udp.payload)
            if len(raw)<8 or (raw[0]>>5)!=1 or not (raw[0] & 0x10):
                continue
            if raw[1]!=0xff: # GTPv1-U T-PDU, not an echo/path management packet
                continue
            reported_size=int.from_bytes(raw[2:4],"big")+8
            if reported_size>len(raw): # truncated capture cannot count as evidence
                continue
            for key,(src,dst,port) in PATHS.items():
                if ipv4.src==src and ipv4.dst==dst and (
                   udp.sport==port or udp.dport==port):
                    d=captures[key]
                    d["packets"]+=1
                    d["bytes"]+=len(raw)
                    teid=raw[4:8].hex()
                    if teid not in d["teids"]:d["teids"].append(teid)
    return captured,captures

class DataPlaneProbe(Probe):
    def ue(self):
        super().ue()
        self.dataplane()

    def dataplane(self):
        self.demand("core","cu","macro","ue")
        ue_addr=sh("docker","exec",name("ue"),"ip","-o","-4","addr","show",
                   timeout=20)
        match=re.search(r"^\d+:\s+([^\s:]+).*?\binet\s+(10\.45\.\d+\.\d+)/\d+",
                        ue_addr,re.MULTILINE)
        if not match:
            raise RuntimeError("Missing real OAI UE PDU TUN IP in 10.45/16")
        iface,ue_ip=match.group(1),match.group(2)
        if not iface.startswith(("oaitun","uesimtun")):
            raise RuntimeError("Assigned IP not on a UE PDU tunnel")
        self.record("T12-real-UE-PDU-interface","PASS",
                    interface=iface,ue_ip=ue_ip)

        # The isolated Open5GS UPF requires a local kernel TUN gateway.
        # Stage 4K did not test N6. The address is the known 10.45.0.0/16
        # pool chosen by Open5GS default release config.
        state=sh("docker","exec",name("core"),"/bin/sh","-c",
                 "ip -o -4 addr show dev ogstun 2>/dev/null || true",
                 check=False,timeout=20)
        if GATEWAY not in state:
            result=sh("docker","exec",name("core"),"/bin/sh","-ec",
                      "ip tuntap add name ogstun mode tun && "
                      "ip addr add 10.45.0.1/16 dev ogstun && "
                      "ip link set ogstun up",timeout=25)
        state=sh("docker","exec",name("core"),"ip","-o","-4",
                 "addr","show","dev","ogstun",timeout=20)
        if GATEWAY not in state:
            raise RuntimeError("Open5GS UPF gateway TUN did not come up")
        self.record("T13-UPF-N6-gateway-TUN","PASS",gateway=GATEWAY)

        pcap=self.out/"isolated-user-plane-only.pcap"
        capture=subprocess.Popen(
            ["sudo","tcpdump","-i","any","-s","0","-U","-w",str(pcap),
             "udp","and","(","port","2152","or","port","38472",")"],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        try:
            time.sleep(2)
            if capture.poll() is not None:
                raise RuntimeError("tcpdump failed to start: "+
                                   (capture.stderr.read() if capture.stderr else "")[-900:])
            # Ping is carried by actual OAI UE PDCP/RLC, F1-U, NG-U and
            # Open5GS UPF; do not treat container bridge ICMP as evidence.
            result=sh("docker","exec",name("ue"),"ping",
                      "-I",iface,"-c","5","-i","1","-W","2",GATEWAY,
                      check=False,timeout=32)
            self.out.joinpath("UE-to-UPF-ICMP.txt").write_text(result)
            pkt=re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",
                          result)
            received=int(pkt.group(2)) if pkt else 0
            sent=int(pkt.group(1)) if pkt else 0
            if sent!=5 or received<3:
                raise RuntimeError(f"UE-PDU ICMP failed: {received}/{sent} replies; "+result[-1100:])
            self.record("T14-real-UE-to-UPF-ICMP-replies","PASS",
                        transmitted=sent,received=received,via_ue_tun=True)
            time.sleep(2)
        finally:
            if capture.poll() is None:
                capture.terminate()
                try:capture.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    capture.kill()
                    capture.wait(timeout=5)
            if capture.stderr:
                capture.stderr.close()

        if not pcap.exists() or pcap.stat().st_size<100:
            raise RuntimeError("User-plane packet capture is missing/empty")
        total,paths=inspect_capture(pcap)
        # Only GTP-U T-PDUs, no SCTP signaling, PFCP or UDP readiness.
        result={"stage":"4L-real-user-plane", "at":datetime.datetime.now(
                 datetime.timezone.utc).isoformat(),"success":False,
                "ue_tun_ip":ue_ip,"upf_tun_gateway":GATEWAY,
                "pcap_sha256":hashlib.sha256(pcap.read_bytes()).hexdigest(),
                "captured_frames":total,"gtpu_tpdus":paths,
                "production_decision":"NO_GO",
                "limits":"one DU, isolated 5GC, synthetic UE, no E2 indication/RC action"}
        self.out.joinpath("gtpu-evidence.json").write_text(json.dumps(result,indent=2)+"\n")
        missing=[label for label,v in paths.items() if v["packets"]<3]
        if missing:
            raise RuntimeError("Insufficient actual bidirectional GTPv1-U T-PDU evidence: "+
                               ", ".join(missing))
        result["success"]=True
        self.out.joinpath("gtpu-evidence.json").write_text(json.dumps(result,indent=2)+"\n")
        self.record("T15-real-F1U-bidirectional-GTPU","PASS",
                    uplink=paths["f1u_du_to_cu"]["packets"],
                    downlink=paths["f1u_cu_to_du"]["packets"])
        self.record("T16-real-NGU-bidirectional-GTPU","PASS",
                    uplink=paths["ngu_cu_to_upf"]["packets"],
                    downlink=paths["ngu_upf_to_cu"]["packets"])
        self.capture("after-user-plane")
        self.record("STAGE4L","PASS",
                    scope="real UE TUN ICMP + 4 directional F1-U/NG-U GTP-U data paths; not production")

def main():
    p=argparse.ArgumentParser()
    for x in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+x,required=True)
    a=p.parse_args()
    return DataPlaneProbe(a).run()

if __name__=="__main__":
    sys.exit(main())
