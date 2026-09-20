#!/usr/bin/env python3
import os
import time
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [layer3] %(message)s")
log = logging.getLogger("layer3-watcher")

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:30990")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8090")
CHECK_INTERVAL_SECONDS = float(os.environ.get("CHECK_INTERVAL_SECONDS", "10"))
ACTION_COOLDOWN_SECONDS = float(os.environ.get("ACTION_COOLDOWN_SECONDS", "120"))

BLER_THRESHOLD = float(os.environ.get("BLER_THRESHOLD", "0.05"))
BLER_QUERY = 'max(oai_gnb_ue_dl_bler_ratio) or max(oai_gnb_ue_ul_bler_ratio)'

FAILOVER_SCENARIO = os.environ.get("FAILOVER_SCENARIO", "hcran-oai")

ACTIONS_LOG_PATH = os.environ.get(
    "ACTIONS_LOG_PATH",
    os.path.expanduser("~/5g-kubernetes/layer3-autonomous/actions.log"),
)

last_action_time = 0.0


def query_prometheus(promql):
    url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(promql)}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read().decode())
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {data}")
    result = data["data"]["result"]
    if not result:
        return None
    return float(result[0]["value"][1])


def record_action(kind, detail):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **detail,
    }
    line = json.dumps(entry)
    log.info(f"ACTION: {line}")
    try:
        with open(ACTIONS_LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        log.warning(f"Could not write actions log: {e}")


def trigger_failover(bler):
    global last_action_time
    now = time.time()
    if now - last_action_time < ACTION_COOLDOWN_SECONDS:
        log.info(
            f"BLER {bler:.4f} exceeds threshold but still in cooldown "
            f"({ACTION_COOLDOWN_SECONDS - (now - last_action_time):.0f}s left) -- not re-triggering"
        )
        return

    payload = json.dumps({"ran": FAILOVER_SCENARIO}).encode()
    req = urllib.request.Request(
        f"{DASHBOARD_URL}/api/deploy",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=200) as resp:
            body = json.loads(resp.read().decode())
        last_action_time = now
        record_action(
            "ran_failover",
            {
                "reason": f"BLER {bler:.4f} exceeded threshold {BLER_THRESHOLD}",
                "target_scenario": FAILOVER_SCENARIO,
                "dashboard_response": body,
            },
        )
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        record_action(
            "ran_failover_failed",
            {
                "reason": f"BLER {bler:.4f} exceeded threshold {BLER_THRESHOLD}",
                "target_scenario": FAILOVER_SCENARIO,
                "error": str(e),
            },
        )


def evaluate_once():
    try:
        bler = query_prometheus(BLER_QUERY)
    except Exception as e:
        log.warning(f"Prometheus query error: {e}")
        return

    if bler is None:
        log.info("No BLER data yet (no active UE/gNB) -- skipping this check")
        return

    if bler > BLER_THRESHOLD:
        log.warning(f"SLA DEGRADATION: BLER {bler:.4f} > threshold {BLER_THRESHOLD}")
        trigger_failover(bler)
    else:
        log.info(f"SLA OK: BLER {bler:.4f} (threshold {BLER_THRESHOLD})")


if __name__ == "__main__":
    log.info(
        f"Layer 3 watcher starting: threshold=BLER>{BLER_THRESHOLD}, "
        f"interval={CHECK_INTERVAL_SECONDS}s, cooldown={ACTION_COOLDOWN_SECONDS}s, "
        f"failover_target={FAILOVER_SCENARIO}"
    )
    while True:
        evaluate_once()
        time.sleep(CHECK_INTERVAL_SECONDS)
