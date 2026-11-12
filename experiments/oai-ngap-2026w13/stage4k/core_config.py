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

    for f in files:
        obj=yaml.safe_load(f.read_text())
        must(isinstance(obj,dict),f"Not a YAML object: {f.name}")
        if f.stem=="amf":
            amf=obj["amf"]
            must(amf.get("ngap",{}).get("server"),"AMF NGAP server missing")
            amf["ngap"]["server"][0]["address"]=a.core_ip
            plmn={"mcc":"208","mnc":"93"}
            amf["guami"]=[{"plmn_id":plmn,
                           "amf_id":{"region":2,"set":1}}]
            amf["tai"]=[{"plmn_id":plmn,"tac":1}]
            amf["plmn_support"]=[{"plmn_id":plmn,
                                   "s_nssai":[{"sst":1,"sd":"010203"}]}]
            amf.setdefault("time",{}).setdefault("t3512",{})["value"]=540
            amf["amf_name"]="stage4k-hcran-ci"
        if f.stem=="smf":
            smf=obj["smf"]
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
            upf["gtpu"]["server"][0]["address"]=a.core_ip
        if f.stem=="nrf":
            nrf=obj["nrf"]
            if "serving" in nrf:
                nrf["serving"]=[{"plmn_id":{"mcc":"208","mnc":"93"}}]

        # The one core container hosts all local loopback SBI and PFCP.
        # UDR/PCF still need Docker bridge access to disposable MongoDB.
        raw=yaml.safe_dump(obj,sort_keys=False)
        for old in ("mongodb://127.0.0.1", "mongodb://localhost",
                    "mongodb://mongo", "mongodb://mongodb"):
            raw=raw.replace(old, "mongodb://"+a.mongo_ip)
        f.write_text(raw)
    print("PASS: isolated Open5GS configs prepared; AMF NGAP and UPF N3 externally bound")
if __name__=="__main__":
    main()
