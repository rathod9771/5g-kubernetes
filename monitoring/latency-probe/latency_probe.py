#!/usr/bin/env python3
"""
Measures real network round-trip latency by doing a plain TCP connect
against a target service (e.g. the AMF's SBI port) on a fixed
interval, and exposes the result as a Prometheus histogram/gauge.

This is control-plane (SBI) latency, not UE data-plane latency through
a PDU session -- the PDU session path is blocked on a separate,
unresolved PFCP issue in the core. Labelled and documented as such.
"""
import os
import socket
import time
import threading
import logging

from prometheus_client import start_http_server, Histogram, Gauge, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("latency-probe")

TARGET_HOST = os.environ.get("TARGET_HOST", "open5gs-0004215456-amf-sbi")
TARGET_PORT = int(os.environ.get("TARGET_PORT", "7777"))
INTERVAL_SECONDS = float(os.environ.get("INTERVAL_SECONDS", "2"))
TARGET_LABEL = os.environ.get("TARGET_LABEL", "amf-sbi")

LABELS = ["target"]

h_latency = Histogram(
    "network_tcp_connect_latency_seconds",
    "TCP connect round-trip time to target",
    LABELS,
    buckets=[.0005, .001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5],
)
g_latency_last = Gauge(
    "network_tcp_connect_latency_last_seconds",
    "Most recent TCP connect round-trip time to target",
    LABELS,
)
c_success = Counter(
    "network_tcp_connect_success_total", "Successful TCP connects", LABELS
)
c_failure = Counter(
    "network_tcp_connect_failure_total", "Failed TCP connects (timeout/refused)", LABELS
)


def probe_once():
    start = time.perf_counter()
    try:
        with socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=2) as s:
            elapsed = time.perf_counter() - start
            h_latency.labels(target=TARGET_LABEL).observe(elapsed)
            g_latency_last.labels(target=TARGET_LABEL).set(elapsed)
            c_success.labels(target=TARGET_LABEL).inc()
            log.info(f"{TARGET_HOST}:{TARGET_PORT} connect in {elapsed*1000:.2f}ms")
    except OSError as e:
        c_failure.labels(target=TARGET_LABEL).inc()
        log.warning(f"{TARGET_HOST}:{TARGET_PORT} connect failed: {e}")


def probe_loop():
    while True:
        probe_once()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    start_http_server(9092)
    log.info(f"Latency probe listening on :9092/metrics, target={TARGET_HOST}:{TARGET_PORT} every {INTERVAL_SECONDS}s")
    t = threading.Thread(target=probe_loop, daemon=True)
    t.start()
    while True:
        time.sleep(3600)
