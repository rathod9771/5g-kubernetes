#!/usr/bin/env python3
"""
Finds the currently-active OAI gNB pod's IP and writes it to a shared
file for the main UE container to read at startup.

Why this exists: the gNB's pod IP changes every time the RAN is
switched or the core redeploys (it has no stable Service -- see
docs/troubleshooting.md for why a plain Service doesn't work here:
H-CRAN runs two independent gNB pods, macro + small, sharing the same
component=gnb label, and load-balancing a real RF-simulator TCP
connection between two physically distinct cells breaks the UE's RRC
state). Discovering it fresh via the Kubernetes API on every UE pod
start, the same way this project's ran-exporter already does, avoids
ever hardcoding an IP that will inevitably go stale.

Selection: prefer a pod labeled cell-tier=macro (H-CRAN's primary
cell) if more than one gNB is running; otherwise take the first
Running pod labeled component=gnb.
"""
import os
import sys
import time

from kubernetes import client, config

NAMESPACE = os.environ.get("TARGET_NAMESPACE", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "/shared/gnb-ip.txt")
MAX_WAIT_SECONDS = int(os.environ.get("MAX_WAIT_SECONDS", "180"))
POLL_INTERVAL = 5


def find_gnb_ip(v1):
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector="component=gnb").items
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
        print(f"No running gNB pod found yet (component=gnb in ns={NAMESPACE}), "
              f"retrying in {POLL_INTERVAL}s ({waited}/{MAX_WAIT_SECONDS}s waited)")
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL

    print(f"ERROR: no running gNB pod found after {MAX_WAIT_SECONDS}s -- "
          f"is a RAN scenario actually deployed?", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
