#!/usr/bin/env python3
import os
import re
import time
import threading
import logging

from kubernetes import client, config, watch
from prometheus_client import start_http_server, Gauge, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ran-exporter")

NAMESPACE = os.environ.get("TARGET_NAMESPACE", "")
POD_LABEL_SELECTOR = os.environ.get("POD_LABEL_SELECTOR", "app=cloud-ran-oai-gnb")

LABELS = ["rnti"]

g_rsrp = Gauge("oai_gnb_ue_rsrp_dbm", "Average RSRP reported for this UE", LABELS)
g_snr_dl = Gauge("oai_gnb_ue_dl_snr_db", "Downlink SNR", LABELS)
g_snr_ul = Gauge("oai_gnb_ue_ul_snr_db", "Uplink SNR", LABELS)
g_bler_dl = Gauge("oai_gnb_ue_dl_bler_ratio", "Downlink Block Error Rate", LABELS)
g_bler_ul = Gauge("oai_gnb_ue_ul_bler_ratio", "Uplink Block Error Rate", LABELS)
g_dlsch_errors = Gauge("oai_gnb_ue_dlsch_errors_total", "Cumulative DLSCH errors (uncorrected after retx)", LABELS)
g_ulsch_errors = Gauge("oai_gnb_ue_ulsch_errors_total", "Cumulative ULSCH errors (uncorrected after retx)", LABELS)
g_dlsch_round1 = Gauge("oai_gnb_ue_dlsch_harq_round1_total", "DLSCH HARQ 1st retransmissions", LABELS)
g_dlsch_round2 = Gauge("oai_gnb_ue_dlsch_harq_round2_total", "DLSCH HARQ 2nd retransmissions", LABELS)
g_dlsch_round3 = Gauge("oai_gnb_ue_dlsch_harq_round3_total", "DLSCH HARQ 3rd retransmissions", LABELS)
g_ulsch_round1 = Gauge("oai_gnb_ue_ulsch_harq_round1_total", "ULSCH HARQ 1st retransmissions", LABELS)
g_ulsch_round2 = Gauge("oai_gnb_ue_ulsch_harq_round2_total", "ULSCH HARQ 2nd retransmissions", LABELS)
g_ulsch_round3 = Gauge("oai_gnb_ue_ulsch_harq_round3_total", "ULSCH HARQ 3rd retransmissions", LABELS)
g_mac_tx_bytes = Gauge("oai_gnb_ue_mac_tx_bytes_total", "MAC layer TX bytes to this UE", LABELS)
g_mac_rx_bytes = Gauge("oai_gnb_ue_mac_rx_bytes_total", "MAC layer RX bytes from this UE", LABELS)
lines_parsed = Counter("oai_gnb_exporter_lines_parsed_total", "Log lines matched and parsed")

RE_RSRP = re.compile(r"UE RNTI (\w+).*average RSRP (-?\d+)")
RE_DLSCH = re.compile(
    r"UE (\w+): dlsch_rounds (\d+)/(\d+)/(\d+)/(\d+), dlsch_errors (\d+).*"
    r"SNR ([\d.+-]+).*BLER ([\d.]+)"
)
RE_ULSCH = re.compile(
    r"UE (\w+): ulsch_rounds (\d+)/(\d+)/(\d+)/(\d+), ulsch_errors (\d+).*"
    r"BLER ([\d.]+).*SNR ([\d.+-]+)"
)
RE_MAC = re.compile(r"UE (\w+): MAC:\s+TX\s+(\d+) RX\s+(\d+) bytes")


def parse_line(line):
    m = RE_RSRP.search(line)
    if m:
        rnti, rsrp = m.group(1), float(m.group(2))
        g_rsrp.labels(rnti=rnti).set(rsrp)
        lines_parsed.inc()
        return

    m = RE_DLSCH.search(line)
    if m:
        rnti = m.group(1)
        g_dlsch_round1.labels(rnti=rnti).set(int(m.group(3)))
        g_dlsch_round2.labels(rnti=rnti).set(int(m.group(4)))
        g_dlsch_round3.labels(rnti=rnti).set(int(m.group(5)))
        g_dlsch_errors.labels(rnti=rnti).set(int(m.group(6)))
        try:
            raw = m.group(7)
            snr = float(raw.split("+")[0]) if "+" in raw else float(raw)
            g_snr_dl.labels(rnti=rnti).set(snr)
        except ValueError:
            pass
        g_bler_dl.labels(rnti=rnti).set(float(m.group(8)))
        lines_parsed.inc()
        return

    m = RE_ULSCH.search(line)
    if m:
        rnti = m.group(1)
        g_ulsch_round1.labels(rnti=rnti).set(int(m.group(3)))
        g_ulsch_round2.labels(rnti=rnti).set(int(m.group(4)))
        g_ulsch_round3.labels(rnti=rnti).set(int(m.group(5)))
        g_ulsch_errors.labels(rnti=rnti).set(int(m.group(6)))
        g_bler_ul.labels(rnti=rnti).set(float(m.group(7)))
        lines_parsed.inc()
        return

    m = RE_MAC.search(line)
    if m:
        rnti, tx, rx = m.group(1), int(m.group(2)), int(m.group(3))
        g_mac_tx_bytes.labels(rnti=rnti).set(tx)
        g_mac_rx_bytes.labels(rnti=rnti).set(rx)
        lines_parsed.inc()
        return


def find_gnb_pod(v1):
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=POD_LABEL_SELECTOR)
    for p in pods.items:
        if p.status.phase == "Running":
            return p.metadata.name
    return None


def tail_loop():
    config.load_incluster_config()
    v1 = client.CoreV1Api()
    while True:
        pod_name = find_gnb_pod(v1)
        if not pod_name:
            log.info("No running gNB pod found yet, retrying in 10s")
            time.sleep(10)
            continue
        log.info(f"Streaming logs from pod: {pod_name}")
        try:
            w = watch.Watch()
            for line in w.stream(
                v1.read_namespaced_pod_log,
                name=pod_name,
                namespace=NAMESPACE,
                follow=True,
                _preload_content=False,
            ):
                parse_line(line if isinstance(line, str) else line.decode("utf-8", errors="ignore"))
        except Exception as e:
            log.warning(f"Log stream ended/errored ({e}), reconnecting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    start_http_server(9091)
    log.info("RAN exporter listening on :9091/metrics")
    t = threading.Thread(target=tail_loop, daemon=True)
    t.start()
    while True:
        time.sleep(3600)
