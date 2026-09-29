#!/usr/bin/env python3
"""
C-RAN TEST VARIANT of discover_gnb.py. A separate copy so the shared,
working discovery script (and its ConfigMap oai-nr-ue-discovery-script)
stays untouched.

Finds the OAI C-RAN DU pod's IP and writes it to a shared file for the
UE container to read at startup. The selector is app=cran-oai-du (the
CU/DU-split DU, labeled component=du -- the shared script's
component=gnb selector would never match it). Override with the
GNB_LABEL_SELECTOR environment variable.

Why this exists: the gNB's pod IP changes every time the RAN is
switched or the core redeploys (it has no stable Service -- see
docs/troubleshooting.md for why a plain Service doesn't work here:
H-CRAN runs two independent gNB pods, macro + small, sharing the same
component=gnb label, and load-balancing a real RF-simulator TCP
connection between two physically distinct cells breaks the UE's RRC
state). Discovering it fresh via the Kubernetes API on every UE pod
start, the same way this project's ran-exporter already does, avoids
ever hardcoding an IP that will inevitably go stale.

Selection: the first Running pod matching the selector. (The
cell-tier=macro preference is kept from the original; it never matches
a CU/DU pod.)
"""
import os
import sys
import time

from kubernetes import client, config

NAMESPACE = os.environ.get("TARGET_NAMESPACE", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "/shared/gnb-ip.txt")
MAX_WAIT_SECONDS = int(os.environ.get("MAX_WAIT_SECONDS", "180"))
POLL_INTERVAL = 5
GNB_LABEL_SELECTOR = os.environ.get("GNB_LABEL_SELECTOR", "app=cran-oai-du")


def find_gnb_ip(v1):
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=GNB_LABEL_SELECTOR).items
    running = [p for p in pods if p.status.phase == "Running" and p.status.pod_ip]
    if not running:
        return None
    macro = [p for p in running if p.metadata.labels.get("cell-tier") == "macro"]
    chosen = macro[0] if macro else running[0]
    print(f"Selected gNB pod: {chosen.metadata.name} ({chosen.status.pod_ip})"
          f"{' [macro cell, preferred]' if macro else ''}")
    return chosen.status.pod_ip


def main():
    config.load_incluster_config()
    v1 = client.CoreV1Api()

    waited = 0
    while waited < MAX_WAIT_SECONDS:
        ip = find_gnb_ip(v1)
        if ip:
            with open(OUTPUT_FILE, "w") as f:
                f.write(ip)
            print(f"Wrote gNB IP {ip} to {OUTPUT_FILE}")
            return 0
        print(f"No running gNB pod found yet ({GNB_LABEL_SELECTOR} in ns={NAMESPACE}), "
              f"retrying in {POLL_INTERVAL}s ({waited}/{MAX_WAIT_SECONDS}s waited)")
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL

    print(f"ERROR: no running gNB pod found after {MAX_WAIT_SECONDS}s -- "
          f"is a RAN scenario actually deployed?", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
