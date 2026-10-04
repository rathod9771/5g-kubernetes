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

import json
import ssl
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


class PodReader:
    """Use the mounted Kubernetes service-account credentials; no pip at startup."""
    def list_namespaced_pod(self, namespace, label_selector):
        from types import SimpleNamespace
        if not namespace or not all(c.isalnum() or c == '-' for c in namespace):
            raise ValueError('Invalid discovery namespace')
        directory = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        host = os.environ['KUBERNETES_SERVICE_HOST']
        port = os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')
        # Service host is supplied by Kubernetes, and may be an IPv6 literal.
        if ':' in host:
            host = '[' + host + ']'
        query = urlencode({'labelSelector': label_selector})
        req = Request(f'https://{host}:{port}/api/v1/namespaces/{namespace}/pods?{query}',
                      headers={'Authorization': 'Bearer ' + (directory / 'token').read_text().strip()})
        with urlopen(req, context=ssl.create_default_context(cafile=str(directory / 'ca.crt')), timeout=10) as response:
            data = json.load(response)
        pods = []
        for pod in data['items']:
            pods.append(SimpleNamespace(
                status=SimpleNamespace(phase=pod.get('status', {}).get('phase'), pod_ip=pod.get('status', {}).get('podIP')),
                metadata=SimpleNamespace(name=pod['metadata']['name'], labels=pod['metadata'].get('labels', {}))))
        return SimpleNamespace(items=pods)


def main():
    v1 = PodReader()

    waited = 0
    while waited < MAX_WAIT_SECONDS:
        try:
            ip = find_gnb_ip(v1)
        except Exception:
            # Never print requests/headers or private HTTP response bodies.
            print('Kubernetes pod discovery unavailable; retrying safely', file=sys.stderr)
            ip = None
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
