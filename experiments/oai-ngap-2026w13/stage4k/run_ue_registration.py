#!/usr/bin/env python3
"""Disposable one-H-CRAN-DU real OAI UE registration and PDU session test.

No production connectivity; fixed synthetic 208/93 subscriber, never any
user's IMSI or security keys. Fail closed if real runtime proof is absent.
"""
import argparse, datetime, json, os, pathlib, re, shutil, subprocess, sys, time, traceback
import yaml
from pymongo import MongoClient
from gtpu_wire import analyze

NETWORK="oai-ngap-stage4k"
IPS={"mongo":"172.29.76.9","core":"172.29.76.10",
     "cu":"172.29.76.20","macro":"172.29.76.30","ue":"172.29.76.40"}
CORE_IMAGE="gradiant/open5gs:2.7.6"
MONGO_IMAGE="mongo:6.0"
# Public test-only examples. Never read subscriber data from HP or Git.
SIM={"imsi":"208930000000001","key":"465b5ce8b199b49faa5f0a2ee238a6bc",
     "opc":"e8ed289deba952e4283b54e88e6183ca"}
NGAP=re.compile(r"Received NGSetupResponse from AMF",re.I)
F1REQ=re.compile(r"Received F1 Setup Request from gNB_DU 100",re.I)
F1RESP=re.compile(r"DU 100 .*sending F1 Setup Response",re.I)
REG=re.compile(r"Registration Accept|5GMM-REGISTERED|registration procedure completed",re.I)
PDU=re.compile(r"PDU Session Establishment Accept|PDU session established",re.I)

def sh(*args, timeout=75, check=True):
    p=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"command exit {p.returncode}: {args[:4]} "
                           f"{p.stdout[-1200:]} {p.stderr[-1200:]}")
    return p.stdout+p.stderr
def name(x): return NETWORK+"-"+x
def configmap(file,kind,fullname):
    with pathlib.Path(file).open() as f:
        matches=[x for x in yaml.safe_load_all(f)
                 if isinstance(x,dict) and x.get("kind")==kind and
                 x.get("metadata",{}).get("name")==fullname]
    if len(matches)!=1:raise RuntimeError(f"expected one {kind}/{fullname}")
    return matches[0]
def db_record():
    bandwidth={"uplink":{"value":1,"unit":3},"downlink":{"value":1,"unit":3}}
    return {"imsi":SIM["imsi"],"subscribed_rau_tau_timer":12,"network_access_mode":0,
        "subscriber_status":0,"operator_determined_barring":0,"access_restriction_data":32,
        "slice":[{"sst":1,"sd":"010203","default_indicator":True,
                  "session":[{"name":"internet","type":3,"pcc_rule":[],
                              "ambr":bandwidth,"qos":{"index":9,"arp":{
                              "priority_level":8,"pre_emption_capability":1,
                              "pre_emption_vulnerability":1}}}]}],
        "ambr":bandwidth,
        "security":{"k":SIM["key"],"opc":SIM["opc"],"op":None,"amf":"8000"},
        "schema_version":1,"__v":0}

