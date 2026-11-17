#!/usr/bin/env python3
"""Stage 5P: three AMF faults in ONE disposable two-UE RFsim session.

Unlike Stage 5N (three independent one-fault sessions), this deliberately
keeps both UE process identities and both DUs across three consecutive
AMF-only stop/restart cycles. Each cycle must independently provide
cause-9/NAS/PDU/TUN and post-fault F1-U/NG-U proof. NO DEPLOY, NO GO.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "stage5s"), str(ROOT / "stage5i"), str(ROOT / "stage5k")]
from run_bounded_recovery_stage5s import build  # Stage 5I wraps real Stage 5E
from verify_bounded_recovery_stage5s import check as check_nas
from verify_tun_renewal import check as check_tun

FILES = (
    "stage5e-recovery-status.json",
    "stage5i-postfault-markers.json",
    "stage5i-runtime.json",
    "post-AMF-gtpu-evidence.json",
    "gtpu.pcap",
    "results.json",
)

def canonical_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def configure(opts):
    if opts.cycles != 3:
        raise ValueError("FAIL CLOSED: Stage5P requires exactly three cycles")
    p = build(opts)
    single_fault = p.fault_and_recover

    def three_cycles(self):
        identity = self.ue_identity()
        if set(identity) != {"ue", "ue-small"} or any(v["restart_count"] != 0 for v in identity.values()):
            raise RuntimeError("Pre-fault UE process identity/zero-restart gate failed")
        stable_identity_hash = canonical_sha(identity)
        summary = {
            "schema": "hcran-stage5p-three-consecutive-amf-recoveries-v1",
            "source": "three-AMF-faults-one-disposable-two-UE-RFsim-session",
            "requested_cycles": 3,
            "ue_process_identity_sha256": stable_identity_hash,
            "cycles": [],
            "production_decision": "NO_GO",
            "production_authorized": False,
            "status": "INCOMPLETE",
        }
        target = self.out / "stage5p-multicycle-summary.json"

        def persist():
            target.write_text(json.dumps(summary, indent=2) + "\n")

        persist()
        for i in range(1, 4):
            label = f"cycle-{i:02d}"
            cdir = self.out / "cycles" / label
            cdir.mkdir(parents=True, exist_ok=False)
            elapsed_start = time.monotonic()
            before = self.pdu_addresses()
            if set(before) != {"ue", "ue-small"} or any(not x for x in before.values()) or len(set(before.values())) != 2:
                raise RuntimeError(f"{label} missing distinct pre-fault UE IPs")
            if canonical_sha(self.ue_identity()) != stable_identity_hash:
                raise RuntimeError(f"{label} UE process identity changed before fault")
            start_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.record("STAGE5P-CYCLE-START", "OBSERVED", cycle=i, before_pdu_ips=before)
            try:
                # The original Stage5I wrapper invokes real AMF stop/restart,
                # requires each UE's ordered NAS cause9/Service Request chain,
                # confirms fresh TUN addresses, real postfault wire captures.
                single_fault()
                for filename in FILES:
                    source = self.out / filename
                    if not source.is_file() or source.is_symlink():
                        raise RuntimeError(f"{label} missing authentic {filename}")
                    shutil.copy2(source, cdir / filename)
                nas_ok, nas_errors = check_nas(cdir)
                tun_ok, tun_errors = check_tun(cdir)
                if not nas_ok or not tun_ok:
                    raise RuntimeError(f"{label} independent evidence failed: " +
                                       "; ".join((nas_errors + tun_errors)[:6]))
                state = json.loads((cdir / "stage5e-recovery-status.json").read_text())
                after = state["after_pdu_ips"]
                if state["before_pdu_ips"] != before or any(before[r] == after.get(r)
                      for r in ("ue", "ue-small")) or len(set(after.values())) != 2:
                    raise RuntimeError(f"{label} did not prove fresh PDU addresses for both UEs")
                if self.pdu_addresses() != after:
                    raise RuntimeError(f"{label} live TUN addresses disagree with captured recovery proof")
                if canonical_sha(self.ue_identity()) != stable_identity_hash:
                    raise RuntimeError(f"{label} UE process changed/restarted after recovery")
                if time.monotonic() - elapsed_start > 440:
                    raise RuntimeError(f"{label} exceeded bounded fault/recovery envelope")
                pcap = cdir / "gtpu.pcap"
                pcap_sha = hashlib.sha256(pcap.read_bytes()).hexdigest()
                marker_record = json.loads((cdir / "stage5i-postfault-markers.json").read_text())
                counts = {}
                for role in ("ue", "ue-small"):
                    start = self._stage5i_offsets[role]
                    # Read *this cycle only*. Persist counts, never raw subscriber logs.
                    new_logs = self.logs(role)[start:]
                    counts[role] = new_logs.count("Stage5M: defer random access")
                    markers = marker_record["ue"][role]
                    if markers.get("chain_missing_from"):
                        raise RuntimeError(f"{label} missing NAS chain for {role}")
                item = {
                    "cycle": i,
                    "status": "PASS",
                    "started_utc": start_utc,
                    "duration_seconds": round(time.monotonic() - elapsed_start, 2),
                    "before_pdu_ips": before,
                    "after_pdu_ips": after,
                    "ue_process_identity_sha256": stable_identity_hash,
                    "postfault_gtpu_pcap_sha256": pcap_sha,
                    "ra_guard_deferred_counts": counts,
                    "nas_evidence": "checked independently by Stage5I",
                    "tun_evidence": "checked independently by Stage5K",
                    "production_decision": "NO_GO",
                }
                summary["cycles"].append(item)
                persist()
                self.record("STAGE5P-CYCLE", "PASS", cycle=i, duration_seconds=item["duration_seconds"],
                            before_pdu_ips=before, after_pdu_ips=after, pcap_sha256=pcap_sha)
                # Ensure stable UE and core state before injecting another fault.
                if i < 3:
                    time.sleep(15)
            except Exception as exc:
                summary["status"] = "FAIL"
                summary["failed_cycle"] = i
                summary["failure_type"] = type(exc).__name__
                persist()
                self.record("STAGE5P-CYCLE", "FAIL", cycle=i, error_type=type(exc).__name__)
                raise
        captures = [x["postfault_gtpu_pcap_sha256"] for x in summary["cycles"]]
        if len(set(captures)) != 3:
            summary["status"] = "FAIL"
            summary["failure_type"] = "DuplicatePCAP"
            persist()
            raise RuntimeError("FAIL CLOSED: repeated/reused per-cycle GTP-U capture digest")
        summary["status"] = "PASS"
        persist()
        self.record("STAGE5P", "PASS",
                    scope="three consecutive AMF stop/restarts with SAME two UE processes, fresh PDU IPs each cycle, independent NAS and F1-U/NG-U evidence",
                    production_decision="NO_GO")

    p.fault_and_recover = types.MethodType(three_cycles, p)
    return p

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", required=True)
    for key in ("rendered", "output", "cu-image", "du-image", "ue-image"):
        ap.add_argument("--" + key, required=True)
    ap.add_argument("--cycles", type=int, default=3, choices=[3])
    ap.add_argument("--verify-gtpu", action="store_true")
    args = ap.parse_args()
    if not args.verify_gtpu:
        ap.error("FAIL CLOSED: live postfault F1-U and NG-U wire evidence mandatory")
    return configure(args).run()

if __name__ == "__main__":
    raise SystemExit(main())
