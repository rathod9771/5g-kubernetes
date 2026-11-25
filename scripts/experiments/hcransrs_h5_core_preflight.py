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

def h2_json_get(url):
    """Open5GS SBI requires cleartext HTTP/2 prior knowledge, not urllib HTTP/1.1.

    Read-only call; response contents may include subscriber authentication
    material and MUST never be printed, hashed into logs, or retained.
    """
    allowed_hosts=tuple(f"http://{IP[r]}:7777/" for r in ("nrf","udr"))
    if not url.startswith(allowed_hosts) or "@" in url:
        raise PreflightError("SBI read-only destination not allowlisted")
    p=subprocess.run(
        ["curl","--http2-prior-knowledge","--silent","--show-error",
         "--fail","--max-time","8","--max-filesize","131072",
         "--header","accept: application/json",url],
        text=True,capture_output=True,timeout=12)
    if p.returncode or len(p.stdout)>131072:
        # Do not display HTTP body or error; potentially sensitive.
        raise PreflightError("Isolated HTTP/2 SBI request failed")
    return json.loads(p.stdout)


def nrf_discovery(role):
    if role not in ("ausf","udm","udr"):
        raise PreflightError("Unreviewed target NF")
    # Use the real 5GC consumer relationship: UDM discovers UDR, not AMF.
    requester={"ausf":"AMF","udm":"AUSF","udr":"UDM"}[role]
    params=urlencode({"target-nf-type":role.upper(),"requester-nf-type":requester})
    url=f"http://{IP['nrf']}:7777/nnrf-disc/v1/nf-instances?{params}"
    data=h2_json_get(url)
    if not isinstance(data,dict):
        raise PreflightError("Malformed NRF response")
    return any(n.get("nfType")==role.upper() for n in data.get("nfInstances",[])
               if isinstance(n,dict))



def nrf_udm_service_for_amf(service):
    """Check the exact NRF UDM SBI service needed after NAS SMC.

    Existing H5 checks only that a UDM *NF instance* registers. That is
    insufficient to prove that AMF can discover its nudm-uecm and nudm-sdm
    services. This is a read-only HTTP/2 NRF metadata request; no UE secret.
    """
    if service not in ("nudm-uecm", "nudm-sdm"):
        raise PreflightError("Unreviewed UDM service query")
    params=urlencode({"target-nf-type":"UDM","requester-nf-type":"AMF",
                      "service-names":service})
    data=h2_json_get(f"http://{IP['nrf']}:7777/nnrf-disc/v1/nf-instances?{params}")
    if not isinstance(data,dict):
        raise PreflightError("Malformed UDM service discovery response")
    for nf in data.get("nfInstances", []):
        if not isinstance(nf,dict) or nf.get("nfType")!="UDM":
            continue
        services=nf.get("nfServices", [])
        if not isinstance(services,list):
            services=list(nf.get("nfServiceList",{}).values())
        if any(isinstance(v,dict) and v.get("serviceName")==service
               and v.get("nfServiceStatus","REGISTERED")=="REGISTERED"
               for v in services):
            return True
    return False

def udr_test():
    url=(f"http://{IP['udr']}:7777/nudr-dr/v1/subscription-data/imsi-{IMSI}"
         "/authentication-data/authentication-subscription")
    data=h2_json_get(url)
    if not isinstance(data,dict):
        raise PreflightError("Malformed UDR response")
    # Presence/type only; no credential material in public receipt.
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
    stage="CREATING_ISOLATED_NETWORK"
    receipt={"schema":"hcransrs-h5-auth-backend-preflight-v1",
             "status":"BLOCKED","synthetic_subscriber":False,
             "backend_sbi_ready":{},"nrf_discovery":{},
             "udr_authentication_lookup":False,"not_run":NOT_RUN,
             "hp_cluster_touched":False,"production_decision":"NO_GO"}
    try:
        command("docker","network","create","--driver","bridge","--internal",
                "--subnet","172.29.79.0/24",NET,timeout=25)
        network_created=True
        stage="STARTING_SYNTHETIC_MONGO"
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
        stage="INSERTING_SYNTHETIC_SUBSCRIBER"
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
            stage="STARTING_SBI_"+role.upper()
            cfg=work/f"{role}.yaml"
            cfg.write_text(config(role))
            # Only nonsecret addresses and routes are in this file. Container\n            # Open5GS may run non-root, so a runner-owned 0600 mount is unreadable.\n            cfg.chmod(0o644)
            extras=(["-e","DB_URI=mongodb://"+IP["mongo"]+"/open5gs"]
                    if role=="udr" else [])
            command("docker","run","-d","--pull=never",
                    "--name",f"{PREFIX}-{role}","--network",NET,
                    "--ip",IP[role],"--memory","1g",
                    *extras,
                    "-v",str(cfg)+":/tmp/h5-config.yaml:ro",
                    "--entrypoint","/bin/sh",CORE,
                    "-ec",f"exec open5gs-{role}d -c /tmp/h5-config.yaml",timeout=30)
            started.append(role)
            if not wait_socket(IP[role],7777,deadline=45):
                raise PreflightError(f"{role} SBI not reachable in isolated network")
            receipt["backend_sbi_ready"][role]=True
        stage="DISCOVERING_AUTH_NFS_AT_NRF"
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
        stage="VERIFYING_SYNTHETIC_UDR_AUTH_RECORD"
        receipt["udr_authentication_lookup"]=udr_test()
        if not receipt["udr_authentication_lookup"]:
            raise PreflightError("UDR authentication-subscription data not verified")
        if not all(alive(role) for role in ("mongo",)+SBI_ROLES):
            raise PreflightError("Some core NFs exited")
        receipt["status"]="ISOLATED_5GC_AUTH_BACKEND_AND_SYNTHETIC_UDR_LOOKUP_PASS_NO_UE"
    except Exception as ex:
        receipt["failed_stage"]=stage
        receipt["error_type"]=type(ex).__name__
        receipt["status"]="BLOCKED_H5_AUTH_BACKEND"
        print("H5 failed stage: "+stage+"; diagnostic class: "+type(ex).__name__,flush=True)
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
