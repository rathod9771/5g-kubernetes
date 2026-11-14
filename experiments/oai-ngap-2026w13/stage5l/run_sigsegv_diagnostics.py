#!/usr/bin/env python3
"""Stage5L: attach host GDB to disposable RFsim UE processes around AMF fault.

Pure diagnosis: uses the exact archived Stage5K candidate and frozen Stage4P
harness; never changes any NAS/RRC recovery code. All debugger raw output
stays in a private runner directory and is deleted with the synthetic keys.
Only function names, no arguments, addresses, or private runtime data are
published. NO DEPLOY and NO production promotion.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import types

from run_bounded_recovery import build


def safe_summary(raw, role, status):
    # GDB raw output may contain private data and must NEVER be uploaded.
    sigsegv = bool(re.search(r"(Program received signal SIGSEGV|Thread .*SIGSEGV)", raw))
    frames = []
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"^#(\\d+)\\s+(?:0x[0-9A-Fa-f]+\\s+in\\s+)?([A-Za-z_~][\\w:~.<>$-]*)", line)
        if m:
            frames.append({"frame": int(m.group(1)), "symbol": m.group(2)[:100]})
    observations = []
    for label, pat in (
        ("attach_denied", r"(ptrace: Operation not permitted|Could not attach|ptrace: Permission denied)"),
        ("target_exited", r"(Inferior .*exited|No such process|process .* is a zombie)"),
        ("segmentation_signal", r"(Program received signal SIGSEGV|Thread .*SIGSEGV)"),
        ("no_symbols", r"(No symbol table info available|No debugging symbols found)"),
        ("backtrace_unavailable", r"(No stack|Cannot access memory)"),
    ):
        if re.search(pat, raw, re.I):
            observations.append(label)
    return {
        "role": role,
        "debugger_exit": status,
        "sigsegv_seen_by_gdb": sigsegv,
        "observations": observations,
        "symbol_frames": frames[:90],
        "full_raw_log_uploaded": False,
    }


def build_debug_probe(opts):
    probe = build(opts)
    base_fault = probe.fault_and_recover

    def fault_with_debugger(self):
        from run_ue_registration import name
        private = self.priv / "stage5l-private-gdb"
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        candidates = {}
        proc_status = {}
        report = {
            "schema": "stage5l-disposable-ue-segv-stack-v1",
            "experiment": "AMF-only two-UE recovery with archived unchanged Stage5K test binary",
            "source_ue_candidate_sha256": "803c3ac7a6e90c61cd94f83540343960340d6dea15b39bd0731eb36820d291b1",
            "production_decision": "NO_GO",
            "backtraces": {},
        }
        try:
            for role in ("ue", "ue-small"):
                details = json.loads(subprocess.check_output(
                    ["docker", "inspect", name(role)], timeout=12, text=True))[0]
                st = details.get("State", {})
                pid = st.get("Pid")
                if st.get("Running") is not True or not isinstance(pid, int) or pid <= 1:
                    raise RuntimeError("Stage5L requires unchanged live " + role)
                # Attach by HOST PID; only to GitHub-hosted throwaway containers.
                raw_path = private / (role + "-gdb-private.log")
                file_obj = raw_path.open("w")
                cmds = [
                    "sudo", "-n", "gdb", "-q", "-nx", "-batch", "-p", str(pid),
                    "-ex", "set pagination off",
                    "-ex", "set confirm off",
                    "-ex", "set print frame-arguments none",
                    "-ex", "handle SIGPIPE nostop noprint pass",
                    "-ex", "handle SIGSEGV stop print pass",
                    "-ex", "continue",
                    "-ex", "printf STAGE5L_SIGNAL_STACK\\n",
                    "-ex", "thread apply all bt 12",
                    "-ex", "continue",
                ]
                p = subprocess.Popen(
                    cmds, stdout=file_obj, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                file_obj.close()
                candidates[role] = (p, raw_path)
            # Verify both debuggers can attach BEFORE deliberately stopping AMF.
            time.sleep(4)
            for role, (p, path) in candidates.items():
                captured = path.read_text(errors="replace")
                if p.poll() is not None or re.search(
                    r"(ptrace: Operation not permitted|Could not attach|ptrace: Permission denied)",
                    captured, re.I
                ):
                    raise RuntimeError("Stage5L fail-closed: host ptrace attach unavailable for " + role)
            self.record("STAGE5L-DEBUGGER-ATTACHED", "PASS", roles=list(candidates),
                        no_host_or_live_kubernetes_access=True)
            base_fault()
        finally:
            # Capture before the baseline cleanup deletes Docker containers.
            for role, (p, path) in candidates.items():
                if p.poll() is None:
                    try:
                        p.terminate()
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(timeout=5)
                raw = path.read_text(errors="replace") if path.exists() else ""
                report["backtraces"][role] = safe_summary(raw, role, p.returncode)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                private.rmdir()
            except OSError:
                pass
            # Metadata-only artifact: no args, IMSIs, keys, raw stack addresses.
            target = self.out / "stage5l-sigsegv-symbols.json"
            target.write_text(json.dumps(report, indent=2) + "\n")
            seen = [name for name, e in report["backtraces"].items()
                    if e["sigsegv_seen_by_gdb"]]
            self.record("STAGE5L-SYMBOLIC-STACK-CAPTURE",
                        "OBSERVED" if seen else "NOT_OBSERVED",
                        roles_with_sigsegv=seen,
                        production_decision="NO_GO")
    probe.fault_and_recover = types.MethodType(fault_with_debugger, probe)
    return probe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    for label in ("rendered", "output", "cu-image", "du-image", "ue-image"):
        parser.add_argument("--" + label, required=True)
    parser.add_argument("--verify-gtpu", action="store_true")
    opts = parser.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: real postfault traffic evidence required")
    return build_debug_probe(opts).run()


if __name__ == "__main__":
    sys.exit(main())
