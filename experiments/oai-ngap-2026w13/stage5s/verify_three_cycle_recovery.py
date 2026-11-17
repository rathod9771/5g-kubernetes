#!/usr/bin/env python3
"""Strict Stage5P per-cycle evidence verifier (offline only, no synthetic PASS).

An unbroken sequence of 3 *distinct* AMF faults is required in ONE UE session.
Each preserved cycle is validated using the existing Stage5I and Stage5K
protocol/data-plane contracts, not text claims in a summary.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "stage5s"), str(ROOT / "stage5i"), str(ROOT / "stage5k")]
from verify_bounded_recovery_stage5s import check as check_nas
from verify_tun_renewal import check as check_tun

HEX = re.compile(r"^[0-9a-f]{64}$")
FILES = (
    "stage5e-recovery-status.json",
    "stage5i-postfault-markers.json",
    "stage5i-runtime.json",
    "post-AMF-gtpu-evidence.json",
    "gtpu.pcap",
    "results.json",
)

def verify(root):
    root = Path(root)
    errors = []
    try:
        report = json.loads((root / "stage5p-multicycle-summary.json").read_text())
        events = json.loads((root / "results.json").read_text())
    except (OSError, ValueError, TypeError) as exc:
        return False, ["Missing/malformed Stage5P evidence: " + type(exc).__name__]
    if not isinstance(report, dict) or not isinstance(events, list):
        return False, ["Invalid Stage5P record structure"]
    if (report.get("schema") != "hcran-stage5p-three-consecutive-amf-recoveries-v1"
            or report.get("requested_cycles") != 3 or report.get("status") != "PASS"
            or report.get("production_authorized") is not False
            or report.get("production_decision") != "NO_GO"):
        errors.append("Unqualified Stage5P summary")
    identity = report.get("ue_process_identity_sha256")
    if not isinstance(identity, str) or not HEX.fullmatch(identity):
        errors.append("Missing UE process fingerprint")
    cycles = report.get("cycles")
    if not isinstance(cycles, list) or len(cycles) != 3:
        return False, errors + ["Exactly three complete cycles required"]
    seen_captures = set()
    prev_after = None
    for i, item in enumerate(cycles, 1):
        cdir = root / "cycles" / f"cycle-{i:02d}"
        if not isinstance(item, dict):
            errors.append(f"Cycle {i} not an object")
            continue
        if (item.get("cycle") != i or item.get("status") != "PASS"
                or item.get("ue_process_identity_sha256") != identity
                or item.get("production_decision") != "NO_GO"
                or not isinstance(item.get("duration_seconds"), (int, float))
                or not 0 < item.get("duration_seconds", -1) <= 440):
            errors.append(f"Cycle {i} invalid completion/identity/timing")
        if not cdir.is_dir() or cdir.is_symlink():
            errors.append(f"Cycle {i} evidence directory missing/unsafe")
            continue
        if not all((cdir / name).is_file() and not (cdir / name).is_symlink()
                   for name in FILES):
            errors.append(f"Cycle {i} lacks complete authentic proof")
            continue
        pcap = cdir / "gtpu.pcap"
        pcap_hash = hashlib.sha256(pcap.read_bytes()).hexdigest()
        if (item.get("postfault_gtpu_pcap_sha256") != pcap_hash
                or pcap_hash in seen_captures or pcap.stat().st_size < 150):
            errors.append(f"Cycle {i} missing/distinct GTP-U packet capture")
        seen_captures.add(pcap_hash)
        try:
            state = json.loads((cdir / "stage5e-recovery-status.json").read_text())
            if (state.get("before_pdu_ips") != item.get("before_pdu_ips")
                    or state.get("after_pdu_ips") != item.get("after_pdu_ips")):
                errors.append(f"Cycle {i} PDU IPs mismatch authentic Stage5E source")
            if prev_after is not None and item.get("before_pdu_ips") != prev_after:
                errors.append(f"Cycle {i} pre-fault IPs not continuous with prior cycle")
            prev_after = item.get("after_pdu_ips")
        except (OSError, ValueError, TypeError):
            errors.append(f"Cycle {i} PDU state unreadable")
        ok_i, err_i = check_nas(cdir)
        ok_k, err_k = check_tun(cdir)
        if not ok_i or not ok_k:
            errors.append(f"Cycle {i} independent proof failed: " +
                          "; ".join((err_i + err_k)[:6]))
        if not any(e.get("stage") == "STAGE5P-CYCLE" and e.get("status") == "PASS"
                   and e.get("cycle") == i for e in events if isinstance(e, dict)):
            errors.append(f"Cycle {i} missing real runner PASS event")
    if not any(e.get("stage") == "STAGE5P" and e.get("status") == "PASS"
               for e in events if isinstance(e, dict)):
        errors.append("Missing terminal three-cycle PASS event")
    return not errors, errors

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence", type=Path)
    opts = ap.parse_args()
    ok, errs = verify(opts.evidence)
    print(json.dumps({"stage": "STAGE5P-MULTICYCLE", "status": "PASS" if ok else "FAIL",
                      "cycles_required": 3, "reasons": errs,
                      "production_decision": "NO_GO"}, indent=2))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
