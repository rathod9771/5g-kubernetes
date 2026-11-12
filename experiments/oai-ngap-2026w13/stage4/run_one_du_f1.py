#!/usr/bin/env python3
"""Real one-DU F1-C SCTP/F1AP test in disposable GitHub Docker bridge.

No second DU, no UE, no subscriber identifiers, no Kubernetes/OSM, no HP access.
Proves only CU-side request/response log events for small DU ID 101.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time
import traceback

import yaml

NET = "oai-ngap-stage4c-f1"
PREFIX = "oai-ngap-stage4c-"
IP = {"amf": "172.29.74.10", "nrf": "172.29.74.11",
      "cu": "172.29.74.20", "small": "172.29.74.30"}
NAMES = {x: PREFIX + x for x in IP}
CORE = "gradiant/open5gs:2.7.6"
NGAP = re.compile(r"Received NGSetupResponse from AMF", re.I)
F1REQ = re.compile(r"Received F1 Setup Request from gNB_DU 101", re.I)
F1RESP = re.compile(r"DU 101 .*sending F1 Setup Response", re.I)


class TestFailure(RuntimeError):
    pass


def run(*args, check=True, timeout=90):
    proc = subprocess.run([str(x) for x in args], check=False, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)
    if check and proc.returncode:
        raise TestFailure(f"command failed: {args!r} (rc={proc.returncode})\n{proc.stdout[-3000:]}")
    return proc.stdout


def configmap(path, name):
    with path.open() as f:
        docs = list(yaml.safe_load_all(f))
    matches = [d for d in docs if isinstance(d, dict) and d.get("kind") == "ConfigMap"
               and d.get("metadata", {}).get("name") == name]
    if len(matches) != 1:
        raise TestFailure(f"expected one ConfigMap {name}, found {len(matches)}")
    return matches[0]["data"]["gnb.conf"]


class Trial:
    def __init__(self, args):
        self.args = args
        self.out = pathlib.Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.begin = time.monotonic()
        self.events = []

    def event(self, stage, result, **detail):
        self.events.append({"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "elapsed_seconds": round(time.monotonic() - self.begin, 2),
                            "stage": stage, "result": result, **detail})
        self.out.joinpath("results.json").write_text(json.dumps(self.events, indent=2) + "\n")
        print(json.dumps(self.events[-1]), flush=True)

    def logs(self, name):
        return run("docker", "logs", NAMES[name], check=False, timeout=20)

    def alive(self, name):
        return run("docker", "inspect", "--format", "{{.State.Running}}",
                   NAMES[name], check=False, timeout=20).strip() == "true"

    def require_live(self, *names):
        for name in names:
            if not self.alive(name):
                raise TestFailure(f"{name} exited; last logs:\n{self.logs(name)[-4500:]}")

    def capture(self, stage):
        for name in NAMES:
            self.out.joinpath(f"{stage}-{name}.log").write_text(self.logs(name))
        self.out.joinpath(f"{stage}-containers.txt").write_text(
            run("docker", "ps", "-a", "--filter", f"network={NET}",
                "--format", "{{.Names}} {{.Status}} {{.Image}}", check=False))
        for name in ("cu", "small"):
            self.out.joinpath(f"{stage}-{name}-sctp.txt").write_text(
                run("docker", "exec", NAMES[name], "cat",
                    "/proc/net/sctp/assocs", check=False, timeout=10))

    def await_logs(self, stage, regex, timeout_sec, expected_live):
        cutoff = time.monotonic() + timeout_sec
        while time.monotonic() < cutoff:
            self.require_live(*expected_live)
            cu_log = self.logs("cu")
            if regex.search(cu_log):
                self.event(stage, "PASS")
                self.capture(stage)
                return
            time.sleep(3)
        self.capture(stage + "-TIMEOUT")
        raise TestFailure(
            f"TIMEOUT {stage}: {regex.pattern}; "
            f"CU tail={self.logs('cu')[-4500:]}\n"
            f"small tail={self.logs('small')[-4500:]}")

    def prepare(self):
        for name in NAMES.values():
            run("docker", "rm", "-f", name, check=False, timeout=20)
        run("docker", "network", "rm", NET, check=False, timeout=20)
        run("docker", "network", "create", "--driver", "bridge",
            "--subnet", "172.29.74.0/24", NET)
        configs = self.out / "configs"
        configs.mkdir(exist_ok=True)
        stage3 = pathlib.Path(self.args.stage3)
        for src in ("amf", "nrf"):
            content = (stage3 / f"{src}.yaml").read_text()
            content = content.replace("172.29.73.", "172.29.74.")
            content = content.replace('mcc: "001"', 'mcc: "208"')
            content = content.replace('mnc: "01"', 'mnc: "93"')
            if src == "amf":
                content = content.replace('        - sst: 1', '        - sst: 1\n          sd: "010203"')
                if 'sd: "010203"' not in content:
                    raise TestFailure("AMF test slice differentiator not added")
            (configs / f"{src}.yaml").write_text(content)

        rendered = pathlib.Path(self.args.rendered)
        cu_conf = configmap(rendered / "cu.yaml", "hcran-oai-cu-config")
        cu_conf = cu_conf.replace("POD_IP", IP["cu"]).replace("AMF_IP", IP["amf"])
        du_conf = configmap(rendered / "small.yaml", "hcran-oai-small-config")
        du_conf = du_conf.replace("POD_IP", IP["small"]).replace("CU_IP", IP["cu"])
        if 'gNB_DU_ID = 101;' not in du_conf:
            raise TestFailure("small DU ID must equal 101")
        if "172.29.74.20" not in du_conf or "172.29.74.10" not in cu_conf:
            raise TestFailure("placeholder substitution incomplete")
        (configs / "cu.conf").write_text(cu_conf)
        (configs / "small.conf").write_text(du_conf)
        self.event("T0-config-and-bridge", "PASS")

        for name, binary in (("nrf", "nrfd"), ("amf", "amfd")):
            run("docker", "run", "-d", "--name", NAMES[name],
                "--network", NET, "--ip", IP[name],
                "-v", str(configs / f"{name}.yaml") + f":/tmp/{name}.yaml:ro",
                "--entrypoint", "/bin/sh", CORE, "-ec",
                f"exec open5gs-{binary} -c /tmp/{name}.yaml")
        self.require_live("nrf", "amf")
        run("docker", "run", "-d", "--name", NAMES["cu"],
            "--network", NET, "--ip", IP["cu"],
            "--cpus", "1.5", "--memory", "3g",
            "-v", str(configs / "cu.conf") + ":/opt/oai-gnb/etc/gnb.conf:ro",
            "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
            self.args.cu_image, "-O", "/opt/oai-gnb/etc/gnb.conf")
        self.event("T0-open5gs-and-CU-start", "PASS")

    def test(self):
        self.prepare()
        self.await_logs("T1-CU-real-NGSetupResponse", NGAP, 101, ("nrf", "amf", "cu"))
        configs = self.out / "configs"
        run("docker", "run", "-d", "--name", NAMES["small"],
            "--network", NET, "--ip", IP["small"],
            "--cpus", "1.5", "--memory", "3g",
            "-v", str(configs / "small.conf") + ":/opt/oai-gnb/etc/gnb.conf:ro",
            "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
            self.args.du_image, "-O", "/opt/oai-gnb/etc/gnb.conf", "--rfsim")
        self.event("T2-small-DU-start", "PASS")
        self.await_logs("T3-F1SetupRequest-from-DU-101",
                        F1REQ, 120, ("cu", "small", "amf"))
        self.await_logs("T4-F1SetupResponse-to-DU-101",
                        F1RESP, 45, ("cu", "small", "amf"))
        self.require_live("cu", "small", "amf")
        self.capture("final")
        self.event("STAGE4C", "PASS", scope="real CU/small DU F1 Setup only; no UE")

    def close(self):
        try:
            self.capture("cleanup")
        except Exception as e:
            self.event("cleanup-capture", "WARN", error=str(e))
        for name in NAMES.values():
            run("docker", "rm", "-f", name, check=False, timeout=20)
        run("docker", "network", "rm", NET, check=False, timeout=20)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rendered", required=True)
    p.add_argument("--stage3", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cu-image", required=True)
    p.add_argument("--du-image", required=True)
    trial = Trial(p.parse_args())
    try:
        trial.test()
        return 0
    except Exception as error:
        trial.event("STAGE4C", "FAIL", error=str(error))
        trial.out.joinpath("error.txt").write_text(traceback.format_exc())
        print(str(error), file=sys.stderr)
        return 1
    finally:
        trial.close()


if __name__ == "__main__":
    sys.exit(main())
