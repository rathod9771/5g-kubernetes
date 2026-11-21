#!/usr/bin/env python3
"""Bounded, read-only LOCAL srsRAN/OAI (non-H-CRAN) and Open5GS protocol inventory.

No installation, pod modification, subscriber access, faults, packet capture,
shell execution, or radio/UE startup. Only explicit kubectl GET and LOGS.
Log markers are NOT evidence of 3GPP conformance. Default is inventory only.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

EXCLUDED = re.compile(r"(?i)(?:h[-_]?cran|hybrid[-_]?cran)")
RAN = re.compile(r"(?i)(?:srsran|srscu|srsdu|oai[-_]?(?:gnb|cu|du)|(?:^|[-_])(?:cran|vcran|fran|oran|cloud[-_]?ran)[-_]?(?:oai|srsran))")
CORE = re.compile(r"(?i)(?:^|[^a-z0-9])open5gs[-_](?:[a-z0-9]+[-_])?(?:amf|smf|upf|nrf|ausf|udm|udr|pcf)(?=[-_]|[^a-z0-9]|$)")
MESSAGES = {
    "ngap_ng_setup_request": r"(?i)NG[-_ ]?Setup[-_ ]?Request",
    "ngap_ng_setup_response": r"(?i)NG[-_ ]?Setup[-_ ]?Response",
    "ngap_ng_setup_failure": r"(?i)NG[-_ ]?Setup[-_ ]?Failure",
    "ngap_initial_ue_message": r"(?i)Initial[-_ ]?UE[-_ ]?Message",
    "f1ap_f1_setup_request": r"(?i)F1[-_ ]?Setup[-_ ]?Request",
    "f1ap_f1_setup_response": r"(?i)F1[-_ ]?Setup[-_ ]?Response",
    "f1ap_f1_setup_failure": r"(?i)F1[-_ ]?Setup[-_ ]?Failure",
    "rrc_setup_complete": r"(?i)(?:RRC[-_ ]?Setup[-_ ]?Complete|RRC[-_ ]?Connected)",
    "rrc_reconfiguration": r"(?i)RRC[-_ ]?Reconfiguration",
    "registration_request": r"(?i)Registration[-_ ]?Request",
    "registration_accept": r"(?i)Registration[-_ ]?Accept",
    "pdu_session_resource_setup_request": r"(?i)PDU[-_ ]?Session[-_ ]?Resource[-_ ]?Setup[-_ ]?Request",
    "pdu_session_resource_setup_response": r"(?i)PDU[-_ ]?Session[-_ ]?Resource[-_ ]?Setup[-_ ]?Response",
    "pdu_session_establishment_accept": r"(?i)PDU[-_ ]?Session[-_ ]?Establishment[-_ ]?Accept",
}
PATTERNS = {name: re.compile(expr) for name, expr in MESSAGES.items()}
CAPTURE_MAX = 8 * 1024 * 1024


def run(args, timeout=18, max_bytes=CAPTURE_MAX):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError("kubectl query unavailable: " + type(exc).__name__) from None
    if result.returncode:
        raise RuntimeError("kubectl read-only query failed; check RBAC and context")
    if len(result.stdout) > max_bytes:
        raise RuntimeError("kubectl response exceeded safety budget")
    return result.stdout.decode("utf-8", "replace")


def pod_group(pod):
    metadata = pod.get("metadata", {})
    spec = pod.get("spec", {})
    fields = [
        str(metadata.get("name", "")),
        str(metadata.get("namespace", "")),
        str(metadata.get("labels") or {}),
    ]
    # Distinguish workloads by names/labels only. Never inspect Env, Secrets or ConfigMaps.
    text = " ".join(fields)
    if EXCLUDED.search(text):
        return None
    if RAN.search(text):
        return "ran"
    if CORE.search(text):
        return "open5gs"
    return None


def select(pods, limit):
    results = []
    for pod in pods:
        group = pod_group(pod)
        if not group:
            continue
        metadata = pod.get("metadata", {})
        name, ns = str(metadata.get("name", "")), str(metadata.get("namespace", ""))
        if not name or not ns or len(name) > 255 or len(ns) > 255:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", ns):
            continue
        status = pod.get("status", {})
        phase = status.get("phase", "UNKNOWN")
        containers = [str(x.get("name", "")) for x in pod.get("spec", {}).get("containers", [])]
        # Explicitly exclude UE processes, not part of the initial control-plane audit.
        containers = [c for c in containers if re.fullmatch(r"[a-z0-9][a-z0-9-]*", c)]
        if "ue" in name.lower() or not containers:
            continue
        results.append({"namespace": ns, "pod": name, "group": group,
                        "phase": phase, "containers": containers[:3]})
    results.sort(key=lambda x: (x["namespace"], x["pod"]))
    if len(results) > limit:
        raise RuntimeError("matched workload limit exceeded; rerun with a narrower cluster context")
    return results


def safe_output(path, payload):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or path.exists():
        raise RuntimeError("output must be a new regular file (no overwrite or symlink)")
    if not path.parent.is_dir():
        raise RuntimeError("output directory must exist")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe", action="store_true",
                        help="Read bounded recent logs; default inventories Pods only")
    parser.add_argument("--since", default="15m",
                        help="kubectl log window; 1m..120m only (default: 15m)")
    parser.add_argument("--max-pods", type=int, default=35)
    parser.add_argument("--tail", type=int, default=120, help="Per-container log line cap (<=300)")
    parser.add_argument("--output", required=True, help="New owner-only JSON output file")
    args = parser.parse_args(argv)
    found = re.fullmatch(r"([0-9]+)m", args.since)
    if not found or not 1 <= int(found.group(1)) <= 120:
        parser.error("--since must be between 1m and 120m")
    if not 1 <= args.max_pods <= 60 or not 1 <= args.tail <= 300:
        parser.error("--max-pods must be 1..60 and --tail 1..300")
    try:
        # Prevent accidental reliance on remote CI or privileged environment.
        if os.geteuid() == 0:
            raise RuntimeError("refusing to run as root")
        raw = run(["kubectl", "get", "pods", "-A", "-o", "json"], timeout=18)
        payload = json.loads(raw)
        pods = select(payload.get("items", []), args.max_pods)
        out = {
            "schema": 1,
            "tool": "layer3-non-hcran-local-readonly-observer",
            "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "mode": "bounded_log_observation" if args.observe else "inventory_only",
            "excluded_architecture": ["hcran-oai", "hcran-srsran"],
            "included_families": ["srsran", "oai", "open5gs"],
            "pod_inventory": pods,
            "message_marker_counts": {key: 0 for key in MESSAGES},
            "containers_read": 0,
            "note": "Counts are heuristic log markers only; absence is NOT evidence of protocol failure.",
            "standards_conformance": "NOT_ASSESSED",
            "production_readiness": "NO_GO",
            "kubernetes_mutations": 0,
        }
        if args.observe:
            # Bound aggregate requests, output and elapsed time. No retries.
            started = time.monotonic()
            budget = 32
            for pod in pods:
                if time.monotonic() - started > 90 or budget <= 0:
                    out["observation_incomplete"] = True
                    break
                for container in pod["containers"]:
                    if budget <= 0 or time.monotonic() - started > 90:
                        out["observation_incomplete"] = True
                        break
                    budget -= 1
                    try:
                        logs = run(["kubectl", "logs", "-n", pod["namespace"], pod["pod"],
                                    "-c", container, "--since=" + args.since,
                                    "--tail=" + str(args.tail), "--timestamps=false"],
                                   timeout=10, max_bytes=130000)
                    except RuntimeError:
                        out["unavailable_log_reads"] = out.get("unavailable_log_reads", 0) + 1
                        continue
                    for key, pattern in PATTERNS.items():
                        payload_count = len(pattern.findall(logs))
                        out["message_marker_counts"][key] += payload_count
                    out["containers_read"] += 1
        print(safe_output(args.output, out))
        print("OBSERVE_ONLY: no Pods modified, no H-CRAN accessed; "
              "no 3GPP conformance or live recovery claimed")
        return 0
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print("BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
