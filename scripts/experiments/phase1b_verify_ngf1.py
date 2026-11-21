#!/usr/bin/env python3
"""Phase 1B0 independent, fail-closed NGAP/F1AP event receipt (NO UE claims).

This checks actual staged Docker harness event chronology after its runtime
assertions. Markers alone are not packet-level 3GPP conformance evidence.
Never consume files from any other path, network or Kubernetes cluster.
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

SCHEMA = "phase1b-cran-oai-ngf1-runtime-receipt-v1"
EVENTS = (
    "T0-config-and-bridge",
    "T0-open5gs-and-CU-start",
    "T1-CU-real-NGSetupResponse",
    "T2-du-DU-start",
    "T3-F1SetupRequest-from-DU-101",
    "T4-F1SetupResponse-to-DU-101",
    "PHASE1B0",
)
TESTS_NOT_RUN = ("NAS_REGISTRATION", "PDU_SESSION", "F1U_TRAFFIC",
                 "NGU_TRAFFIC", "UE_RECOVERY")


class EvidenceError(ValueError):
    pass


def verify(events):
    if not isinstance(events, list) or len(events) > 80:
        raise EvidenceError("Event list must be bounded")
    selected = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("stage"), str):
            raise EvidenceError("Malformed event")
        if event.get("result") == "FAIL":
            raise EvidenceError("Explicit runner failure event")
        if event.get("stage") in EVENTS:
            selected.append(event)
    if tuple(x["stage"] for x in selected) != EVENTS:
        raise EvidenceError("Required NG Setup and F1 Setup event sequence incomplete or duplicated")
    last_sec = -1
    last_date = None
    for event in selected:
        if event.get("result") != "PASS":
            raise EvidenceError("An NGAP/F1AP stage did not pass")
        elapsed = event.get("elapsed_seconds")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not 0 <= elapsed < 3600:
            raise EvidenceError("Invalid bounded monotonic event duration")
        if elapsed < last_sec:
            raise EvidenceError("Non-monotonic event timing")
        last_sec = elapsed
        try:
            when = dt.datetime.fromisoformat(event["time_utc"].replace("Z", "+00:00"))
        except (AttributeError, ValueError, KeyError) as exc:
            raise EvidenceError("Missing UTC timestamp") from exc
        if when.tzinfo is None or when.utcoffset() != dt.timedelta(0):
            raise EvidenceError("Event timestamp is not UTC")
        if last_date is not None and when < last_date:
            raise EvidenceError("Non-monotonic UTC event timestamps")
        last_date = when
    scope = selected[-1].get("scope", "")
    if not isinstance(scope, str) or "no UE/PDU/user-plane" not in scope:
        raise EvidenceError("Final event must explicitly exclude UE/PDU/user-plane qualification")
    return {
        "schema": SCHEMA,
        "scenario": "cran-oai",
        "status": "NGAP_F1AP_RUNTIME_SMOKE_PASS_UE_NOT_TESTED",
        "observed_stages": list(EVENTS),
        "elapsed_seconds": last_sec,
        "not_run": list(TESTS_NOT_RUN),
        "producer": "isolated-one-DU-C-RAN-OAI-Docker-runtime",
        "validation_limit": "CU runtime log markers for NG Setup and F1 Setup, not independent 3GPP packet decode",
        "fault_injection_performed": False,
        "hp_cluster_touched": False,
        "production_authorized": False,
        "production_decision": "NO_GO",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events", required=True, type=Path)
    p.add_argument("--receipt", required=True, type=Path)
    opts = p.parse_args(argv)
    try:
        if not opts.events.is_file() or opts.events.is_symlink() or opts.events.stat().st_size > 128 * 1024:
            raise EvidenceError("Missing or oversized local event JSON")
        result = verify(json.loads(opts.events.read_text(encoding="utf-8")))
        opts.receipt.parent.mkdir(parents=True, exist_ok=True)
        opts.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print("FAIL CLOSED Phase1B0 receipt: " + type(exc).__name__ + ": " + str(exc)[:140], file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
