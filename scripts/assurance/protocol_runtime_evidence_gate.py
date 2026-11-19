#!/usr/bin/env python3
"""Read-only replay of actual isolated OAI/5GC CI protocol events.

Never starts a gNB, contacts a Kubernetes cluster, injects faults or executes healing.
All payloads are bounded and raw logs are never exported.
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORY = "rathod9771/5g-kubernetes"
API = "https://api.github.com/repos/" + REPOSITORY
CASES = (
    ("baseline", 43194751671, 131102702755, "669f4a375954a379ecdc4d9f74658e0700813ad4", "success"),
    ("amf_unassisted_failure", 43219517357, 131187161633, "4080110a74bf6ba58804797c5b7216458e2f0a93", "failure"),
    ("amf_supervised_recovery", 43367213888, 131694351197, "b1e1a7d5f04592bbb1402c45e61a08c3b854ba3d", "success"),
)
MAX_LOG_BYTES = 2 * 1024 * 1024
RECEIPT_VERSION = 1

class EvidenceError(ValueError):
    pass

def require(value, message):
    if not value:
        raise EvidenceError(message)

def _request(path):
    token = os.environ.get("GH_TOKEN", "")
    require(len(token) >= 10, "GITHUB_TOKEN_REQUIRED")
    req = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "User-Agent": "l3-protocol-evidence-replay/1",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return req

def _read_bounded(resp, maximum):
    data = resp.read(maximum + 1)
    require(len(data) <= maximum, "RESPONSE_TOO_LARGE")
    return data

def get_json(path):
    with urllib.request.urlopen(_request(path), timeout=25) as response:
        return json.loads(_read_bounded(response, 512 * 1024))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def job_log(job_id):
    """Authenticated GitHub API request; signed blob URL fetched without credentials."""
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(_request(f"/actions/jobs/{job_id}/logs"), timeout=25) as r:
            require(r.status == 200, "UNEXPECTED_LOG_STATUS")
            return _read_bounded(r, MAX_LOG_BYTES)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        target = e.headers.get("Location", "")
    parsed = urllib.parse.urlsplit(target)
    host = (parsed.hostname or "").lower()
    trusted = (
        host.endswith(".blob.core.windows.net")
        or host.endswith(".actions.githubusercontent.com")
        or host.endswith(".githubusercontent.com")
    )
    require(parsed.scheme == "https" and trusted and not parsed.username and not parsed.password, "UNTRUSTED_LOG_REDIRECT")
    with urllib.request.urlopen(
        urllib.request.Request(target, headers={"User-Agent": "l3-protocol-evidence-replay/1"}),
        timeout=30
    ) as r:
        return _read_bounded(r, MAX_LOG_BYTES)

def parse_stages(raw):
    stages = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[^ ]*Z\s+(\{.*\})\s*$", line)
        if not match:
            continue
        try:
            item = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("stage"), str) or item.get("status") not in ("PASS", "FAIL"):
            continue
        name = item["stage"]
        if name in stages:
            require(stages[name] == item, "CONFLICTING_DUPLICATE_STAGE:" + name)
        stages[name] = item
    require(stages, "NO_PROTOCOL_EVENTS")
    return stages

def stage(events, key, expected="PASS"):
    require(key in events, "MISSING_STAGE:" + key)
    item = events[key]
    require(item.get("status") == expected, "WRONG_STAGE_OUTCOME:" + key)
    return item

def _elapsed(event):
    return dt.datetime.fromisoformat(event["time"])

def validate_baseline(events):
    for key in (
        "T5-real-NGSetupResponse", "T6-F1SetupRequest", "T7-F1SetupResponse",
        "T15-F1SetupRequest-from-DU-101", "T16-F1SetupResponse-to-DU-101",
        "T9-real-NAS-RegistrationAccept", "T10-real-PDU-SessionEstablishmentAccept",
        "T26-second-UE-NAS-RegistrationAccept",
        "T27-second-UE-PDUSessionEstablishmentAccept", "STAGE4O"
    ):
        stage(events, key)
    peers = stage(events, "T17-CU-three-SCTP-peers-before-UE").get("peers")
    require(peers == ["AMF", "macro-DU-100", "small-DU-101"], "NGAP_F1_PEER_SET")
    ips = stage(events, "T28-distinct-PDU-IPv4-addresses")
    require(ips.get("macro_ue") != ips.get("small_ue") and all(ips.get(n) for n in ("macro_ue", "small_ue")), "PDU_ADDRESSES_NOT_DISTINCT")
    pings = stage(events, "T29-two-simultaneous-UE-ping-streams")
    require(pings.get("each_transmitted") == 6 and pings.get("each_received") == 6, "USERPLANE_PING_NOT_PASS")
    traffic = stage(events, "T30-both-DUs-concurrent-F1U-and-two-NGU-TEIDs")
    require(all(type(traffic.get(k)) is int and traffic[k] > 0 for k in
        ("macro_ul", "macro_dl", "small_ul", "small_dl", "ngu_ul", "ngu_dl")), "MISSING_BIDIRECTIONAL_GTPU")
    return {"classification": "PASS_HISTORICAL_PROTOCOL_SMOKE", "ng_setup": True,
            "f1_macro_small": True, "two_ue_registration_pdu": True,
            "two_ue_userplane": True}

def validate_failed_recovery(events):
    validate_baseline(events)
    for key in ("T31-AMF-actually-stopped", "T33-AMF-process-restarted",
                "T34-new-NGSetupResponse-after-AMF-restart",
                "T35-both-F1C-DUs-survived-AMF-restart"):
        stage(events, key)
    failure = stage(events, "STAGE4K", "FAIL")
    require("fresh dual-UE recovery NOT established" in failure.get("error", ""), "UNASSISTED_FAILURE_NOT_VERIFIED")
    require(not any(v.get("status") == "PASS" and "both-UE-PDU-ICMP-restored" in k
                    for k,v in events.items()), "FALSE_FAILURE_OUTCOME")
    return {"classification": "FAIL_UNASSISTED_UE_SERVICE_RECOVERY",
            "ngap_reconnected": True, "ue_session_recovered": False}

def validate_supervised(events):
    validate_baseline(events)
    for key in ("T31-AMF-actually-stopped", "T33-AMF-process-restarted",
                "T34-new-NGSetupResponse-after-AMF-restart", "T35-both-F1C-DUs-survived-AMF-restart",
                "T64-ue-fresh-NAS-registration", "T65-ue-fresh-PDU-session",
                "T64-ue-small-fresh-NAS-registration", "T65-ue-small-fresh-PDU-session"):
        stage(events, key)
    detected = stage(events, "T62-supervisor-detected-persistent-service-loss")
    require(detected.get("missing_ue_tunnels") == ["ue", "ue-small"] and
            detected.get("samples") == 3 and detected.get("hysteresis") == 2,
            "UNTRUSTWORTHY_DETECTION")
    restored = stage(events, "T67-two-synthetic-UE-PDU-services-supervised-restored")
    require(restored.get("restarted_UEs") == {"ue": 1, "ue-small": 1},
            "EXPLICIT_ASSIST_NOT_VERIFIED")
    ping = stage(events, "T36-both-UE-PDU-ICMP-restored")
    require(all(ping.get("streams", {}).get(ue, {}).get(k) == 6
                for ue in ("ue", "ue-small") for k in ("sent", "received")), "POST_FAULT_PING_FAIL")
    t = stage(events, "T37-both-F1U-and-NGU-data-paths-restored")
    require(all(type(t.get(du, {}).get(k)) is int and t[du][k] > 0
                for du in ("macro", "small")
                for k in ("f1u_ul", "f1u_dl", "ngu_ul", "ngu_dl")), "POST_FAULT_GTPU_FAIL")
    result = stage(events, "STAGE4U")
    require(result.get("recovery_mode") == "automated supervised synthetic UE restart"
            and result.get("native_ue_autonomous_recovery") == "NOT_VERIFIED",
            "FALSE_AUTONOMOUS_RECOVERY_CLAIM")
    fault = _elapsed(stage(events, "T31-AMF-actually-stopped"))
    detection = _elapsed(detected)
    service = _elapsed(ping)
    require(fault < detection < service, "NON_MONOTONIC_RECOVERY_TIMELINE")
    return {"classification": "PASS_HISTORICAL_SUPERVISED_SYNTHETIC_UE_RECOVERY",
            "recovery_mode": "SUPERVISED_TEST_ONLY",
            "unassisted_ue_recovery": "NOT_VERIFIED",
            "source_runner_fault_to_detect_seconds": round((detection - fault).total_seconds(), 3),
            "source_runner_fault_to_first_verified_ping_seconds": round((service - fault).total_seconds(), 3),
            "independent_observer_mttr": "NOT_VERIFIED"}

def evaluate_case(case_name, stages):
    if case_name == "baseline":
        return validate_baseline(stages)
    if case_name == "amf_unassisted_failure":
        return validate_failed_recovery(stages)
    if case_name == "amf_supervised_recovery":
        return validate_supervised(stages)
    raise EvidenceError("UNKNOWN_CASE")

def self_test():
    import copy
    mock = {k: {"stage": k, "status": "PASS", "time": "2026-11-13T00:00:00+00:00"} for k in (
        "T5-real-NGSetupResponse", "T6-F1SetupRequest", "T7-F1SetupResponse",
        "T15-F1SetupRequest-from-DU-101", "T16-F1SetupResponse-to-DU-101",
        "T9-real-NAS-RegistrationAccept", "T10-real-PDU-SessionEstablishmentAccept",
        "T26-second-UE-NAS-RegistrationAccept", "T27-second-UE-PDUSessionEstablishmentAccept",
        "STAGE4O", "T17-CU-three-SCTP-peers-before-UE",
        "T28-distinct-PDU-IPv4-addresses", "T29-two-simultaneous-UE-ping-streams",
        "T30-both-DUs-concurrent-F1U-and-two-NGU-TEIDs")}
    mock["T17-CU-three-SCTP-peers-before-UE"]["peers"] = ["AMF", "macro-DU-100", "small-DU-101"]
    mock["T28-distinct-PDU-IPv4-addresses"].update(macro_ue="10.45.0.2", small_ue="10.45.0.3")
    mock["T29-two-simultaneous-UE-ping-streams"].update(each_transmitted=6, each_received=6)
    for k in ("macro_ul", "macro_dl", "small_ul", "small_dl", "ngu_ul", "ngu_dl"):
        mock["T30-both-DUs-concurrent-F1U-and-two-NGU-TEIDs"][k] = 1
    require(validate_baseline(mock)["two_ue_userplane"], "POSITIVE_FIXTURE")
    negatives = [
        lambda x: x.pop("T26-second-UE-NAS-RegistrationAccept"),
        lambda x: x["T28-distinct-PDU-IPv4-addresses"].update(small_ue="10.45.0.2"),
        lambda x: x["T29-two-simultaneous-UE-ping-streams"].update(each_received=0),
        lambda x: x["T30-both-DUs-concurrent-F1U-and-two-NGU-TEIDs"].update(small_dl=0),
        lambda x: x["T5-real-NGSetupResponse"].update(status="FAIL"),
    ]
    for i, mutation in enumerate(negatives):
        case = copy.deepcopy(mock)
        mutation(case)
        try:
            validate_baseline(case)
        except EvidenceError:
            pass
        else:
            raise AssertionError(f"NEGATIVE_FIXTURE_{i}_WAS_ACCEPTED")
    print("6_OFFLINE_REPLAY_CONTRACT_CHECKS_PASS")

def main():
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return
    require(len(sys.argv) == 2, "USAGE: protocol_runtime_evidence_gate.py report.json")
    checks = []
    for name, runid, jobid, sha, outcome in CASES:
        run = get_json(f"/actions/runs/{runid}")
        job = get_json(f"/actions/jobs/{jobid}")
        require(run.get("id") == runid and run.get("head_sha") == sha and
                run.get("status") == "completed" and run.get("conclusion") == outcome,
                "SOURCE_RUN_PROVENANCE_" + name)
        require(job.get("id") == jobid and job.get("run_id") == runid and
                job.get("status") == "completed" and job.get("conclusion") == outcome,
                "SOURCE_JOB_PROVENANCE_" + name)
        raw = job_log(jobid)
        result = evaluate_case(name, parse_stages(raw))
        checks.append({"case": name, "source_run": runid, "source_job": jobid,
                       "source_commit": sha, "job_conclusion": outcome,
                       "log_sha256": hashlib.sha256(raw).hexdigest(), **result})
        print(name + ": " + result["classification"])
    report = {
        "schema": RECEIPT_VERSION,
        "scope": "HISTORICAL_ISOLATED_CI_PROTOCOL_LOG_REPLAY_ONLY",
        "source": REPOSITORY,
        "3gpp_references": {
            "NGAP": "TS 38.413 Rel-16",
            "F1AP": "TS 38.473 Rel-16",
            "RRC": "TS 38.331 Rel-16; NOT VERIFIED BY THESE LOGS",
            "5GS_REGISTRATION_AND_PDU": "TS 23.502 Rel-16",
            "PM": "TS 28.552; KPI conformance NOT VERIFIED BY THESE LOGS"},
        "checks": checks,
        "evidence_only": True,
        "live_cluster_tested_now": False,
        "live_fault_injected_now": False,
        "autonomous_production_self_healing": "NOT_VERIFIED",
        "independent_observer_mttr": "NOT_VERIFIED",
        "production_approval": "NO_GO",
    }
    path = Path(sys.argv[1])
    require(not path.exists(), "REPORT_ALREADY_EXISTS")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("PROTOCOL_LOG_REPLAY_PASS__PRODUCTION_NO_GO")

if __name__ == "__main__":
    try:
        main()
    except (EvidenceError, KeyError, TypeError, ValueError, urllib.error.URLError) as e:
        print("PROTOCOL_LOG_REPLAY_BLOCKED: " + type(e).__name__ + ": " + str(e), file=sys.stderr)
        raise SystemExit(1)
