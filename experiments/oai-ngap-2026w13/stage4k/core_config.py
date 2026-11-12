#!/usr/bin/env python3
"""Rewrite disposable Open5GS 2.7.6 config copies for H-CRAN OAI RFsim lab.

The source is extracted from the public Open5GS container, never from
production. All NFs run inside one isolated core container with local SBI.
Only NGAP and GTP-U need externally reachable Docker bridge addresses.
"""
import argparse
from pathlib import Path
import yaml

def must(x, reason):
    if not x:
        raise ValueError(reason)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--directory",required=True)
    p.add_argument("--core-ip",required=True)
    p.add_argument("--mongo-ip",required=True)
    a=p.parse_args()
    root=Path(a.directory)
    files=sorted(root.glob("*.yaml"))
    must(files,"No Open5GS YAML files extracted from container")
    required={"amf","nrf","smf","upf","ausf","udm","udr","pcf","nssf","scp"}
    available={f.stem for f in files}
    must(required.issubset(available),"Missing 5GC config(s): "+str(sorted(required-available)))

    # All processes share one Docker network namespace. Their SBI and
    # PFCP sockets MUST NOT all bind the public core bridge address.
    local_ips={
       "nrf":"127.0.0.10","scp":"127.0.0.11","amf":"127.0.0.5",
       "smf":"127.0.0.4","upf":"127.0.0.7","ausf":"127.0.0.12",
       "udm":"127.0.0.13","udr":"127.0.0.14","pcf":"127.0.0.15",
       "nssf":"127.0.0.16","bsf":"127.0.0.17",
    }
    def set_endpoint(block, ip):
        if isinstance(block,dict) and isinstance(block.get("server"),list):
            for item in block["server"]:
                if isinstance(item,dict):
                    item["address"]=ip
                    item.pop("dev",None)
    def map_hosts(value):
        if isinstance(value,str):
            for host,ip in local_ips.items():
                value=value.replace("http://"+host+":", "http://"+ip+":")
                if value==host:value=ip
            return value
        if isinstance(value,list):return [map_hosts(x) for x in value]
        if isinstance(value,dict):return {k:map_hosts(v) for k,v in value.items()}
        return value
    for f in files:
        obj=yaml.safe_load(f.read_text())
        must(isinstance(obj,dict),f"Not a YAML object: {f.name}")
        nf=f.stem
        if nf in local_ips:
            network=obj.get(nf,{})
            for interface in ("sbi","metrics","pfcp"):
                if interface in network:
                    set_endpoint(network[interface],local_ips[nf])
            obj=map_hosts(obj)
        if f.stem=="amf":
            amf=obj["amf"]
            must(amf.get("ngap",{}).get("server"),"AMF NGAP server missing")
            # Existing container has duplicate 38412 SCTP bind candidates.
            amf["ngap"]["server"]=[{"address":a.core_ip}]
            plmn={"mcc":"208","mnc":"93"}
            amf["guami"]=[{"plmn_id":plmn,
                           "amf_id":{"region":2,"set":1}}]
            amf["tai"]=[{"plmn_id":plmn,"tac":1}]
            amf["plmn_support"]=[{"plmn_id":plmn,
                                   "s_nssai":[{"sst":1,"sd":"ffffff"}]}]
            amf.setdefault("time",{}).setdefault("t3512",{})["value"]=540
            amf["amf_name"]="stage4k-hcran-ci"
        if f.stem=="smf":
            smf=obj["smf"]
            # FreeDiameter is legacy EPC policy. For this 5GC-only trial,
            # disable it instead of resolving a non-existent PCRF hostname.
            smf.pop("freeDiameter",None)
            # Legacy SMF GTP-U listener must not steal N3 port 2152
            # from the 5G UPF in this shared-network-namespace lab.
            set_endpoint(smf.get("gtpu"),local_ips["smf"])
            set_endpoint(smf.get("gtpc"),local_ips["smf"])
            session=smf.get("session",[])
            must(bool(session),"SMF session address pool missing")
            for item in session:
                if isinstance(item,dict):
                    item["dnn"]="internet"
                    if "name" in item:item["name"]="internet"
            # Preserve PFCP association addresses: both SMF and UPF are
            # local to the core container's loopback namespace.
        if f.stem=="upf":
            upf=obj["upf"]
            must(upf.get("gtpu",{}).get("server"),"UPF N3 GTP-U listener missing")
            # Existing image has multiple GTP-U listeners on same endpoint:
            # retain only N3 for one UPF, one DU trial.
            upf["gtpu"]["server"]=[{"address":a.core_ip}]
            set_endpoint(upf.get("pfcp"),local_ips["upf"])
        if f.stem=="smf":
            smf=obj["smf"]
            set_endpoint(smf.get("pfcp"),local_ips["smf"])
            pfcp=smf.get("pfcp",{})
            if isinstance(pfcp,dict):
                client=pfcp.get("client",{})
                if isinstance(client,dict) and isinstance(client.get("upf"),list):
                    for item in client["upf"]:
                        if isinstance(item,dict):
                            item["address"]=local_ips["upf"]
        if f.stem=="nrf":
            nrf=obj["nrf"]
            if "serving" in nrf:
                nrf["serving"]=[{"plmn_id":{"mcc":"208","mnc":"93"}}]

        # The one core container hosts all local loopback SBI and PFCP.
        # UDR/PCF still need Docker bridge access to disposable MongoDB.
        # SBI, PFCP and metrics endpoints must be unique. The AMF NGAP
        # listener alone is public for SCTP from the OAI CU.
        raw=yaml.safe_dump(obj,sort_keys=False)
        for old in ("mongodb://127.0.0.1", "mongodb://localhost",
                    "mongodb://mongo", "mongodb://mongodb"):
            raw=raw.replace(old, "mongodb://"+a.mongo_ip)
        f.write_text(raw)
    print("PASS: isolated Open5GS configs prepared; AMF NGAP and UPF N3 externally bound")
if __name__=="__main__":
    main()
