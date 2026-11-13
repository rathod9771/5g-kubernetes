#!/usr/bin/env python3
"""Fail-closed Stage4Z F1 mobility evidence contract. Read-only, pure JSON.

Validated evidence MUST come from live runtime harness, not synthetic fixtures.
Acceptance here does not independently decode raw packet captures or certify
3GPP conformance; production remains NO_GO even with a valid report.
"""
import ipaddress
import json
import pathlib
import sys

DIRECTIONS=(("macro","small",101),("small","macro",102))
PACKET_KEYS=("f1u_ul","f1u_dl","ngu_ul","ngu_dl")


def check(obj):
    """Return (qualified, reasons). False positive is unacceptable."""
    issues=[]
    if not isinstance(obj,dict):
        return False,["report is not an object"]
    if obj.get("schema")!="hcran-stage4z-real-f1-handover-v1":
        issues.append("unsupported schema")
    if obj.get("source")!="real-isolated-docker-rfsim-pcap":
        issues.append("untrusted evidence source")
    if obj.get("rc_ric_required") is not False:
        issues.append("E2/RIC must remain disabled")
    if obj.get("production_authorized") is not False or obj.get("production_decision")!="NO_GO":
        issues.append("production authorization falsely asserted")
    ue_ip=obj.get("initial_pdu_ip")
    try:
        if not isinstance(ue_ip,str) or ipaddress.ip_address(ue_ip) not in ipaddress.ip_network("10.45.0.0/16"):
            raise ValueError()
    except ValueError:
        issues.append("missing, unqualified or incompatible UE PDU IP")
    baseline=obj.get("baseline",{})
    if not isinstance(baseline,dict) or baseline.get("serving_du")!="macro":
        issues.append("missing macro serving-cell baseline")
    elif not valid_count(baseline.get("packet_counts")):
        issues.append("missing real baseline F1-U and NG-U packet counts")
    transitions=obj.get("transitions")
    if not isinstance(transitions,list) or len(transitions)!=2:
        issues.append("requires exactly two opposite real F1 handovers")
    else:
        for index,(src,dst,pci) in enumerate(DIRECTIONS):
            hop=transitions[index]
            if not isinstance(hop,dict):
                issues.append("handover event not an object")
                continue
            if hop.get("from")!=src or hop.get("to")!=dst or hop.get("source_pci")!=pci:
                issues.append("wrong source, target or PCI in hop "+str(index))
            if hop.get("pre_pdu_ip")!=ue_ip or hop.get("post_pdu_ip")!=ue_ip:
                issues.append("UE PDU IP was not demonstrably preserved")
            if hop.get("f1ap_context_update_confirmed") is not True:
                issues.append("missing observed F1AP target-context update")
            if hop.get("source_context_release_confirmed") is not True:
                issues.append("missing observed old DU UE context release")
            if hop.get("rrc_reconfiguration_confirmed") is not True:
                issues.append("missing UE mobility RRC reconfiguration completion evidence")
            if hop.get("icmp_sent")!=6 or hop.get("icmp_received")!=6:
                issues.append("UE PDU ICMP traffic incomplete")
            if not valid_count(hop.get("packet_counts")):
                issues.append("missing real target DU F1-U or NG-U packet counts")
            if not isinstance(hop.get("post_ho_pcap_sha256"),str) or len(hop["post_ho_pcap_sha256"])!=64:
                issues.append("missing packet capture checksum")
    return not issues,issues


def valid_count(val):
    return isinstance(val,dict) and all(
        isinstance(val.get(k),int) and not isinstance(val.get(k),bool)
        and val[k]>0 for k in PACKET_KEYS)


def main():
    if len(sys.argv)!=2:
        raise SystemExit("Usage: evidence_contract.py <real-stage4z.json>")
    p=pathlib.Path(sys.argv[1])
    valid,issues=check(json.loads(p.read_text()))
    print(json.dumps({"structural_check":"PASS" if valid else "BLOCKED",
                      "issues":issues,"production_decision":"NO_GO"},indent=2))
    return 0 if valid else 2


if __name__=="__main__":
    raise SystemExit(main())
