#!/usr/bin/env python3
"""H7 research NAS post-authentication wire classification; fail-closed.

Runs only on disposable GitHub-hosted runner after the successful H6
synthetic-subscriber experiment. Dissects NGAP NAS-PDU in-memory, emits
only known 5GMM message class / SCTP frame numbers / NAS security header
category, never raw PDU, subscriber identity, keys, or decrypted guesses.

An outer ciphered NAS header DOES NOT prove the inner message type.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# Single source of truth for AMF/CU IPs: H1's actual isolated bridge.
# H7 previously queried 172.29.78.11 (NRF!), silently discarding all NAS.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hcransrs_ngf1 as h1

H6_PASS = "REAL_NAS_AUTH_REQUEST_AND_RESPONSE_WIRE_CANDIDATE_ONLY"
AMF = h1.IP["amf"]
CU = h1.IP["cu"]
KNOWN = {
    0x41: "REGISTRATION_REQUEST",
    0x42: "REGISTRATION_ACCEPT",
    0x43: "REGISTRATION_COMPLETE",
    0x44: "REGISTRATION_REJECT",
    0x56: "AUTHENTICATION_REQUEST",
    0x57: "AUTHENTICATION_RESPONSE",
    0x58: "AUTHENTICATION_REJECT",
    0x59: "AUTHENTICATION_FAILURE",
    0x5d: "SECURITY_MODE_COMMAND",
    0x5e: "SECURITY_MODE_COMPLETE",
    0x5f: "SECURITY_MODE_REJECT",
}
EXPECTED_DIRECTION = {
    "REGISTRATION_ACCEPT": "downlink",
    "REGISTRATION_COMPLETE": "uplink",
    "REGISTRATION_REJECT": "downlink",
    "AUTHENTICATION_REQUEST": "downlink",
    "AUTHENTICATION_RESPONSE": "uplink",
    "AUTHENTICATION_REJECT": "downlink",
    "AUTHENTICATION_FAILURE": "uplink",
    "SECURITY_MODE_COMMAND": "downlink",
    "SECURITY_MODE_COMPLETE": "uplink",
    "SECURITY_MODE_REJECT": "uplink",
}

# Labels cross-checked against pinned Open5GS v2.7.6
# lib/nas/5gs/types.h (3GPP TS 24.501 5GMM cause values).
# Numeric values are nonsecret; no NAS PDU body is ever retained.
REJECTION_CAUSES = {
    3: "ILLEGAL_UE",
    5: "PEI_NOT_ACCEPTED",
    6: "ILLEGAL_ME",
    7: "5GS_SERVICES_NOT_ALLOWED",
    9: "UE_IDENTITY_NOT_DERIVABLE",
    11: "PLMN_NOT_ALLOWED",
    12: "TRACKING_AREA_NOT_ALLOWED",
    13: "ROAMING_NOT_ALLOWED_IN_TA",
    15: "NO_SUITABLE_CELLS_IN_TA",
    20: "MAC_FAILURE",
    21: "SYNCHRONIZATION_FAILURE",
    22: "CONGESTION",
    23: "UE_SECURITY_CAPABILITY_MISMATCH",
    24: "SECURITY_MODE_REJECTED_UNSPECIFIED",
    26: "NON_5G_AUTHENTICATION_UNACCEPTABLE",
    27: "N1_MODE_NOT_ALLOWED",
    28: "RESTRICTED_SERVICE_AREA",
    31: "REDIRECTION_TO_EPC",
    62: "NO_NETWORK_SLICES_AVAILABLE",
    71: "NGKSI_ALREADY_IN_USE",
    73: "SERVING_NETWORK_NOT_AUTHORIZED",
    90: "PAYLOAD_WAS_NOT_FORWARDED",
    95: "SEMANTICALLY_INCORRECT_MESSAGE",
    96: "INVALID_MANDATORY_INFORMATION",
    97: "MESSAGE_TYPE_NON_EXISTENT",
    98: "MESSAGE_TYPE_NOT_COMPATIBLE_WITH_PROTOCOL_STATE",
    99: "INFORMATION_ELEMENT_NON_EXISTENT",
    100: "CONDITIONAL_IE_ERROR",
    101: "MESSAGE_NOT_COMPATIBLE_WITH_PROTOCOL_STATE",
    111: "PROTOCOL_ERROR_UNSPECIFIED",
}
MAX_FRAMES = 128
MAX_PCAP = 5 * 1024 * 1024

class H7EvidenceError(RuntimeError):
    pass

def classify_nas_pdu(hex_value, direction):
    """Return coarse class without recording PDU octets or ciphered plaintext.

    A security-protected outer header consists of EPD, security type,
    4-byte MAC and sequence number; inner plain NAS begins at byte seven
    *only* for integrity-only types 1 and 3. Types 2/4 are ciphered.
    """
    if direction not in ("uplink", "downlink"):
        raise H7EvidenceError("Unexpected NGAP direction")
    payload = hex_value.replace(":", "").lower()
    if (not re.fullmatch("[0-9a-f]+", payload) or len(payload) % 2
            or len(payload) < 6 or len(payload) > 8192):
        return {"class": "INVALID_OR_UNDECODABLE_NAS", "security_header_type": None}
    if payload[:2] != "7e":
        return {"class": "UNRELATED_OR_UNKNOWN_NAS", "security_header_type": None}
    security_header = int(payload[2:4], 16) & 0x0f
    if security_header == 0:
        message = int(payload[4:6], 16)
        cause_offset = 6
    elif security_header in (1, 3):
        if len(payload) < 20 or payload[14:18] != "7e00":
            message = None
        else:
            message = int(payload[18:20], 16)
        cause_offset = 20
    elif security_header in (2, 4):
        return {"class": "PROTECTED_CIPHERED_NAS_TYPE_UNAVAILABLE",
                "security_header_type": security_header}
    else:
        return {"class": "UNKNOWN_SECURITY_HEADER",
                "security_header_type": security_header}
    name = KNOWN.get(message, "OTHER_VISIBLE_5GMM")
    if name in EXPECTED_DIRECTION and EXPECTED_DIRECTION[name] != direction:
        name = "MESSAGE_DIRECTION_MISMATCH"
    # SMC must be protected with the *new* security context; a plaintext
    # 5d in a capture is not evidence of a valid security transition.
    if name == "SECURITY_MODE_COMMAND" and security_header != 3:
        name = "UNQUALIFIED_SECURITY_MODE_COMMAND"
    result = {"class": name, "security_header_type": security_header}
    # The first mandatory octet after a *visible unencrypted*
    # Registration Reject message-type is the 5GMM cause. For ciphertext,
    # never guess the NAS class or parse an inner cause.
    if name == "REGISTRATION_REJECT":
        cause = (int(payload[cause_offset:cause_offset + 2], 16)
                 if len(payload) >= cause_offset + 2 else None)
        result["registration_reject_cause_code"] = cause
        result["registration_reject_cause_label"] = (
            REJECTION_CAUSES.get(cause, "UNKNOWN_OR_UNSUPPORTED_5GMM_CAUSE")
            if cause is not None else "TRUNCATED_MANDATORY_CAUSE")
    return result


def decode_wire(pcap, exec_fn=None):
    if not pcap.is_file() or pcap.is_symlink() or not 128 <= pcap.stat().st_size <= MAX_PCAP:
        raise H7EvidenceError("Absent or unbounded H6 private NGAP capture")
    if exec_fn is None:
        def exec_fn(args):
            p = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if p.returncode:
                # Never print tool stderr or NAS data.
                raise H7EvidenceError("Read-only NAS packet decoder failed")
            return p.stdout
    result = {"source": "INDEPENDENT_REAL_NGAP_WIRE",
              "known_frame_numbers": {name: [] for name in KNOWN.values()},
              "other_classes": {},
              "registration_reject_causes": {},
              "security_header_counts": {},
              "total_nas_pdu_frames": 0,
              "ciphered_inner_messages_decoded": False}
    # Message-specific NGAP filters match the independently verified H4/H6
    # dissection contract; a generic NAS-PDU presence filter hid a bad IP.
    for direction, message, src, dst in (
        ("downlink", "DownlinkNASTransport", AMF, CU),
        ("uplink", "UplinkNASTransport", CU, AMF),
        ("uplink", "InitialUEMessage", CU, AMF),
    ):
        expr = f"ngap.{message}_element && ip.src == {src} && ip.dst == {dst}"
        cmd = ["tshark", "-n", "-r", str(pcap),
               "-Y", expr, "-T", "fields", "-E", "occurrence=f",
               "-e", "frame.number", "-e", "ngap.NAS_PDU"]
        data = exec_fn(cmd)
        for row in data.splitlines():
            cols = row.split("\t")
            if len(cols) != 2 or not re.fullmatch("[1-9][0-9]*", cols[0]):
                raise H7EvidenceError("Unexpected NGAP NAS field format")
            frame = int(cols[0])
            kind = classify_nas_pdu(cols[1], direction)
            result["total_nas_pdu_frames"] += 1
            if result["total_nas_pdu_frames"] > MAX_FRAMES:
                raise H7EvidenceError("Unbounded NAS messages")
            key = direction + ":" + str(kind["security_header_type"])
            result["security_header_counts"][key] = result["security_header_counts"].get(key, 0) + 1
            klass = kind["class"]
            if klass == "REGISTRATION_REJECT":
                result["registration_reject_causes"][str(frame)] = {
                    "code": kind["registration_reject_cause_code"],
                    "label": kind["registration_reject_cause_label"],
                    "cause_source": "VISIBLE_NON_CIPHERED_NAS_ONLY",
                }
            if klass in result["known_frame_numbers"]:
                result["known_frame_numbers"][klass].append(frame)
            else:
                result["other_classes"][klass] = result["other_classes"].get(klass, 0) + 1
    for name in result["known_frame_numbers"]:
        result["known_frame_numbers"][name] = sorted(set(result["known_frame_numbers"][name]))
    return result


def qualify(h6, wire):
    """H7 requires ordered SMC and UL protection, never infers ciphered type."""
    if h6.get("status") != H6_PASS:
        return "BLOCKED_H6_AUTHENTICATION"
    if wire.get("source") != "INDEPENDENT_REAL_NGAP_WIRE":
        return "BLOCKED_NO_REAL_NAS_WIRE"
    auth = h6.get("nas_wire") or {}
    req = auth.get("authentication_request_frames") or []
    rsp = auth.get("authentication_response_frames") or []
    if not req or not rsp or min(req) >= min(rsp):
        return "BLOCKED_H6_AUTH_FRAME_ORDER"
    events = wire.get("known_frame_numbers") or {}
    # A successful H6 audit MUST be reproducible independently in H7
    # from the very same PCAP. Never report 'no SMC' after decoding zero
    # authentication messages because of a bad IP/filter.
    if not set(req).issubset(set(events.get("AUTHENTICATION_REQUEST") or [])) or not set(rsp).issubset(set(events.get("AUTHENTICATION_RESPONSE") or [])):
        return "BLOCKED_H6_AUTH_NOT_REDECODED_BY_H7"
    reject = ["REGISTRATION_REJECT", "AUTHENTICATION_REJECT",
              "SECURITY_MODE_REJECT", "AUTHENTICATION_FAILURE"]
    if any(events.get(key) for key in reject):
        return "BLOCKED_VISIBLE_NAS_REJECTION"
    command = events.get("SECURITY_MODE_COMMAND") or []
    if not command or min(command) <= min(rsp):
        return "BLOCKED_SECURITY_MODE_COMMAND_WIRE"
    cmd = min(command)
    complete = events.get("SECURITY_MODE_COMPLETE") or []
    if any(f > cmd for f in complete):
        return "NAS_SECURITY_MODE_COMMAND_COMPLETE_HEADERS_CANDIDATE_ONLY"
    # A protected UL after command may be ciphered. This is evidence of
    # protected NAS, NOT proof of Security Mode Complete.
    up = sum(n for k, n in (wire.get("security_header_counts") or {}).items()
             if k in ("uplink:2", "uplink:4"))
    return ("NAS_SMC_AND_UPLINK_PROTECTED_TRAFFIC_ONLY"
            if up else "BLOCKED_NO_UPLINK_PROTECTED_NAS_AFTER_SMC")


def run(args):
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise H7EvidenceError("GitHub-hosted disposable runner required")
    root = Path(os.environ["RUNNER_TEMP"]).resolve() / "hcransrs-h2"
    pcap = Path(args.capture).resolve()
    receipt_path = Path(args.h6_receipt).resolve()
    out = Path(args.output).resolve()
    if (pcap != root / "runner-local" / "setup.pcapng"
            or receipt_path != root / "h6-receipt.json"
            or out != root / "h7-receipt.json"):
        raise H7EvidenceError("Unreviewed runner input or output paths")
    if Path(args.capture).is_symlink() or Path(args.h6_receipt).is_symlink():
        raise H7EvidenceError("Symlinked runner evidence forbidden")
    h6 = json.loads(receipt_path.read_text())
    if h6.get("production_decision") != "NO_GO":
        raise H7EvidenceError("Unreviewed H6 production decision")
    wire = decode_wire(pcap)
    result = {"schema": "hcransrs-h7-real-post-auth-nas-v1",
              "status": qualify(h6, wire),
              "h6_candidate": h6.get("status"),
              "wire": wire,
              "registration_accept_and_complete": "NOT_ESTABLISHED",
              "cryptographic_integrity_or_decryption": "NOT_VALIDATED",
              "hp_cluster_touched": False, "production_decision": "NO_GO"}
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"],
                      "known_frame_numbers": wire["known_frame_numbers"],
                      "other_classes": wire["other_classes"],
                      "registration_reject_causes": wire["registration_reject_causes"],
                      "security_header_counts": wire["security_header_counts"],
                      "production_decision": "NO_GO"}, sort_keys=True))
    # H7 is forensic qualification, rather than blind success if undecodable.
    return 0 if result["status"].startswith("NAS_SECURITY_MODE_COMMAND_COMPLETE_HEADERS") else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--capture", required=True)
    p.add_argument("--h6-receipt", required=True)
    p.add_argument("--output", required=True)
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
