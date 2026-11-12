#!/usr/bin/env python3
"""Isolated OAI CU <-> real FlexRIC nearRT-RIC E2 Setup.

GitHub-hosted only; no UE, no HP/K8s, no OSM, no subscriber data.
E2 Setup is NOT KPM indications or an operational RC control proof.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback

NET = "oai-ngap-stage4g-e2"
GATEWAY = "172.29.75.1"
CU_IP = "172.29.75.20"
AMF_IP = "172.29.75.10"
NRF_IP = "172.29.75.11"
NAMES = {"cu": "oai-ngap-stage4g-cu",
         "amf": "oai-ngap-stage4g-amf",
         "nrf": "oai-ngap-stage4g-nrf"}
CORE_IMAGE = "gradiant/open5gs:2.7.6"
NGAP_RSP = re.compile("Received NGSetupResponse from AMF", re.I)
E2_SETUP_REQ = re.compile(r"(?:E2[_ -]?SETUP[_ -]?REQUEST|E2[_ -]?Setup[_ -]?Req)", re.I)
E2_SETUP_RSP = re.compile(r"(?:E2[_ -]?SETUP[_ -]?RESPONSE|E2[_ -]?Setup[_ -]?Resp)", re.I)


class CheckError(RuntimeError):
    pass


def call(*argv, timeout=60, check=True):
    p = subprocess.run(list(map(str, argv)), text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if check and p.returncode:
        raise CheckError(f"{argv!r}: rc={p.returncode}\n{p.stdout[-4000:]}")
    return p.stdout


class Trial:
    def __init__(self, args):
        self.args = args
        self.out = pathlib.Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.records = []
        self.ric = None
        self.ric_output = None
        self.start = time.monotonic()

    def evidence(self, phase, result, **other):
        x = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "elapsed_s": round(time.monotonic() - self.start, 1),
             "phase": phase, "result": result, **other}
        self.records.append(x)
        self.out.joinpath("results.json").write_text(json.dumps(self.records, indent=2) + "\n")
        print(json.dumps(x), flush=True)

    def logs(self, which):
        return call("docker", "logs", NAMES[which], check=False, timeout=20)

    def ric_log(self):
        if self.ric_output is not None:
            self.ric_output.flush()
        p = self.out / "near-rt-ric.log"
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    def snapshot(self, name):
        for key in NAMES:
            self.out.joinpath(f"{name}-{key}.log").write_text(self.logs(key))
        self.out.joinpath(f"{name}-ric.log").write_text(self.ric_log())
        self.out.joinpath(f"{name}-cu-sctp.txt").write_text(
            call("docker", "exec", NAMES["cu"], "cat",
                 "/proc/net/sctp/assocs", check=False, timeout=12))

    def check_alive(self):
        for name in NAMES.values():
            x = call("docker", "inspect", "--format", "{{.State.Running}}", name, check=False)
            if x.strip() != "true":
                raise CheckError(f"{name} not running")
        if self.ric is None or self.ric.poll() is not None:
            raise CheckError(f"FlexRIC exited (rc={None if self.ric is None else self.ric.returncode}):\n" +
                             self.ric_log()[-5500:])

    def run(self):
        for n in NAMES.values():
            call("docker", "rm", "-f", n, check=False, timeout=20)
        call("docker", "network", "rm", NET, check=False, timeout=20)
        call("docker", "network", "create", "--driver", "bridge",
             "--subnet", "172.29.75.0/24", "--gateway", GATEWAY, NET)
        self.evidence("T0-isolated-bridge", "PASS")
        # RIC built from the OAI 2026.w13-pinned FlexRIC submodule, which uses
        # the same E2AP and E2SM-KPM versions as the E2-enabled CU.
        self.ric_output = (self.out / "near-rt-ric.log").open("w", encoding="utf-8")
        self.ric = subprocess.Popen([self.args.ric_binary, "-a", GATEWAY],
                                    stdout=self.ric_output,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(3)
        if self.ric.poll() is not None:
            raise CheckError("FlexRIC RIC exited early:\n" + self.ric_log()[-4500:])
        self.evidence("T1-pinned-FlexRIC-nearRT-RIC-started", "PASS",
                      ric_ip=GATEWAY, e2_port=36421)

        config_dir = self.out / "configs"
        config_dir.mkdir(exist_ok=True)
        s3 = pathlib.Path(self.args.stage3)
        for key in ("amf", "nrf"):
            config = (s3 / f"{key}.yaml").read_text()
            config = config.replace("172.29.73.", "172.29.75.")
            (config_dir / f"{key}.yaml").write_text(config)
        conf = (s3 / "cu.conf").read_text().replace("172.29.73.", "172.29.75.")
        if "e2_agent" in conf:
            raise CheckError("Unexpected previous E2 configuration block")
        conf += f'''
e2_agent = {{
  near_ric_ip_addr = "{GATEWAY}";
  sm_dir = "/usr/local/lib/flexric/";
}};
'''
        (config_dir / "cu.conf").write_text(conf)
        for key, exe in (("nrf", "nrfd"), ("amf", "amfd")):
            call("docker", "run", "-d", "--name", NAMES[key],
                 "--network", NET, "--ip", NRF_IP if key == "nrf" else AMF_IP,
                 "-v", str(config_dir / f"{key}.yaml") + f":/tmp/{key}.yaml:ro",
                 "--entrypoint", "/bin/sh", CORE_IMAGE, "-ec",
                 f"exec open5gs-{exe} -c /tmp/{key}.yaml")
        call("docker", "run", "-d", "--name", NAMES["cu"], "--network", NET,
             "--ip", CU_IP, "--cpus", "1.5", "--memory", "3g",
             "-v", str(config_dir / "cu.conf") + ":/opt/oai-gnb/etc/gnb.conf:ro",
             "--entrypoint", "/opt/oai-gnb/bin/nr-softmodem",
             self.args.cu_image, "-O", "/opt/oai-gnb/etc/gnb.conf")
        self.evidence("T2-E2-enabled-CU-started", "PASS", image=self.args.cu_image)

        deadline = time.monotonic() + 140
        while time.monotonic() < deadline:
            self.check_alive()
            cu = self.logs("cu")
            ric = self.ric_log()
            sctp = call("docker", "exec", NAMES["cu"], "cat", "/proc/net/sctp/assocs",
                        check=False, timeout=12)
            request_seen = bool(E2_SETUP_REQ.search(cu + "\n" + ric))
            response_seen = bool(E2_SETUP_RSP.search(cu + "\n" + ric))
            ngap_seen = bool(NGAP_RSP.search(cu))
            e2_sctp = GATEWAY in sctp
            if request_seen and response_seen and ngap_seen and e2_sctp:
                self.evidence("T3-real-E2-Setup-and-NGAP", "PASS",
                              e2_setup_request=True, e2_setup_response=True,
                              e2_kernel_sctp=True, real_ngap=True)
                self.snapshot("T3-success")
                return
            time.sleep(3)
        self.snapshot("T3-timeout")
        raise CheckError(f"Timeout E2 or NGAP: request={request_seen} response={response_seen} "
                         f"ngap={ngap_seen} sctp={e2_sctp}\n" +
                         "CU tail:\n" + self.logs("cu")[-2500:] +
                         "\nRIC tail:\n" + self.ric_log()[-2500:])

    def close(self):
        try:
            self.snapshot("cleanup")
        except Exception:
            pass
        for n in NAMES.values():
            call("docker", "rm", "-f", n, check=False, timeout=20)
        call("docker", "network", "rm", NET, check=False, timeout=20)
        if self.ric is not None and self.ric.poll() is None:
            self.ric.terminate()
            try:
                self.ric.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.ric.kill()
        if self.ric_output is not None:
            self.ric_output.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cu-image", required=True)
    p.add_argument("--ric-binary", required=True)
    p.add_argument("--stage3", required=True)
    p.add_argument("--out", required=True)
    t = Trial(p.parse_args())
    try:
        t.run()
        t.evidence("STAGE4G", "PASS", scope="E2AP Setup + Open5GS NGAP, no UE or KPM indications")
        return 0
    except Exception as e:
        t.evidence("STAGE4G", "FAIL", error=str(e))
        (t.out / "exception.txt").write_text(traceback.format_exc())
        print("FAIL:", str(e), file=sys.stderr)
        return 1
    finally:
        t.close()


if __name__ == "__main__":
    sys.exit(main())