class Probe:
    def __init__(self,opts):
        self.opts=opts
        self.out=pathlib.Path(opts.output).resolve()
        self.out.mkdir(parents=True,exist_ok=True)
        self.priv=self.out/"PRIVATE-REMOVE-BEFORE-UPLOAD"
        self.priv.mkdir(mode=0o700,exist_ok=True)
        self.events=[]
        self.start=time.monotonic()
        self.pcap_process=None
        self.pcap_stderr=None
    def record(self,label,status,**more):
        record={"time":datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "seconds":round(time.monotonic()-self.start,1),
                "stage":label,"status":status,**more}
        self.events.append(record)
        (self.out/"results.json").write_text(json.dumps(self.events,indent=2)+"\n")
        print(json.dumps(record),flush=True)
    def live(self,item):
        return sh("docker","inspect","--format","{{.State.Running}}",
                  name(item),check=False,timeout=20).strip()=="true"
    def logs(self,item):
        return sh("docker","logs",name(item),check=False,timeout=20)
    def core_logs(self):
        # docker exec does not work after an NF exits and shuts down the
        # supervisor. docker cp still works on the stopped container.
        if self.live("core"):
            return sh("docker","exec",name("core"),"/bin/sh","-c",
                      "for f in /tmp/stage4k-log/*.log; do echo ==$f; tail -n 100 \"$f\"; done",
                      check=False,timeout=20)
        dump=self.priv/"nf-logs-after-exit"
        dump.mkdir(parents=True,exist_ok=True)
        copy=sh("docker","cp",name("core")+":/tmp/stage4k-log/.",
                str(dump),check=False,timeout=30)
        logs=[]
        for f in sorted(dump.glob("*.log")):
            logs.append("==== "+f.name+" ====")
            logs.append(f.read_text(errors="replace")[-7000:])
        status=sh("docker","inspect","--format","{{json .State}}",
                  name("core"),check=False,timeout=20)
        container_output=self.logs("core")
        return "Core exit state: "+status+"\n"+container_output+"\n"+copy+"\n"+"\n".join(logs)
    def demand(self,*items):
        for item in items:
            if not self.live(item):
                out=self.core_logs() if item=="core" else self.logs(item)
                raise RuntimeError(f"{item} stopped: {out[-3500:]}")
    def capture(self,label):
        for item in IPS:
            text=self.logs(item)
            for v in SIM.values():text=text.replace(v,"[SYNTH]")
            (self.out/f"{label}-{item}.log").write_text(text[-60000:])
        core=self.core_logs()
        for v in SIM.values():core=core.replace(v,"[SYNTH]")
        (self.out/f"{label}-core.log").write_text(core[-100000:])
        for item in ("cu","macro"):
            (self.out/f"{label}-{item}-sctp.txt").write_text(
                sh("docker","exec",name(item),"cat","/proc/net/sctp/assocs",
                   check=False,timeout=15))
    def wait_log(self,label,item,pattern,timeout,others):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.demand(*others)
            if pattern.search(self.logs(item)):
                self.record(label,"PASS",source=item)
                return
            time.sleep(3)
        self.capture(label+"-timeout")
        raise RuntimeError(f"TIMEOUT {label}: absent {pattern.pattern}; "+
                           self.logs(item)[-2600:])
    def bootstrap(self):
        for item in IPS:
            sh("docker","rm","-f",name(item),check=False)
        sh("docker","network","rm",NETWORK,check=False)
        sh("docker","network","create","--driver","bridge",
           "--subnet","172.29.76.0/24",NETWORK)
        self.record("T0-isolated-bridge","PASS")
        sh("docker","run","-d","--name",name("mongo"),
           "--network",NETWORK,"--ip",IPS["mongo"],
           "--cpus","0.7","--memory","1.5g",MONGO_IMAGE,"--bind_ip_all")
        db=MongoClient(IPS["mongo"],27017,serverSelectionTimeoutMS=2500)
        expiry=time.monotonic()+80
        while time.monotonic()<expiry:
            try:
                db.admin.command("ping")
                break
            except Exception:
                time.sleep(2)
        else:raise RuntimeError("synthetic Mongo database unavailable")
        table=db["open5gs"]["subscribers"]
        table.insert_one(db_record())
        if table.count_documents({"imsi":SIM["imsi"]})!=1:
            raise RuntimeError("synthetic subscriber was not inserted")
        self.record("T1-synthetic-test-subscriber","PASS",subscriber="REDACTED")

        tmp=name("defaults")
        sh("docker","rm","-f",tmp,check=False)
        try:
            sh("docker","create","--name",tmp,"--entrypoint","/bin/true",CORE_IMAGE)
            sh("docker","cp",tmp+":/opt/open5gs/etc/open5gs/.",
               str(self.priv),timeout=35)
        finally:
            sh("docker","rm","-f",tmp,check=False)
        sh(sys.executable,str(pathlib.Path(__file__).with_name("core_config.py")),
           "--directory",str(self.priv),"--core-ip",IPS["core"],
           "--mongo-ip",IPS["mongo"])
        launcher=self.priv/"start-core.sh"
        launcher.write_text("#!/bin/bash\nset -euo pipefail\n"
          "mkdir -p /tmp/stage4k-log\n"
          "command -v ip >/dev/null || { echo iproute2-unavailable >&2; exit 1; }\n"
          "ip tuntap add dev ogstun mode tun\n"
          "ip addr add 10.45.0.1/16 dev ogstun\n"
          "ip link set ogstun up\n"
          "for svc in nrfd scpd ausfd udmd udrd nssfd pcfd bsfd smfd upfd amfd; do\n"
          "  case \"$svc\" in\n"
          "    nrfd) nf=nrf;; scpd) nf=scp;; ausfd) nf=ausf;; udmd) nf=udm;;\n"
          "    udrd) nf=udr;; nssfd) nf=nssf;; pcfd) nf=pcf;; bsfd) nf=bsf;;\n"
          "    smfd) nf=smf;; upfd) nf=upf;; amfd) nf=amf;; esac\n"
          "  command -v open5gs-$svc >/dev/null\n"
          "  open5gs-$svc -c /tmp/stage4k-conf/$nf.yaml > /tmp/stage4k-log/$nf.log 2>&1 &\n"
          "  sleep .5\n"
          "done\nwait -n\n")
        launcher.chmod(0o700)
        self.record("T2-5GC-config-generated","PASS")
        sh("docker","run","-d","--name",name("core"),
           "--network",NETWORK,"--ip",IPS["core"],
           "--cpus","1.5","--memory","2.5g",
           "--user","0:0",
           "--cap-add","NET_ADMIN","--device","/dev/net/tun",
           "-e","DB_URI=mongodb://"+IPS["mongo"]+"/open5gs",
           "-v",str(self.priv)+":/tmp/stage4k-conf:ro",
           "--entrypoint","/bin/bash",CORE_IMAGE,
           "/tmp/stage4k-conf/start-core.sh",timeout=90)
        time.sleep(6)
        self.demand("core","mongo")
        self.record("T3-real-5GC-started","PASS")
    def ran(self):
        render=pathlib.Path(self.opts.rendered)
        cu=configmap(render/"cu.yaml","ConfigMap","hcran-oai-cu-config")["data"]["gnb.conf"]
        du=configmap(render/"macro.yaml","ConfigMap","hcran-oai-macro-config")["data"]["gnb.conf"]
        cu=cu.replace("POD_IP",IPS["cu"]).replace("AMF_IP",IPS["core"])
        # Experimental Stage 4P ONLY: turn on E2 for a disposable CU
        # without changing canonical H-CRAN chart values or production pods.
        e2_ric=os.getenv("HCRAN_CI_E2_RIC","")
        if e2_ric:
            if e2_ric!="172.29.76.1":
                raise RuntimeError("E2 RIC address outside isolated CI bridge")
            if "e2_agent" in cu:
                raise RuntimeError("Conflicting duplicate E2 configuration")
            cu += ('\ne2_agent = {\n'
                   '  near_ric_ip_addr = "'+e2_ric+'";\n'
                   '  sm_dir = "/usr/local/lib/flexric/";\n'
                   '};\n')
        du=du.replace("POD_IP",IPS["macro"]).replace("CU_IP",IPS["cu"])
        if "gNB_DU_ID = 100;" not in du:raise RuntimeError("wrong H-CRAN macro DU identity")
        if IPS["core"] not in cu or IPS["cu"] not in du:raise RuntimeError("IP substitution failed")
        (self.priv/"cu.conf").write_text(cu)
        (self.priv/"macro.conf").write_text(du)
        for item,image,conf,extra in (
           ("cu",self.opts.cu_image,"cu.conf",[]),
           ("macro",self.opts.du_image,"macro.conf",["--rfsim"])):
            sh("docker","run","-d","--name",name(item),
               "--network",NETWORK,"--ip",IPS[item],"--cpus","1.5","--memory","3g",
               "-v",str(self.priv/conf)+":/opt/oai-gnb/etc/gnb.conf:ro",
               "--entrypoint","/opt/oai-gnb/bin/nr-softmodem",
               image,"-O","/opt/oai-gnb/etc/gnb.conf",*extra)
            self.record("T4-"+item+"-started","PASS")
        self.wait_log("T5-real-NGSetupResponse","cu",NGAP,105,("core","cu","macro"))
        self.wait_log("T6-F1SetupRequest","cu",F1REQ,90,("core","cu","macro"))
        self.wait_log("T7-F1SetupResponse","cu",F1RESP,60,("core","cu","macro"))
        self.capture("before-UE")
    def start_capture(self):
        self.pcap_stderr=(self.out/"tcpdump.stderr.txt").open("w")
        self.pcap_process=subprocess.Popen(
            ["sudo","tcpdump","-i","any","-nn","-U","-s","0",
             "-w",str(self.out/"gtpu.pcap"),"udp port 2152 or udp port 38472"],
            stdout=subprocess.DEVNULL,stderr=self.pcap_stderr)
        time.sleep(2)
        if self.pcap_process.poll() is not None:
            raise RuntimeError("GTP-U capture failed to start")
    def stop_capture(self):
        if self.pcap_process is not None and self.pcap_process.poll() is None:
            self.pcap_process.terminate()
            try:self.pcap_process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.pcap_process.kill()
        if self.pcap_stderr is not None:
            self.pcap_stderr.close()
            self.pcap_stderr=None
    def verify_gtpu(self):
        # The Open5GS local TUN gateway is an internal Docker test address,
        # never a WAN endpoint. A ping result is independent of GTP-U counts.
        reply=sh("docker","exec",name("ue"),"/bin/sh","-c",
                 "ping -I oaitun_ue1 -c 4 -W 2 10.45.0.1",
                 check=False,timeout=40)
        (self.out/"UE-internal-ping.txt").write_text(reply[-4000:])
        time.sleep(2)
        self.stop_capture()
        packet_evidence=analyze(self.out/"gtpu.pcap")
        (self.out/"GTPU-GPDU-evidence.json").write_text(
            json.dumps(packet_evidence,indent=2)+"\n")
        counts=packet_evidence["G_PDU_counts"]
        if not all(counts.values()):
            raise RuntimeError("Not all F1-U/NG-U directions carry G-PDU packets: "+str(counts))
        self.record("T12-F1U-NGU-real-GPDU-4directions","PASS",
                    packet_counts=counts,teids=packet_evidence["TEIDs"])
        if " 0% packet loss" not in reply and "0% packet loss" not in reply:
            raise RuntimeError("No confirmed loss-free UE-to-UPF-gateway ICMP echo replies")
        self.record("T13-real-UE-UPF-internal-ICMP","PASS",ping_target="10.45.0.1")
    def ue(self):
        if getattr(self.opts,"verify_gtpu",False):
            self.start_capture()
        ueconf=self.priv/"ue.conf"
        ueconf.write_text('uicc0 = {\n'
          '  imsi = "'+SIM["imsi"]+'";\n'
          '  key = "'+SIM["key"]+'";\n'
          '  opc = "'+SIM["opc"]+'";\n'
          '  pdu_sessions = ({ dnn = "internet"; nssai_sst = 1; nssai_sd = 0x010203; });\n};\n')
        ueconf.chmod(0o600)
        sh("docker","run","-d","--name",name("ue"),
           "--network",NETWORK,"--ip",IPS["ue"],
           "--cpus","2.0","--memory","3g",
           "--cap-add","NET_ADMIN","--device","/dev/net/tun",
           "-v",str(ueconf)+":/opt/oai-nr-ue/etc/ue.conf:ro",
           "--entrypoint","/opt/oai-nr-ue/bin/nr-uesoftmodem",
           self.opts.ue_image,"-O","/opt/oai-nr-ue/etc/ue.conf",
           "--rfsim","-r","106","--numerology","1","--band","78",
           "-C","3450720000","--ssb","516",
           "--rfsimulator.serveraddr",IPS["macro"])
        self.record("T8-real-synthetic-UE-started","PASS")
        self.wait_log("T9-real-NAS-RegistrationAccept","ue",REG,180,
                      ("core","cu","macro","ue"))
        self.wait_log("T10-real-PDU-SessionEstablishmentAccept","ue",PDU,120,
                      ("core","cu","macro","ue"))
        tun=sh("docker","exec",name("ue"),"/bin/sh","-c",
               "ip -o -4 addr show | grep -E 'oaitun|uesimtun' || true",
               check=False,timeout=20)
        if not tun.strip():raise RuntimeError("PDU Accept but no UE tunnel interface")
        (self.out/"UE-PDU-interface.txt").write_text(tun)
        self.record("T11-UE-assigned-IP","PASS")
        if getattr(self.opts,"verify_gtpu",False):
            self.verify_gtpu()
        self.capture("final")
        self.record("STAGE4L" if self.opts.verify_gtpu else "STAGE4K",
                    "PASS",
                    scope="one-DU real NAS/PDU and F1-U/NG-U G-PDU" if self.opts.verify_gtpu
                    else "one-DU real NAS/PDU; user-plane and second DU not tested")
    def cleanup(self):
        self.stop_capture()
        try:self.capture("cleanup")
        except Exception as e:self.record("capture-cleanup","WARN",error=str(e)[:500])
        for item in IPS:sh("docker","rm","-f",name(item),check=False)
        sh("docker","network","rm",NETWORK,check=False)
        shutil.rmtree(self.priv,ignore_errors=True)
    def run(self):
        try:
            self.bootstrap(); self.ran(); self.ue()
            return 0
        except Exception as exc:
            msg=str(exc)
            for v in SIM.values():msg=msg.replace(v,"[SYNTH]")
            self.record("STAGE4K","FAIL",error=msg[-3000:])
            tb=traceback.format_exc()
            for v in SIM.values():tb=tb.replace(v,"[SYNTH]")
            (self.out/"exception-redacted.txt").write_text(tb[-5000:])
            return 1
        finally:
            self.cleanup()

def main():
    parser=argparse.ArgumentParser()
    for arg in ("rendered","output","cu-image","du-image","ue-image"):
        parser.add_argument("--"+arg,required=True)
    parser.add_argument("--verify-gtpu",action="store_true")
    return Probe(parser.parse_args()).run()
if __name__=="__main__":
    sys.exit(main())
