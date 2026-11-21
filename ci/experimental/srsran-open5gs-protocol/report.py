#!/usr/bin/env python3
"""Independent PCAP-based NGAP/F1AP gate; never calls or simulates a protocol.
Counts only decoded signalling on the expected isolated Docker IP endpoints.
"""
import collections
import hashlib
import json
import pathlib
import subprocess
import sys

FIELDS = [
    "frame.time_epoch", "ip.src", "ip.dst", "sctp.srcport", "sctp.dstport",
    "ngap.NGSetupRequest_element", "ngap.NGSetupResponse_element",
    "ngap.NGSetupFailure_element", "f1ap.F1SetupRequest_element",
    "f1ap.F1SetupResponse_element", "f1ap.F1SetupFailure_element",
]
KINDS = [
    "NG_SETUP_REQUEST", "NG_SETUP_RESPONSE", "NG_SETUP_FAILURE",
    "F1_SETUP_REQUEST", "F1_SETUP_RESPONSE", "F1_SETUP_FAILURE",
]
REQUIRED = (
    "ngap.NGSetupRequest_element", "ngap.NGSetupResponse_element",
    "f1ap.F1SetupRequest_element", "f1ap.F1SetupResponse_element",
)

def require(x, reason):
    if not x:
        raise ValueError(reason)

def decode(pcap):
    size = pcap.stat().st_size
    require(24 < size <= 5 * 1024 * 1024, "MISSING_OR_OVERSIZED_PCAP")
    cmd = ["tshark", "-n", "-r", str(pcap), "-Y", "ngap || f1ap",
           "-T", "fields", "-E", "separator=/t", "-E", "occurrence=f"]
    for name in FIELDS:
        cmd.extend(["-e", name])
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    require(completed.returncode == 0, "DECODER_FIELD_OR_PCAP_ERROR")
    require(len(completed.stdout) <= 1_000_000, "DECODED_EVENT_BUDGET")
    rows = []
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        require(len(parts) == len(FIELDS), "DECODED_ROW_FORMAT")
        data = dict(zip(FIELDS, parts))
        flags = [name for name in FIELDS[5:] if data[name] != ""]
        require(len(flags) == 1, "UNEXPECTED_OR_AMBIGUOUS_NGAP_F1AP")
        rows.append((data, KINDS[FIELDS[5:].index(flags[0])]))
    require(rows, "NO_DECODED_APPLICATION_MESSAGES")
    return rows

def assess(plan, rows, pcap):
    n2_src = plan["cu_address"]
    amf = plan["amf_address"]
    du_ips = [x["address"] for x in plan["du_configs"]]
    seen = collections.defaultdict(list)
    for d, kind in rows:
        src, dst = d["ip.src"], d["ip.dst"]
        stamp = float(d["frame.time_epoch"])
        require(stamp > 0, "NONPOSITIVE_WIRE_TIMESTAMP")
        if kind.startswith("NG_"):
            require({src, dst} == {n2_src, amf} and
                    "38412" in {d["sctp.srcport"], d["sctp.dstport"]},
                    "UNEXPECTED_NGAP_PEER_OR_PORT")
            if kind == "NG_SETUP_REQUEST":
                require(src == n2_src, "WRONG_NG_REQUEST_DIRECTION")
            else:
                require(src == amf, "WRONG_NG_RESPONSE_DIRECTION")
            seen[(kind, "n2")].append(stamp)
        elif kind.startswith("F1_"):
            require((src == n2_src and dst in du_ips) or
                    (dst == n2_src and src in du_ips),
                    "UNEXPECTED_F1_PEER")
            require("38472" in {d["sctp.srcport"], d["sctp.dstport"]},
                    "UNEXPECTED_F1_PORT")
            du = src if src in du_ips else dst
            if kind == "F1_SETUP_REQUEST":
                require(src == du, "WRONG_F1_REQUEST_DIRECTION")
            else:
                require(src == n2_src, "WRONG_F1_RESPONSE_DIRECTION")
            seen[(kind, du)].append(stamp)
    require(not seen.get(("NG_SETUP_FAILURE", "n2")), "NG_SETUP_FAILURE_SEEN")
    require(seen.get(("NG_SETUP_REQUEST", "n2")), "NO_NG_SETUP_REQUEST")
    require(seen.get(("NG_SETUP_RESPONSE", "n2")), "NO_NG_SETUP_RESPONSE")
    req = min(seen[("NG_SETUP_REQUEST", "n2")])
    rsp = min(t for t in seen[("NG_SETUP_RESPONSE", "n2")] if t >= req) if any(
        t >= req for t in seen[("NG_SETUP_RESPONSE", "n2")]) else None
    require(rsp is not None, "UNORDERED_NG_SETUP")
    dus = []
    for du in du_ips:
        require(not seen.get(("F1_SETUP_FAILURE", du)), "F1_SETUP_FAILURE_SEEN:" + du)
        require(seen.get(("F1_SETUP_REQUEST", du)), "MISSING_F1_SETUP_REQUEST:" + du)
        require(seen.get(("F1_SETUP_RESPONSE", du)), "MISSING_F1_SETUP_RESPONSE:" + du)
        start = min(seen[("F1_SETUP_REQUEST", du)])
        end = min((t for t in seen[("F1_SETUP_RESPONSE", du)] if t >= start), default=None)
        require(end is not None, "UNORDERED_F1_SETUP:" + du)
        dus.append({"du_address": du, "request_observed": True,
                    "response_observed": True, "observed_setup_latency_ms": round((end-start)*1000,3)})
    result = {
        "schema_version": 1,
        "profile": plan["profile"],
        "classification": "PASS_CAPTURED_NGAP_NG_SETUP_AND_F1AP_F1_SETUP_ONLY",
        "protocol_evidence": "REAL_DISPOSABLE_SRSRAN_OPEN5GS_PCAP",
        "ngap_ts_38_413_ng_setup": {
            "result": "PASS_CAPTURED", "request": True, "response": True,
            "observed_setup_latency_ms": round((rsp-req)*1000, 3)},
        "f1ap_ts_38_473_f1_setup": {"result": "PASS_CAPTURED", "dus": dus},
        "rrc_ts_38_331": "NOT_TESTED_NO_UE",
        "ue_registration_ts_23_502": "NOT_TESTED_NO_UE",
        "pdu_session_ts_23_502": "NOT_TESTED_NO_UE",
        "ngap_pdu_session_resource_procedures": "NOT_TESTED_NO_UE",
        "ts_28_552_kpi_conformance": "NOT_VERIFIED_NO_UE_METRIC_SOURCES",
        "observed_packet_events": sum(len(x) for x in seen.values()),
        "pcap_sha256": hashlib.sha256(pcap.read_bytes()).hexdigest(),
        "no_oai": True, "hp_cluster_modified": False,
        "production_decision": "NO_GO",
        "notes": "Only decoded NG/F1 setup pairs from one disposable run; not 3GPP conformance certification."
    }
    return result

def main():
    require(len(sys.argv) == 4, "USAGE: report.py PLAN PCAP OUTPUT")
    plan = json.loads(pathlib.Path(sys.argv[1]).read_text())
    dest = pathlib.Path(sys.argv[3])
    require(not dest.exists(), "REPORT_ALREADY_EXISTS")
    data = assess(plan, decode(pathlib.Path(sys.argv[2])), pathlib.Path(sys.argv[2]))
    dest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(data["classification"])

if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, subprocess.TimeoutExpired, OSError) as exc:
        print("PROTOCOL_GATE_FAILED: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)
