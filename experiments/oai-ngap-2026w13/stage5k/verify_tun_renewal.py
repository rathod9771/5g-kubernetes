#!/usr/bin/env python3
"""Independent fail-closed Stage5K PDU TUN renewal acceptance.

A successful source build or fresh Registration/PDU alone is NOT enough.
Require 2 unchanged live UE processes, old->new 5GC IPv4 reassignment,
real UE-side TUN reconfiguration markers and restored bidirectional F1U/NGU.
No production authorization is granted by this experiment.
"""
import argparse, ipaddress, json, pathlib, sys
def check(path):
    path=pathlib.Path(path)
    failures=[]
    try:
        s=json.loads((path/"stage5e-recovery-status.json").read_text())
        m=json.loads((path/"stage5i-postfault-markers.json").read_text())
        n=json.loads((path/"stage5i-runtime.json").read_text())
        events=json.loads((path/"results.json").read_text())
    except (OSError,ValueError) as exc:
        return False,["Missing authentic Stage5I recovery proof: "+str(exc)]
    if not isinstance(s,dict) or not isinstance(m,dict) or not isinstance(n,dict) or not isinstance(events,list):
        return False,["Invalid evidence type"]
    for key in ("amf_only_fault","new_ngap_connection_observed","both_ue_icmp_recovered"):
        if s.get(key) is not True: failures.append("Stage5E missing "+key)
    if s.get("ues_were_restarted") is not False:
        failures.append("UE process identity/restart proof invalid")
    if n.get("two_ue_processes_unchanged") is not True or n.get("production_authorized") is not False:
        failures.append("Stage5I unchanged UE or NO_GO evidence missing")
    for role in ("ue","ue-small"):
        old=s.get("before_pdu_ips",{}).get(role)
        new=s.get("after_pdu_ips",{}).get(role)
        try:
            old_ip=ipaddress.IPv4Address(old)
            new_ip=ipaddress.IPv4Address(new)
            if old_ip==new_ip or not str(new_ip).startswith("10.45."):
                failures.append(role+" did not prove new synthetic 5GC IPv4 assignment")
        except (ipaddress.AddressValueError,TypeError):
            failures.append(role+" invalid old/new PDU interface addresses")
        detail=m.get("ue",{}).get(role,{})
        count=next((x.get("count") for x in detail.get("marker_counts",[])
                    if x.get("marker")=="Stage5K: refreshed existing PDU TUN"),0)
        if type(count) is not int or count<1:
            failures.append(role+" missing source-level TUN refresh marker")
        if detail.get("chain_missing_from"):
            failures.append(role+" incomplete cause9/NAS/PDU chain")
    if len(set(s.get("after_pdu_ips",{}).values()))!=2:
        failures.append("Reallocated PDU IPv4 addresses not distinct")
    if not any(e.get("stage")=="STAGE5I" and e.get("status")=="PASS" for e in events if isinstance(e,dict)):
        failures.append("Stage5I real two-UE recovery not passed")
    pcap=path/"gtpu.pcap"
    if not pcap.is_file() or pcap.stat().st_size<150:
        failures.append("Missing real postfault GTP-U wire capture")
    return not failures,failures

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("evidence",type=pathlib.Path)
    args=ap.parse_args()
    ok,issues=check(args.evidence)
    print(json.dumps({"stage":"STAGE5K-TUN-RENEWAL","status":"PASS" if ok else "FAIL",
                      "reasons":issues,"production_decision":"NO_GO"},indent=2))
    sys.exit(0 if ok else 1)
