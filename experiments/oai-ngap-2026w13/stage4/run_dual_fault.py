#!/usr/bin/env python3
"""Real bounded AMF stop/restart injections with both F1 DUs kept alive.

Uses the already proven Stage4D hosted-runner lab setup. No UE or live host.
Does not modify the production cluster or invoke OSM/kubectl.
"""
import argparse
import json
import pathlib
import re
import sys
import time
import traceback

import run_dual_f1 as lab

REDIAL = re.compile(r"Re-dial AMF", re.I)
SCTP_FAIL = re.compile(r"Received unsuccessful result for SCTP association", re.I)


class FaultTrial(lab.Trial):
    def counts(self):
        cu = self.logs("cu")
        return {
            "ng_setup_responses": len(lab.NGAP.findall(cu)),
            "redials": len(REDIAL.findall(cu)),
            "sctp_failures": len(SCTP_FAIL.findall(cu)),
            "f1_macro_requests": len(lab.F1REQ.findall(cu)),
            "f1_small_requests": len(lab.F1REQ_SMALL.findall(cu)),
        }

    def assert_two_f1_associations(self, stage, require_amf=True):
        self.require_live("cu", "macro", "small", "nrf")
        rows = lab.run("docker", "exec", lab.NAMES["cu"], "cat",
                       "/proc/net/sctp/assocs", timeout=15)
        for peer in ("macro", "small") + (("amf",) if require_amf else ()):
            if lab.IP[peer] not in rows:
                raise lab.TestFailure(f"{stage}: missing SCTP peer {peer}={lab.IP[peer]}")
        self.out.joinpath(f"{stage}-cu-sctp.txt").write_text(rows)

    def wait_predicate(self, label, predicate, seconds, require_amf=False):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.assert_two_f1_associations(label + "-poll", require_amf=require_amf)
            metrics = self.counts()
            if predicate(metrics):
                self.event(label, "PASS", **metrics)
                self.capture(label)
                return metrics
            time.sleep(3)
        self.capture(label + "-TIMEOUT")
        raise lab.TestFailure(f"{label} timeout ({seconds}s): last counts={self.counts()}")

    def stable_f1_counts(self, expected, label):
        counts = self.counts()
        if counts["f1_macro_requests"] != expected["f1_macro_requests"]:
            raise lab.TestFailure(label + ": macro DU F1 reset/re-setup observed")
        if counts["f1_small_requests"] != expected["f1_small_requests"]:
            raise lab.TestFailure(label + ": small DU F1 reset/re-setup observed")
        for name in ("cu", "macro", "small"):
            restarts = lab.run("docker", "inspect", "--format", "{{.RestartCount}}",
                               lab.NAMES[name]).strip()
            if restarts != "0":
                raise lab.TestFailure(f"{label}: {name} has {restarts} Docker restarts")

    def test(self):
        self.prepare()
        self.await_logs("T0-initial-NGSetup", lab.NGAP, 100, ("nrf", "amf", "cu"))
        configs = self.out / "configs"
        for name, id_, req, resp in (
            ("macro", 100, lab.F1REQ, lab.F1RESP),
            ("small", 101, lab.F1REQ_SMALL, lab.F1RESP_SMALL),
        ):
            lab.run("docker", "run", "-d", "--name", lab.NAMES[name],
                    "--network", lab.NET, "--ip", lab.IP[name],
                    "--cpus", "1.25", "--memory", "2g",
                    "-v", str(configs / f"{name}.conf") + ":/opt/oai-gnb/etc/gnb.conf:ro",
                    "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
                    self.args.du_image, "-O", "/opt/oai-gnb/etc/gnb.conf", "--rfsim")
            self.event(f"T0-{name}-DU-{id_}-started", "PASS")
            self.await_logs(f"T0-{name}-F1-Request", req, 120, ("cu", name, "amf"))
            self.await_logs(f"T0-{name}-F1-Response", resp, 45, ("cu", name, "amf"))
        self.assert_two_f1_associations("baseline")
        initial = self.counts()
        if initial["f1_macro_requests"] != 1 or initial["f1_small_requests"] != 1:
            raise lab.TestFailure(f"F1 baseline is not clean: {initial}")
        if initial["ng_setup_responses"] < 1:
            raise lab.TestFailure("Missing initial real Open5GS NG Setup response")
        self.event("T0-baseline-all-three-SCTP-peers", "PASS", **initial)
        self.capture("T0-baseline")

        for attempt in range(1, 4):
            before = self.counts()
            self.stable_f1_counts(initial, f"cycle-{attempt}-before")
            lab.run("docker", "stop", "--time", "5", lab.NAMES["amf"], timeout=30)
            self.event(f"T{attempt}-AMF-stopped", "PASS")
            self.assert_two_f1_associations(f"cycle-{attempt}-AMF-down", require_amf=False)
            self.wait_predicate(
                f"T{attempt}-observed-automatic-redial",
                lambda n: n["redials"] > before["redials"],
                80, require_amf=False)
            self.stable_f1_counts(initial, f"cycle-{attempt}-AMF-offline")
            lab.run("docker", "start", lab.NAMES["amf"], timeout=30)
            self.require_live("amf")
            self.event(f"T{attempt}-AMF-restarted", "PASS")
            self.wait_predicate(
                f"T{attempt}-real-NGSetup-recovered",
                lambda n: n["ng_setup_responses"] > before["ng_setup_responses"],
                120, require_amf=True)
            self.assert_two_f1_associations(f"cycle-{attempt}-after-recovery")
            self.stable_f1_counts(initial, f"cycle-{attempt}-recovered")
            self.event(f"T{attempt}-both-DUs-retained-F1", "PASS", **self.counts())
            self.capture(f"T{attempt}-recovery-final")

        self.assert_two_f1_associations("final")
        self.stable_f1_counts(initial, "final")
        final = self.counts()
        if final["ng_setup_responses"] < initial["ng_setup_responses"] + 3:
            raise lab.TestFailure("Fewer than three new successful NG Setup responses")
        self.event("STAGE4E", "PASS", cycles=3,
                   final_metrics=final,
                   scope="three AMF restarts with both F1-C DUs retained; no UE/F1-U/E2")
        self.capture("final")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rendered", required=True)
    ap.add_argument("--stage3", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cu-image", required=True)
    ap.add_argument("--du-image", required=True)
    trial = FaultTrial(ap.parse_args())
    try:
        trial.test()
        return 0
    except Exception as e:
        trial.event("STAGE4E", "FAIL", error=str(e))
        trial.out.joinpath("error.txt").write_text(traceback.format_exc())
        print(str(e), file=sys.stderr)
        return 1
    finally:
        trial.close()


if __name__ == "__main__":
    sys.exit(main())
