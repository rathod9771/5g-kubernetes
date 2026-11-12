#!/usr/bin/env python3
"""Real Open5GS NGAP recovery probe on a disposable GitHub-hosted Docker network.

No UE, Kubernetes, Longhorn, OSM, image publishing, or HP system access.
A failure at any evidence gate fails the CI job. Metrics count *real logs*.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback

NETWORK = "oai-ngap-stage3-ci"
CU = "oai-ngap-stage3-cu"
AMF = "oai-ngap-stage3-amf"
NRF = "oai-ngap-stage3-nrf"
NET_SUBNET = "172.29.73.0/24"
CU_IP = "172.29.73.20"
AMF_IP = "172.29.73.10"
NRF_IP = "172.29.73.11"
CORE_IMAGE = "gradiant/open5gs:2.7.6"
RETRY_PATTERN = re.compile(r"Re-dial AMF", re.IGNORECASE)
FAIL_PATTERN = re.compile(
    r"Received unsuccessful result for SCTP association \((?:3|0)\)",
    re.IGNORECASE,
)
SETUP_PATTERN = re.compile(r"Received NGSetupResponse from AMF", re.IGNORECASE)
REQUEST_PATTERN = re.compile(r"Send NGSetupRequest to AMF", re.IGNORECASE)


class Stage3Error(Exception):
    pass


def sh(*args, timeout=60, check=True):
    result = subprocess.run(
        [str(x) for x in args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout, check=False,
    )
    if check and result.returncode:
        raise Stage3Error(f"Command failed ({result.returncode}): {args!r}\n{result.stdout[-4500:]}")
    return result.stdout


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def count(pattern, data):
    return len(pattern.findall(data))


class Runner:
    def __init__(self, root, image, conf):
        self.root = root
        self.image = image
        self.conf = conf
        self.records = []
        self.root.mkdir(parents=True, exist_ok=True)
        self.started_at = time.monotonic()

    def note(self, phase, result, **fields):
        record = {"timestamp_utc": utc(), "elapsed_s": round(time.monotonic() - self.started_at, 1),
                  "phase": phase, "result": result, **fields}
        self.records.append(record)
        print(json.dumps(record), flush=True)
        (self.root / "test-results.json").write_text(
            json.dumps(self.records, indent=2) + "\n", encoding="utf-8"
        )

    def logs(self, name):
        return sh("docker", "logs", name, timeout=20, check=False)

    def capture(self, label):
        for container in (CU, AMF, NRF):
            (self.root / f"{label}-{container}.log").write_text(
                self.logs(container), encoding="utf-8")
        (self.root / f"{label}-containers.txt").write_text(
            sh("docker", "ps", "-a", "--filter", f"network={NETWORK}",
               "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}",
               check=False), encoding="utf-8")
        (self.root / f"{label}-sctp.txt").write_text(
            sh("docker", "exec", CU, "cat", "/proc/net/sctp/assocs", timeout=15, check=False),
            encoding="utf-8")

    def alive(self, name):
        return sh("docker", "inspect", "--format", "{{.State.Running}}", name,
                  check=False).strip() == "true"

    def must_live(self, name):
        if not self.alive(name):
            raise Stage3Error(
                f"{name} exited unexpectedly. Last 10000 log chars:\n{self.logs(name)[-10000:]}")

    def await_cu(self, phase, predicate, timeout):
        end = time.monotonic() + timeout
        last = ""
        while time.monotonic() < end:
            self.must_live(CU)
            last = self.logs(CU)
            if predicate(last):
                self.note(phase, "PASS",
                          retries=count(RETRY_PATTERN, last),
                          failures=count(FAIL_PATTERN, last),
                          ng_setup_requests=count(REQUEST_PATTERN, last),
                          ng_setup_responses=count(SETUP_PATTERN, last))
                self.capture(phase)
                return last
            time.sleep(3)
        self.capture(phase + "-FAILED")
        raise Stage3Error(
            f"TIMEOUT {phase} after {timeout}s; "
            f"retry={count(RETRY_PATTERN, last)}, "
            f"fail={count(FAIL_PATTERN, last)}, "
            f"NGSetupRsp={count(SETUP_PATTERN, last)}\n"
            f"CU tail:\n{last[-8500:]}\nAMF tail:\n{self.logs(AMF)[-2500:]}")

    def boot(self):
        # Defensive remove of only explicitly named CI objects.
        for name in (CU, AMF, NRF):
            sh("docker", "rm", "-f", name, timeout=30, check=False)
        sh("docker", "network", "rm", NETWORK, timeout=30, check=False)
        sh("docker", "network", "create", "--driver", "bridge",
           "--subnet", NET_SUBNET, NETWORK, timeout=30)
        self.note("T0-network", "PASS", cidr=NET_SUBNET)

        # All Open5GS processes and CU are on the same isolated Docker bridge.
        # NRF stays running throughout. AMF starts as a container without an
        # AMF daemon, preserving its IP while SCTP fails, then is enabled.
        sh("docker", "run", "-d", "--name", NRF,
           "--network", NETWORK, "--ip", NRF_IP,
           "-v", f"{self.conf / 'nrf.yaml'}:/tmp/nrf.yaml:ro",
           "--entrypoint", "/bin/sh", CORE_IMAGE,
           "-ec", "exec open5gs-nrfd -c /tmp/nrf.yaml", timeout=90)
        sh("docker", "run", "-d", "--name", AMF,
           "--network", NETWORK, "--ip", AMF_IP,
           "-v", f"{self.conf / 'amf.yaml'}:/tmp/amf.yaml:ro",
           "--entrypoint", "/bin/sh", CORE_IMAGE,
           "-ec", 'while [ ! -f /tmp/stage3-enable-amf ]; do sleep 1; done; exec open5gs-amfd -c /tmp/amf.yaml',
           timeout=90)
        self.must_live(AMF)
        self.must_live(NRF)

        sh("docker", "run", "-d", "--name", CU,
           "--network", NETWORK, "--ip", CU_IP,
           "-v", f"{self.conf / 'cu.conf'}:/opt/oai-gnb/etc/gnb.conf:ro",
           self.image, timeout=90)
        self.note("T0-cu-start", "PASS", image=self.image)
        self.must_live(CU)
        (self.root / "cu-image-id.txt").write_text(
            sh("docker", "image", "inspect", "--format", "{{.Id}}", self.image),
            encoding="utf-8")
        (self.root / "open5gs-image-id.txt").write_text(
            sh("docker", "image", "inspect", "--format", "{{.Id}}", CORE_IMAGE),
            encoding="utf-8")

    def enable_amf(self):
        self.must_live(AMF)
        sh("docker", "exec", AMF, "/bin/sh", "-c",
           "touch /tmp/stage3-enable-amf", timeout=30)

    def run(self):
        self.boot()
        self.await_cu("T1-initial-SCTP-unreachable",
                      lambda s: count(FAIL_PATTERN, s) >= 1, 95)
        self.await_cu("T2-two-redials-with-AMF-offline",
                      lambda s: count(RETRY_PATTERN, s) >= 2
                      and count(FAIL_PATTERN, s) >= 2, 120)
        self.note("T3-enable-Open5GS-AMF", "PASS")
        self.enable_amf()
        self.await_cu("T4-first-real-NGSetupResponse",
                      lambda s: count(SETUP_PATTERN, s) >= 1
                      and count(REQUEST_PATTERN, s) >= 1, 135)
        self.must_live(AMF)
        self.must_live(NRF)

        # This test requires a *new* NGSetupResponse, not log replay of T4.
        before = self.logs(CU)
        base_rsp = count(SETUP_PATTERN, before)
        base_redial = count(RETRY_PATTERN, before)
        sh("docker", "exec", AMF, "/bin/sh", "-c",
           "rm -f /tmp/stage3-enable-amf", timeout=30)
        sh("docker", "stop", "--time", "5", AMF, timeout=30)
        sh("docker", "start", AMF, timeout=30)
        self.must_live(AMF)
        self.note("T5-AMF-stopped-and-restarted-disabled", "PASS",
                  prior_responses=base_rsp, prior_redials=base_redial)
        self.await_cu("T5-CU-detects-AMF-loss-and-redials",
                      lambda s: count(RETRY_PATTERN, s) > base_redial, 125)

        self.enable_amf()
        self.await_cu("T6-real-NGSetupResponse-after-AMF-restart",
                      lambda s: count(SETUP_PATTERN, s) > base_rsp, 135)
        self.must_live(CU)
        self.must_live(AMF)
        self.must_live(NRF)

        # SCTP association is kernel state; do not confuse ST=3 here with
        # OAI's separate SCTP_STATE_UNREACHABLE (enum value 3).
        assoc = sh("docker", "exec", CU, "cat", "/proc/net/sctp/assocs",
                   timeout=15, check=False)
        (self.root / "T7-final-sctp-associations.txt").write_text(
            assoc, encoding="utf-8")
        if AMF_IP not in assoc:
            raise Stage3Error("No matching AMF peer in CU kernel SCTP association table.")
        restarts = sh("docker", "inspect", "--format", "{{.RestartCount}}", CU).strip()
        if restarts != "0":
            raise Stage3Error(f"CU restarted unexpectedly: {restarts}")
        last = self.logs(CU)
        all_retries = count(RETRY_PATTERN, last)
        if all_retries < 3:
            raise Stage3Error("Fewer than three observed redials across all test phases")
        # Soft bound to catch retry storms; exact timer scheduling/counters are
        # not directly exported by OAI and require separate instrumentation.
        if all_retries > 20:
            raise Stage3Error("Excessive redials observed; investigate timer storms")
        self.note("T7-live-association-and-basic-retry-bounds", "PASS",
                  retry_count=all_retries,
                  ng_setup_responses=count(SETUP_PATTERN, last),
                  cu_restart_count=int(restarts),
                  amf_kernel_sctp_association_present=True)
        self.capture("final")

    def close(self):
        try:
            self.capture("cleanup")
        except Exception as exc:
            self.note("cleanup-capture", "WARN", error=str(exc))
        for name in (CU, AMF, NRF):
            sh("docker", "rm", "-f", name, timeout=30, check=False)
        sh("docker", "network", "rm", NETWORK, timeout=30, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conf", required=True)
    args = ap.parse_args()
    runner = Runner(pathlib.Path(args.out).resolve(),
                    args.image, pathlib.Path(args.conf).resolve())
    try:
        runner.run()
        runner.note("STAGE3", "PASS", scope="real SCTP/NGAP; no UE or DU")
        return 0
    except Exception as exc:
        runner.note("STAGE3", "FAIL", error=str(exc))
        (runner.root / "exception.txt").write_text(traceback.format_exc(),
                                                   encoding="utf-8")
        print(f"STAGE3 FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.close()


if __name__ == "__main__":
    sys.exit(main())
