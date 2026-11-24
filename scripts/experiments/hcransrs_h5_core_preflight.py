#!/usr/bin/env python3
"""H-CRAN srsRAN H5: isolated 5GC authentication-backend prerequisite.

Disposable GitHub-hosted Docker network ONLY. Start real pinned Open5GS
NRF/AUSF/UDM/UDR and MongoDB, insert one synthetic test subscriber with
ephemeral randomly generated K/OPc, verify NRF service registration and
UDR authentication-subscription lookup without exporting credential bytes.

No UE/RAN is started here, so registration/authentication/RRC are NOT proven.
No user-provided subscriber, host K8s/OSM, or remote network used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import urlopen

NET="hcransrs-h5-core-only"
PREFIX="hcransrs-h5"
CORE="gradiant/open5gs:2.7.6"
MONGO="mongo:6.0"
IP={"nrf":"172.29.79.11","mongo":"172.29.79.12",
    "ausf":"172.29.79.13","udm":"172.29.79.14","udr":"172.29.79.15"}
IMSI="208930100001100"
SBI_ROLES=("nrf","ausf","udm","udr")
NOT_RUN=["OAI_UE_START","NAS_AUTHENTICATION_REQUEST","NAS_AUTHENTICATION_RESPONSE",
         "NAS_REGISTRATION_ACCEPT","NAS_REGISTRATION_COMPLETE","PDU_SESSION",
         "F1U_TRAFFIC","NGU_TRAFFIC","HANDOVER","FAULT_RECOVERY"]

class PreflightError(RuntimeError):
    pass

def command(*args,input_text=None,timeout=30,check=True):
    p=subprocess.run([str(x) for x in args],input=input_text,capture_output=True,
                     text=True,timeout=timeout)
    if check and p.returncode:
        # Do NOT echo command arguments or stderr: Mongo stdin could contain K/OPc.
        raise PreflightError(f"isolated command failed: {args[0]} exit={p.returncode}")
    return p.stdout

def check_isolated():
    if os.environ.get("GITHUB_ACTIONS")!="true" or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted":
        raise PreflightError("Not a disposable GitHub-hosted runner")
    if os.environ.get("HCRANSRS_ALLOW_H5_CORE")!="EXACTLY_ISOLATED_SYNTHETIC_NO_DEPLOY":
        raise PreflightError("Isolated synthetic-core permission marker missing")

def config(role):
    if role not in SBI_ROLES:
        raise PreflightError("Unexpected NF role")
    server=f"      - address: {IP[role]}\n        port: 7777\n"
    if role=="nrf":
        return ("logger:\n  level: info\nnrf:\n  serving:\n"
                "    - plmn_id:\n        mcc: '208'\n        mnc: '93'\n"
                "  sbi:\n    server:\n"+server)
    base=("logger:\n  level: info\n"
          + ("db_uri: mongodb://"+IP["mongo"]+"/open5gs\n" if role=="udr" else "")
          +role+":\n  sbi:\n    server:\n"+server
          +"    client:\n      nrf:\n        - uri: http://"+IP["nrf"]+":7777\n")
    return base

def subscriber():
    # Ephemeral credentials exist only in memory and process stdin.
    return {
        "imsi":IMSI,"schema_version":1,"access_restriction":0,
        "subscriber_status":0,"operator_determined_barring":0,
        "network_access_mode":0,"subscribed_rau_tau_timer":12,
        "security":{"k":secrets.token_hex(16),"amf":"8000",
                    "op":None,"opc":secrets.token_hex(16)},
        "ambr":{"uplink":{"value":1,"unit":3},"downlink":{"value":1,"unit":3}},
        "slice":[{"sst":1,"sd":"010203","default_indicator":True,
                  "session":[{"name":"internet","type":3,
                              "qos":{"index":9,"arp":{"priority_level":8,
                                    "pre_emption_capability":1,"pre_emption_vulnerability":1}},
                              "ambr":{"uplink":{"value":1,"unit":3},
                                      "downlink":{"value":1,"unit":3}},
                              "pcc_rule":[]}]}],
    }

def check_subscriber_doc(doc):
    if doc.get("imsi")!=IMSI or doc.get("schema_version")!=1:
        raise PreflightError("Invalid synthetic subscriber identity/schema")
    sec=doc.get("security") or {}
    if not all(isinstance(sec.get(k),str) and len(sec[k])==32 and
               all(c in "0123456789abcdef" for c in sec[k]) for k in ("k","opc")):
        raise PreflightError("Invalid ephemeral 128-bit synthetic authentication data")
    if doc.get("slice",[{}])[0].get("sd")!="010203":
        raise PreflightError("Synthetic slice mismatch")
    return True

def alive(name):
    return command("docker","inspect","--format","{{.State.Running}}",
                   f"{PREFIX}-{name}",timeout=8,check=False).strip()=="true"

def wait_socket(ip,port,deadline=45):
    end=time.monotonic()+deadline
    while time.monotonic()<end:
        try:
            with socket.create_connection((ip,port),timeout=1.5):
                return True
        except OSError:
            time.sleep(1)
    return False

def nrf_discovery(role):
    params=urlencode({"target-nf-type":role.upper(),"requester-nf-type":"AMF"})
    url=f"http://{IP['nrf']}:7777/nnrf-disc/v1/nf-instances?{params}"
    with urlopen(url,timeout=8) as response:
        if response.status!=200:
            raise PreflightError("NRF discovery request not accepted")
        raw=response.read(128*1024+1)
    if len(raw)>128*1024:
        raise PreflightError("Oversized NRF discovery response")
    data=json.loads(raw)
    return any(n.get("nfType")==role.upper() for n in data.get("nfInstances",[]))

def udr_test():
    # No UDR response payload is printed, uploaded or persisted.
    url=(f"http://{IP['udr']}:7777/nudr-dr/v1/subscription-data/imsi-{IMSI}"
         "/authentication-data/authentication-subscription")
    with urlopen(url,timeout=8) as response:
        if response.status!=200:
            raise PreflightError("UDR auth-subscription lookup rejected")
        body=response.read(64*1024+1)
    if len(body)>64*1024:
        raise PreflightError("UDR response too large")
    data=json.loads(body)
    # Validate only the presence of expected *nonsecret* field names.
    return data.get("authenticationMethod")=="5G_AKA" and (
        "encPermanentKey" in data or "permanentKey" in data
    )

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",required=True)
    parser.add_argument("--core-image",required=True)
    parser.add_argument("--mongo-image",required=True)
    args=parser.parse_args()
    check_isolated()
    if args.core_image!=CORE or args.mongo_image!=MONGO:
        raise PreflightError("Unreviewed image reference")
    root=Path(os.environ["RUNNER_TEMP"]).resolve()
    out=Path(args.out).resolve()
    if not out.is_relative_to(root) or out.is_symlink():
        raise PreflightError("Evidence location must be runner-temporary")
    out.mkdir(parents=True,exist_ok=True,mode=0o700)
    work=out/"private"
    work.mkdir(mode=0o700,exist_ok=False)
    started=[]
    network_created=False
    receipt={"schema":"hcransrs-h5-auth-backend-preflight-v1",
             "status":"BLOCKED","synthetic_subscriber":False,
             "backend_sbi_ready":{},"nrf_discovery":{},
             "udr_authentication_lookup":False,"not_run":NOT_RUN,
             "hp_cluster_touched":False,"production_decision":"NO_GO"}
    try:
        command("docker","network","create","--driver","bridge","--internal",
                "--subnet","172.29.79.0/24",NET,timeout=25)
        network_created=True
        command("docker","run","-d","--pull=never","--name",f"{PREFIX}-mongo",
                "--network",NET,"--ip",IP["mongo"],"--memory","2g",
                MONGO,"mongod","--bind_ip_all","--quiet",timeout=35)
        started.append("mongo")
        ready=False
        for _ in range(40):
            if not alive("mongo"):
                raise PreflightError("Synthetic Mongo container exited")
            try:
                x=command("docker","exec",f"{PREFIX}-mongo","mongosh","--norc",
                    "--quiet","--eval","db.adminCommand({ping:1}).ok","admin",timeout=9)
                if x.strip().endswith("1"):
                    ready=True
                    break
            except (PreflightError,subprocess.TimeoutExpired):
                pass
            time.sleep(1)
        if not ready:
            raise PreflightError("Disposable synthetic MongoDB unavailable")
        doc=subscriber()
        check_subscriber_doc(doc)
        js=("const d="+json.dumps(doc,separators=(",",":"))+"; "
            "if(db.subscribers.countDocuments({imsi:d.imsi})!==0) throw Error('duplicate');"
            "const r=db.subscribers.insertOne(d);"
            "if(!r.acknowledged) throw Error('insert');"
            "print('INSERTED_ONE_SYNTHETIC_SUBSCRIBER');")
        output=command("docker","exec","-i",f"{PREFIX}-mongo",
                "mongosh","--norc","--quiet","open5gs",input_text=js,timeout=20)
        if "INSERTED_ONE_SYNTHETIC_SUBSCRIBER" not in output:
            raise PreflightError("Synthetic subscriber insertion not verified")
        receipt["synthetic_subscriber"]=True
        del doc,js,output
        for role in SBI_ROLES:
            cfg=work/f"{role}.yaml"
            cfg.write_text(config(role))
            cfg.chmod(0o600)
            command("docker","run","-d","--pull=never",
                    "--name",f"{PREFIX}-{role}","--network",NET,
                    "--ip",IP[role],"--memory","1g",
                    "-v",str(cfg)+":/tmp/h5-config.yaml:ro",
                    "--entrypoint","/bin/sh",CORE,
                    "-ec",f"exec open5gs-{role}d -c /tmp/h5-config.yaml",timeout=30)
            started.append(role)
            if not wait_socket(IP[role],7777,deadline=45):
                raise PreflightError(f"{role} SBI not reachable in isolated network")
            receipt["backend_sbi_ready"][role]=True
        for role in ("ausf","udm","udr"):
            verified=False
            for _ in range(15):
                try:
                    verified=nrf_discovery(role)
                    if verified:break
                except (OSError,ValueError,KeyError):
                    pass
                time.sleep(2)
            receipt["nrf_discovery"][role]=verified
        if not all(receipt["nrf_discovery"].values()):
            raise PreflightError("NRF has not registered all 3 authentication data functions")
        receipt["udr_authentication_lookup"]=udr_test()
        if not receipt["udr_authentication_lookup"]:
            raise PreflightError("UDR authentication-subscription data not verified")
        if not all(alive(role) for role in ("mongo",)+SBI_ROLES):
            raise PreflightError("Some core NFs exited")
        receipt["status"]="ISOLATED_5GC_AUTH_BACKEND_AND_SYNTHETIC_UDR_LOOKUP_PASS_NO_UE"
    except Exception as ex:
        receipt["error_type"]=type(ex).__name__
        receipt["status"]="BLOCKED_H5_AUTH_BACKEND"
        print("H5 diagnostic class: "+type(ex).__name__,flush=True)
    finally:
        for role in reversed(started):
            command("docker","rm","-f",f"{PREFIX}-{role}",timeout=20,check=False)
        if network_created:
            command("docker","network","rm",NET,timeout=20,check=False)
        (out/"receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
        print(json.dumps({k:v for k,v in receipt.items() if k not in
                          ("not_run",)},sort_keys=True),flush=True)
    return 0 if receipt["status"]=="ISOLATED_5GC_AUTH_BACKEND_AND_SYNTHETIC_UDR_LOOKUP_PASS_NO_UE" else 1

if __name__=="__main__":
    raise SystemExit(main())
