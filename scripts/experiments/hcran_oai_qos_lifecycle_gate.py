#!/usr/bin/env python3
"""OAI H-CRAN QoS and session-lifecycle evidence contract; OFFLINE verifier.

This checker does NOT generate live packets and cannot certify a runtime by
itself. A source-pinned independent capture adapter must produce a receipt
with decoded NAS/NGAP/F1AP/RRC and GTP-U observations. No subscriber secrets.
"""
import argparse
import json
from pathlib import Path

class EvidenceError(ValueError):
    pass

REQUIRED_PROCEDURES = (
    "nas_registration_accept", "nas_security_mode_complete",
    "rrc_security_mode_complete", "pdu_session_establishment_accept",
    "pdu_session_modification_command", "pdu_session_modification_complete",
    "pdu_session_release_command", "pdu_session_release_complete",
    "ngap_pdu_session_resource_modify", "ngap_pdu_session_resource_release",
    "f1ap_ue_context_modification", "f1ap_ue_context_release",
)
MAX_RECEIPT = 128 * 1024
EXPECTED_CELLS = {"macro":100, "small":101}

def verify(receipt):
    if not isinstance(receipt,dict) or receipt.get("schema")!="hcran-oai-qos-lifecycle-wire-v1":
        raise EvidenceError("Unrecognized evidence schema")
    source=receipt.get("source") or {}
    if (source.get("architecture")!="oai-hcran" or
        source.get("experiment")!="ISOLATED_GITHUB_RUNTIME" or
        source.get("independent_capture") is not True or
        not isinstance(source.get("run_id"),int) or source["run_id"]<=0 or
        not isinstance(source.get("pcap_sha256"),str) or len(source["pcap_sha256"])!=64 or
        any(c not in "0123456789abcdef" for c in source["pcap_sha256"])):
        raise EvidenceError("Missing genuine bounded runtime and wire provenance")
    if receipt.get("synthetic_fixture") is not False:
        raise EvidenceError("Synthetic fixture cannot qualify production protocol evidence")
    if receipt.get("negative_controls_passed") is not True:
        raise EvidenceError("Independent negative cases absent")
    ues=receipt.get("ues")
    if not isinstance(ues,list) or len(ues)!=2:
        raise EvidenceError("Exactly one independently observed UE per DU required")
    seen=set()
    for ue in ues:
        if not isinstance(ue,dict):raise EvidenceError("Malformed UE evidence")
        cell=ue.get("cell"); du=ue.get("du_id")
        if cell not in EXPECTED_CELLS or EXPECTED_CELLS[cell]!=du or cell in seen:
            raise EvidenceError("Duplicate or incorrect independent DU")
        seen.add(cell)
        before=ue.get("pdu_ipv4_before"); after=ue.get("pdu_ipv4_after")
        # Limit evidence to private PDU addresses; no subscriber ID is needed.
        import ipaddress
        try:
            a,b=ipaddress.ip_address(before),ipaddress.ip_address(after)
        except (ValueError,TypeError):
            raise EvidenceError("PDU address evidence invalid") from None
        if a.version!=4 or b.version!=4 or not a.is_private or not b.is_private:
            raise EvidenceError("PDU address must be private IPv4")
        events=ue.get("events")
        if not isinstance(events,dict) or set(events)!=set(REQUIRED_PROCEDURES):
            raise EvidenceError("Missing protocol lifecycle evidence classes")
        seq=[]
        for key in REQUIRED_PROCEDURES:
            event=events[key]
            if not isinstance(event,dict) or not isinstance(event.get("frame"),int) or type(event["frame"]) is bool or event["frame"]<=0:
                raise EvidenceError("Invalid independently decoded protocol event")
            if event.get("decoded") is not True:
                raise EvidenceError("Keyword-only evidence not accepted")
            seq.append(event["frame"])
        if seq!=sorted(set(seq)):
            raise EvidenceError("Protocol procedure order invalid or reused frame")
        qos=ue.get("qos")
        if not isinstance(qos,dict) or type(qos.get("five_qi")) is not int or not 1<=qos["five_qi"]<=255 or type(qos.get("qfi")) is not int or not 1<=qos["qfi"]<=63:
            raise EvidenceError("Missing valid 5QI/QFI mapping")
        if type(qos.get("drb_id")) is not int or not 1<=qos["drb_id"]<=32 or qos.get("mapping_decoded") is not True:
            raise EvidenceError("No observed QFI-to-DRB mapping")
        for leg in ("f1u_ul","f1u_dl","ngu_ul","ngu_dl"):
            e=ue.get("traffic",{}).get(leg)
            if not isinstance(e,dict) or type(e.get("packets")) is not int or e["packets"]<1 or type(e.get("teid")) is not int or e["teid"]<=0:
                raise EvidenceError("No binary-decoded bidirectional GTP-U or TEID for "+leg)
        if ue.get("traffic_after_release") not in (0,False):
            raise EvidenceError("User-plane traffic after verified release")
    return {"schema":"hcran-oai-qos-lifecycle-acceptance-v1","status":"PASS",
            "runtime_run_id":source["run_id"],"du_ids":[100,101],
            "scope":"independently observed two-UE QoS/session lifecycle, not general conformance",
            "production_decision":"NO_GO"}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("receipt")
    args=parser.parse_args()
    p=Path(args.receipt)
    if not p.is_file() or p.is_symlink() or p.stat().st_size>MAX_RECEIPT:raise SystemExit("NO_GO: invalid evidence file")
    try:
        result=verify(json.loads(p.read_text()))
    except (EvidenceError,json.JSONDecodeError) as exc:
        raise SystemExit("NO_GO: "+str(exc))
    print(json.dumps(result,sort_keys=True))

if __name__=="__main__":main()
