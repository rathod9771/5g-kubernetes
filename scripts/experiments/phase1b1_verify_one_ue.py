#!/usr/bin/env python3
"""Independent Phase 1B1 receipt: prove one NR UE with genuine packet evidence.

Never generate PASS from a Helm render or protocol marker alone. Verify full
ordered runner receipt, UE-assigned TUN IPv4, zero-loss ICMP and parse a real
nontrivial GTPv1-U PCAP using the frozen peer-aware decoder. No raw logs or
subscriber credentials are persisted in the public sanitized receipt.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

SCHEMA = "phase1b1-cran-oai-one-ue-actual-data-plane-v1"
STAGES = (
    "T0-isolated-bridge",
    "T1-synthetic-test-subscriber",
    "T2-5GC-config-generated",
    "T3-real-5GC-started",
    "T4-cu-started",
    "T4-macro-started",
    "T5-real-NGSetupResponse",
    "T6-F1SetupRequest",
    "T7-F1SetupResponse",
    "T8-real-synthetic-UE-started",
    "T9-real-NAS-RegistrationAccept",
    "T10-real-PDU-SessionEstablishmentAccept",
    "T11-UE-assigned-IP",
    "T12-F1U-NGU-real-GPDU-4directions",
    "T13-real-UE-UPF-internal-ICMP",
    "STAGE4L",
    "PHASE1B1",
)
COUNTS = ("f1u_ul", "f1u_dl", "ngu_ul", "ngu_dl")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ProofError(ValueError):
    pass


def read_file(path, max_size):
    path = pathlib.Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_size:
        raise ProofError("Required runner-local file missing or oversized")
    return path


def ordered_events(data):
    if not isinstance(data, list) or not 10 <= len(data) <= 100:
        raise ProofError("Invalid/oversized runtime event series")
    matching = [x for x in data if isinstance(x, dict) and x.get("stage") in STAGES]
    if tuple(x["stage"] for x in matching) != STAGES:
        raise ProofError("Missing/reordered/duplicated authentic milestones")
    if any(not isinstance(x, dict) or x.get("status") == "FAIL" for x in data):
        raise ProofError("Runtime recorded a failure")
    prev_secs = -1
    prev_time = None
    for ev in matching:
        if ev.get("status") != "PASS":
            raise ProofError("Required runtime milestone was not PASS")
        seconds = ev.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < prev_secs or seconds > 3600:
            raise ProofError("Invalid elapsed time ordering")
        prev_secs = seconds
        try:
            when = dt.datetime.fromisoformat(ev["time"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ProofError("Malformed UTC event evidence") from exc
        if when.tzinfo is None or when.utcoffset() != dt.timedelta(0):
            raise ProofError("Event timestamp not UTC")
        if prev_time and when < prev_time:
            raise ProofError("Event UTC timestamps out of sequence")
        prev_time = when
    if "no AMF fault, no H-CRAN" not in matching[-1].get("scope", ""):
        raise ProofError("Runtime acceptance scope incorrectly broad")
    return prev_secs


def verify(root, baseline):
    root = pathlib.Path(root)
    results = json.loads(read_file(root / "results.json", 128 * 1024).read_text())
    elapsed = ordered_events(results)
    tun = read_file(root / "UE-PDU-interface.txt", 4096).read_text()
    ips = set(re.findall(r"\binet\s+(10\.45\.[0-9]{1,3}\.[0-9]{1,3})/[0-9]{1,2}", tun))
    if "oaitun_ue1" not in tun or len(ips) != 1:
        raise ProofError("Exactly one real UE PDU IPv4 on oaitun_ue1 required")
    ping = read_file(root / "UE-internal-ping.txt", 8192).read_text()
    match = re.search(r"\b(\d+) packets transmitted,\s*(\d+) (?:packets )?received", ping)
    if not match or (int(match.group(1)), int(match.group(2))) != (4, 4) or "0% packet loss" not in ping:
        raise ProofError("Missing real four-packet zero-loss UE PDU ICMP result")
    pcap = read_file(root / "gtpu.pcap", 8 * 1024 * 1024)
    if pcap.stat().st_size < 150:
        raise ProofError("GTPv1-U capture insufficient")
    frozen = pathlib.Path(baseline).resolve() / "stage4k" / "gtpu_wire.py"
    if not frozen.is_file():
        raise ProofError("Frozen independent GTPv1-U decoder is missing")
    sys.path.insert(0, str(frozen.parent))
    import gtpu_wire
    observed = gtpu_wire.analyze(pcap)
    original = json.loads(read_file(root / "GTPU-GPDU-evidence.json", 64 * 1024).read_text())
    actual_sha = hashlib.sha256(pcap.read_bytes()).hexdigest()
    if not HEX64.fullmatch(actual_sha):
        raise ProofError("Invalid PCAP hash")
    if original.get("pcap_sha256") != actual_sha or observed.get("pcap_sha256") != actual_sha:
        raise ProofError("Packet capture digest disagreement")
    oldcounts = original.get("G_PDU_counts", {})
    current = observed.get("G_PDU_counts", {})
    teids = observed.get("TEIDs", {})
    if set(current) != set(COUNTS) or any(
            type(current.get(k)) is not int or current[k] < 1
            or current[k] != oldcounts.get(k)
            or not isinstance(teids.get(k), list)
            or len(teids[k]) < 1 for k in COUNTS):
        raise ProofError("One or more actual F1-U/NG-U UL/DL G-PDU directions absent")
    events_packet = next(x for x in results if x["stage"] == "T12-F1U-NGU-real-GPDU-4directions")
    if events_packet.get("packet_counts") != current:
        raise ProofError("Runner event packet counters differ from independent decoder")
    return {
        "schema": SCHEMA, "scenario": "cran-oai",
        "status": "ONE_UE_NAS_PDU_TUN_F1U_NGU_ICMP_RUNTIME_PASS",
        "evidence_type": "real-isolated-GitHub-Docker-OAI-RFsim",
        "protocol_evidence": ["NGSetupResponse", "F1SetupRequest", "F1SetupResponse",
                              "RegistrationAccept", "PDUSessionEstablishmentAccept",
                              "UE_TUN_IP", "bidirectional_F1U_NGU_G_PDU", "zero_loss_ICMP"],
        "data_plane": {"gtpu_g_pdu_counts": current, "pcap_sha256": actual_sha,
                        "ue_tun_ipv4_present": True,
                        "independently_decoded_with_frozen_peer_ips": True,
                        "icmp_4_sent_4_received": True},
        "runtime_seconds": elapsed, "fault_injection": "NOT_RUN",
        "srsran_procedures": "NOT_RUN", "other_architectures": "NOT_RUN",
        "test_scope": "one UE/one CU/one DU, test-only synthetic subscriber",
        "hp_cluster_touched": False, "production_authorized": False,
        "production_decision": "NO_GO",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", required=True, type=pathlib.Path)
    p.add_argument("--baseline", required=True, type=pathlib.Path)
    p.add_argument("--receipt", required=True, type=pathlib.Path)
    a = p.parse_args(argv)
    try:
        result = verify(a.evidence, a.baseline)
        a.receipt.parent.mkdir(parents=True, exist_ok=True)
        a.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        print("FAIL CLOSED Phase 1B1: " + type(exc).__name__ + ": " +
              str(exc)[:150], file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
