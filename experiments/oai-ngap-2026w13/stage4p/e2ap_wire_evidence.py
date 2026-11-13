#!/usr/bin/env python3
"""Bounded proof of the E2AP Setup procedure on an isolated SCTP wire.

For pinned FlexRIC that uses SCTP PPID=0 (no Wireshark auto-dissect).
Matches E2AP APER top-level PDU choice + procedure code 1 (E2 Setup):
  initiatingMessage: 00 01 ; successfulOutcome: 20 01
This is a bounded protocol-envelope check, not full ASN.1/conformance decode.
"""
import hashlib
import ipaddress
import pathlib
import struct


def parse_e2_setup_sctp(pcap_path, cu_ip, ric_ip):
    data = pathlib.Path(pcap_path).read_bytes()
    if len(data) < 24 or data[:4] != bytes.fromhex("d4c3b2a1"):
        raise ValueError("Expected little-endian classic PCAP")
    linktype = struct.unpack_from("<I", data, 20)[0]
    if linktype != 276:
        raise ValueError(f"Expected Linux cooked v2 linktype 276; got {linktype}")
    result = {"e2_setup_requests": [], "e2_setup_responses": [],
              "ppid_counts": {}, "pcap_sha256": hashlib.sha256(data).hexdigest()}
    off = 24
    frame_no = 0
    while off + 16 <= len(data):
        _, _, captured, _ = struct.unpack_from("<IIII", data, off)
        off += 16
        if captured > 262144 or off + captured > len(data):
            break
        frame = data[off:off + captured]
        off += captured
        frame_no += 1
        if len(frame) < 40 or frame[:2] != bytes.fromhex("0800"):
            continue
        ip = frame[20:]
        if not ip or (ip[0] >> 4) != 4:
            continue
        ihl = (ip[0] & 15) * 4
        if ip[9] != 132 or len(ip) < ihl + 12:
            continue
        src = str(ipaddress.IPv4Address(ip[12:16]))
        dst = str(ipaddress.IPv4Address(ip[16:20]))
        sctp = ip[ihl:]
        sport, dport = struct.unpack_from("!HH", sctp, 0)
        cu_to_ric = src == cu_ip and dst == ric_ip and dport == 36421
        ric_to_cu = src == ric_ip and dst == cu_ip and sport == 36421
        if not (cu_to_ric or ric_to_cu):
            continue
        idx = 12
        while idx + 4 <= len(sctp):
            typ, flags, length = struct.unpack_from("!BBH", sctp, idx)
            if length < 4 or idx + length > len(sctp):
                break
            if typ == 0 and length >= 16:
                body = sctp[idx + 4:idx + length]
                ppid = struct.unpack_from("!I", body, 8)[0]
                payload = body[12:]
                key = str(ppid)
                result["ppid_counts"][key] = result["ppid_counts"].get(key, 0) + 1
                observation = {"frame": frame_no, "bytes": len(payload),
                               "payload_sha256": hashlib.sha256(payload).hexdigest()}
                if cu_to_ric and payload[:2] == bytes.fromhex("0001"):
                    result["e2_setup_requests"].append(observation)
                if ric_to_cu and payload[:2] == bytes.fromhex("2001"):
                    result["e2_setup_responses"].append(observation)
            idx += (length + 3) & ~3
    return result
