#!/usr/bin/env python3
"""Phase 1B2 observer preflight: capture STARTUP ONLY, before expensive srsRAN build.

Creates one temporary internal Docker bridge and runs the same SCTP capture
command as the real experiment. No AMF, CU, DU, UE, subscribers or traffic
are started. Raw diagnostic output and PCAP stay runner-local; the retained
receipt contains only fixed categorical statuses and bounded integer data.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import phase1b2_srsran_ngf1 as wire

SCHEMA = "phase1b2-observer-preflight-v1"
MAX_DIAG_BYTES = 8192


class PreflightError(RuntimeError):
    pass


def classify_error(log):
    """Safe finite vocabulary; never copy log lines, paths or credentials."""
    if not isinstance(log, str):
        return "OBSERVER_DIAGNOSTIC_UNAVAILABLE"
    patterns = (
        ("NO_SUCH_INTERFACE", r"no such (?:device|interface)|interface .* not found|could not find interface"),
        ("PERMISSION_DENIED", r"permission denied|operation not permitted|you don't have permission|not allowed to capture|sudo: .*password"),
        ("CAPTURE_ARGUMENT_REJECTED", r"invalid (?:capture|argument|option)|unknown option|unrecognized option|error parsing|invalid .*filesize"),
        ("DUMPCAP_LAUNCH_FAILED", r"couldn't run .*dumpcap|could not run .*dumpcap|dumpcap.*not found"),
    )
    for category, pattern in patterns:
        if re.search(pattern, log, re.I):
            return category
    return "OBSERVER_EXIT_UNCLASSIFIED"


def capture_command(interface, output, seconds=8):
    """Keep options identical to actual trial; only duration is shorter."""
    if interface != wire.BRIDGE or not 4 <= seconds <= 120:
        raise PreflightError("Unapproved bridge or capture time")
    return ["sudo", "-n", "tshark", "-n", "-i", interface, "-f", "sctp",
            "-a", "duration:" + str(seconds), "-a", "filesize:4096",
            "-w", str(output)]


def read_diag(path):
    try:
        with path.open("rb") as fh:
            return fh.read(MAX_DIAG_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


def runner_only():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise PreflightError("Only a disposable GitHub-hosted runner is permitted")


def validate_output(path):
    temp = os.environ.get("RUNNER_TEMP")
    if not temp:
        raise PreflightError("Runner temporary directory unavailable")
    p = Path(path).resolve(strict=False)
    root = Path(temp).resolve(strict=True)
    if p == root or root not in p.parents:
        raise PreflightError("Preflight output must stay within runner temp")
    if p.is_symlink():
        raise PreflightError("Refusing symlinked output")
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def preflight(out):
    runner_only()
    out = validate_output(out)
    private = out / "observer-private"
    private.mkdir(mode=0o700, exist_ok=True)
    pcap = private / "startup.pcapng"
    stderr = private / "capture-stderr.log"
    receipt = {"schema": SCHEMA, "status": "BLOCKED",
               "scope": "CAPTURE_STARTUP_ONLY_NO_5G_PROCEDURES",
               "bridge_present": False, "process_survived_startup": False,
               "capture_readable": False, "error_category": "NOT_EXECUTED",
               "not_run": wire.NOT_RUN + ["NGAP_SETUP", "F1AP_SETUP", "SRSRAN_SOURCE_BUILD"],
               "hp_cluster_touched": False, "production_decision": "NO_GO"}
    created = False
    process = None
    try:
        if not shutil.which("docker") or not shutil.which("tshark") or not shutil.which("sudo"):
            receipt["error_category"] = "REQUIRED_TOOL_MISSING"
            raise PreflightError("Required observer tool absent")
        if not Path("/proc/net/sctp").exists():
            receipt["error_category"] = "SCTP_KERNEL_UNAVAILABLE"
            raise PreflightError("SCTP kernel support absent")
        wire.cmd("docker", "network", "create", "--driver", "bridge", "--internal",
                 "--subnet", "172.29.74.0/24", "--opt",
                 "com.docker.network.bridge.name=" + wire.BRIDGE, wire.NET)
        created = True
        receipt["bridge_present"] = Path("/sys/class/net", wire.BRIDGE).exists()
        if not receipt["bridge_present"]:
            receipt["error_category"] = "NO_SUCH_INTERFACE"
            raise PreflightError("Expected Linux bridge not present after Docker network create")
        with stderr.open("w") as log:
            process = subprocess.Popen(capture_command(wire.BRIDGE, pcap),
                                       stdout=subprocess.DEVNULL, stderr=log)
            time.sleep(2)
            premature = process.poll()
            if premature is not None:
                receipt["observer_returncode"] = int(premature)
                receipt["error_category"] = classify_error(read_diag(stderr))
                raise PreflightError("Packet observer exited during startup")
            receipt["process_survived_startup"] = True
            try:
                rc = process.wait(timeout=16)
            except subprocess.TimeoutExpired as exc:
                receipt["error_category"] = "OBSERVER_TIMEOUT"
                raise PreflightError("Bounded packet observer failed to stop") from exc
        receipt["observer_returncode"] = int(rc)
        if rc != 0:
            receipt["error_category"] = classify_error(read_diag(stderr))
            raise PreflightError("Packet observer completed nonzero")
        wire.cmd("sudo", "-n", "chown", str(os.getuid()) + ":" + str(os.getgid()), str(pcap))
        if not pcap.is_file() or pcap.is_symlink() or not 64 <= pcap.stat().st_size <= 5*1024*1024:
            receipt["error_category"] = "PCAP_MISSING_OR_INVALID"
            raise PreflightError("Packet capture output missing, unsafe, or outside bound")
        # No packet content is needed; a readable PCAP proves the observer
        # could open and close a capture on the exact bridge.
        wire.cmd("tshark", "-n", "-r", str(pcap), "-T", "fields",
                 "-e", "frame.number", timeout=15)
        receipt["capture_readable"] = True
        receipt["status"] = "CAPTURE_READY_NO_PROTOCOL_TESTED"
        receipt["error_category"] = "NONE"
        return receipt
    except (PreflightError, OSError, subprocess.SubprocessError, wire.EvidenceError) as exc:
        if receipt["error_category"] == "NOT_EXECUTED":
            receipt["error_category"] = "PREFLIGHT_" + type(exc).__name__.upper()
        return receipt
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        if created:
            wire.cmd("docker", "network", "rm", wire.NET, check=False, timeout=15)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        receipt = preflight(args.out)
    except (PreflightError, OSError, subprocess.SubprocessError, wire.EvidenceError) as exc:
        receipt = {
            "schema": SCHEMA, "status": "BLOCKED",
            "error_category": "PREFLIGHT_" + type(exc).__name__.upper(),
            "scope": "CAPTURE_STARTUP_ONLY_NO_5G_PROCEDURES",
            "hp_cluster_touched": False, "production_decision": "NO_GO"
        }
    result = 0 if receipt["status"] == "CAPTURE_READY_NO_PROTOCOL_TESTED" else 2
    try:
        if args.out.is_dir() and not args.out.is_symlink():
            (args.out / "observer-preflight-receipt.json").write_text(
                json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except OSError:
        result = 2
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
