#!/usr/bin/env python3
"""Stage 4V: fail-closed structural audit of E2SM-RC *effect* evidence.

Validating a manifest does NOT authenticate its contents or establish that a
real E2SM-RC control worked. Only an independently reviewed disposable-runtime
trial with packet captures, measured effect and rollback can do that.
No networking, Kubernetes, Open5GS, OSM, subscriber access or deployment.
"""
import argparse
import datetime as dt
import json
import math
import pathlib
import re
import sys

SHA = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _path(data, dotted):
    cur = data
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            raise ValueError("missing " + dotted)
        cur = cur[key]
    return cur


def _integer(data, field, minimum=1):
    value = _path(data, field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(field + " must be integer >= " + str(minimum))
    return value


def _number(data, field, minimum=None):
    value = _path(data, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(field + " must be numeric")
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(field + " out of range")
    return float(value)


def _string(data, field):
    value = _path(data, field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " must be non-empty string")
    return value


def _true(data, field):
    if _path(data, field) is not True:
        raise ValueError(field + " must be true")


def _false(data, field):
    if _path(data, field) is not False:
        raise ValueError(field + " must be false")


def _sha(data, field, pattern=SHA):
    if not pattern.fullmatch(_string(data, field)):
        raise ValueError(field + " has invalid SHA format")


def _timestamp(data, field):
    raw = _string(data, field)
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(field + " is not ISO-8601") from error
    if value.tzinfo is None:
        raise ValueError(field + " missing timezone offset")
    return value.astimezone(dt.timezone.utc)


def assess(data):
    """Return schema issues; never return a production-ready decision."""
    errors = []

    def check(fn):
        try:
            fn()
        except (ValueError, TypeError, KeyError) as error:
            errors.append(str(error))

    def equal(field, expected):
        actual = _path(data, field)
        if actual != expected:
            raise ValueError(field + " must equal " + repr(expected))

    def wire():
        request = _integer(data, "control.request_wire_frame")
        acknowledged = _integer(data, "control.ack_wire_frame")
        revert_req = _integer(data, "reversal.request_wire_frame")
        revert_ack = _integer(data, "reversal.ack_wire_frame")
        if not request < acknowledged < revert_req < revert_ack:
            raise ValueError("wire frame order must be request < ack < revert request < revert ack")
        if _string(data, "control.request_id") != _string(data, "control.ack_request_id"):
            raise ValueError("RC control request and acknowledgement IDs do not match")
        if _string(data, "reversal.request_id") != _string(data, "reversal.ack_request_id"):
            raise ValueError("RC reversal request and acknowledgement IDs do not match")
        if _string(data, "control.request_id") == _string(data, "reversal.request_id"):
            raise ValueError("RC control and reversal must have distinct request IDs")

    def effect():
        baseline = _number(data, "effect.baseline")
        changed = _number(data, "effect.after_control")
        restored = _number(data, "effect.after_reversal")
        minimum = _number(data, "effect.min_effect", 0.000001)
        tolerance = _number(data, "effect.max_restore_error", 0)
        direction = _string(data, "effect.expected_direction")
        delta = changed - baseline
        if direction not in ("increase", "decrease"):
            raise ValueError("expected_direction must be increase or decrease")
        if direction == "increase" and delta < minimum:
            raise ValueError("independent readback does not show requested increase")
        if direction == "decrease" and -delta < minimum:
            raise ValueError("independent readback does not show requested decrease")
        if abs(restored - baseline) > tolerance:
            raise ValueError("post-reversal state did not return to baseline tolerance")
        observer = _string(data, "effect.observer_kind")
        if observer not in ("independent_ue_measurement", "independent_ran_readback",
                            "independent_packet_capture"):
            raise ValueError("effect observer must be independent of xApp and RIC logs")
        timeline = [_timestamp(data, "effect." + k) for k in
                    ("baseline_utc", "control_utc", "readback_utc",
                     "reversal_utc", "restored_utc")]
        if any(a >= b for a, b in zip(timeline, timeline[1:])):
            raise ValueError("effect timestamp order invalid")
        if (timeline[-1] - timeline[0]).total_seconds() > 1800:
            raise ValueError("effect evidence window exceeds 30 minutes")

    if not isinstance(data, dict):
        return ["root must be a JSON object"]
    for field, value in [
        ("schema_version", "stage4v-rc-effect-v1"),
        ("scope", "isolated-github-hosted-disposable-runner"),
        ("control.service_model", "E2SM-RC"),
        ("control.procedure", "RICcontrol"),
        ("control.outcome", "RICcontrolAcknowledge"),
        ("reversal.outcome", "RICcontrolAcknowledge"),
        ("production_decision", "NO_GO"),
    ]:
        check(lambda f=field, v=value: equal(f, v))
    for f in ("source.run_id", "source.du_count", "source.ue_count",
              "control.ran_function_id", "control.style_id",
              "control.action_id", "effect.baseline_samples",
              "effect.after_samples", "effect.restore_samples"):
        check(lambda f=f: _integer(data, f))
    for f in ("source.head_sha",):
        check(lambda f=f: _sha(data, f, GIT_SHA))
    for f in ("captures.e2ap_pcap_sha256", "captures.external_observer_sha256",
              "captures.dual_ue_traffic_sha256"):
        check(lambda f=f: _sha(data, f))
    for f in ("control.decoded_from_wire", "reversal.decoded_from_wire",
              "effect.independent_observer", "continuity.both_f1c_connected",
              "continuity.both_pdu_sessions_after", "continuity.post_control_traffic",
              "continuity.post_reversal_traffic", "safety.disposable_subscribers_only",
              "safety.rollback_executed", "safety.rollback_on_failure"):
        check(lambda f=f: _true(data, f))
    for f in ("safety.live_hp_cluster_access", "safety.production_deployment"):
        check(lambda f=f: _false(data, f))
    for f in ("effect.metric_name", "effect.unit", "effect.observer_kind"):
        check(lambda f=f: _string(data, f))
    check(lambda: equal("source.du_count", 2))
    check(lambda: equal("source.ue_count", 2))
    check(wire)
    check(effect)
    return errors


def report(manifest):
    errors = assess(manifest)
    return {
        "contract": "stage4v-rc-effect-v1",
        "result": "REJECTED" if errors else "STRUCTURE_VALID_ONLY",
        "issues": errors,
        "real_rc_effect_verified": False,
        "production_deployment_authorized": False,
        "production_decision": "NO_GO",
        "limitation": (
            "A valid JSON manifest is not authenticated packet evidence, does not "
            "prove agent RC action support, and cannot close the RC runtime gate."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", type=pathlib.Path)
    ap.add_argument("--report", type=pathlib.Path)
    args = ap.parse_args(argv)
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = report(data)
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0 if result["result"] == "STRUCTURE_VALID_ONLY" else 1


if __name__ == "__main__":
    sys.exit(main())
