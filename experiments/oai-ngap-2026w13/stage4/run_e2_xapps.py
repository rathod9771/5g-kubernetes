#!/usr/bin/env python3
"""Run the pinned FlexRIC KPM/RC monitoring xApps against a real CU E2 Setup.

No UE or PDU is started. Evidence of an xApp process or E2 Setup alone is
not evidence of RIC INDICATIONs or real E2SM-RC control affecting RAN state.
The script retains all logs and reports those outcomes separately.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
from types import SimpleNamespace

from run_e2_setup import Trial, GATEWAY

def run_xapp(trial, label, binary):
    out = trial.out / f"{label}.log"
    env = os.environ.copy()
    env["XAPP_DURATION"] = "20"
    env["DB_DIR"] = str(trial.out)
    # Use the same pinned RIC address in the isolated hosted-runner bridge.
    with out.open("w", encoding="utf-8") as f:
        try:
            proc = subprocess.run(
                ["stdbuf", "-oL", "-eL", binary, "-a", GATEWAY],
                env=env, stdout=f, stderr=subprocess.STDOUT, timeout=55,
                check=False,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
    output = out.read_text(encoding="utf-8", errors="replace")
    # Record evidence without claiming indications from a mere text match
    # in a log message that describes supported service model names.
    diagnostics = {
        "exit_code": code,
        "log_characters": len(output),
        "log_tail": output[-2000:],
        "has_subscription_activity": bool(re.search(
            r"SUBSCRIPTION|subscribe|subscrib", output, re.I)),
        "has_indication_text": bool(re.search(
            r"RIC[_ -]?INDICATION(?: rx| received| message)|KPM indication received",
            output, re.I)),
    }
    trial.evidence(label, "PASS" if code == 0 else "FAIL", **diagnostics)
    return diagnostics

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cu-image", required=True)
    ap.add_argument("--ric-binary", required=True)
    ap.add_argument("--stage3", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--xapp-kpm", required=True)
    ap.add_argument("--xapp-rc", required=True)
    a = ap.parse_args()
    trial = Trial(SimpleNamespace(cu_image=a.cu_image,
                                  ric_binary=a.ric_binary,
                                  stage3=a.stage3,
                                  out=a.out,
                                  capture_gdb=False))
    try:
        trial.run()   # Requires actual E2AP request, response and Open5GS NG Setup.
        trial.evidence("T0-real-E2-setup", "PASS", source="wire capture")
        kpm=run_xapp(trial,"T1-KPM-xApp",a.xapp_kpm)
        trial.check_alive()
        rc=run_xapp(trial,"T2-RC-monitor-xApp",a.xapp_rc)
        trial.check_alive()
        report={
          "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "scope": "H-CRAN OAI E2 xApp smoke without UE",
          "e2_setup": "PASS",
          "kpm_xapp_exit": kpm["exit_code"],
          "rc_monitor_xapp_exit": rc["exit_code"],
          "kpm_indications": "NOT_VERIFIED: no UE",
          "rc_control_effect": "NOT_VERIFIED: no UE and monitoring xApp",
          "production_release": "NO_GO",
        }
        trial.out.joinpath("xapp-outcomes.json").write_text(json.dumps(report,indent=2)+"\n")
        if kpm["exit_code"] != 0 or rc["exit_code"] != 0:
            raise RuntimeError("KPM or RC monitor failed; see xApp logs")
        trial.evidence("STAGE4I", "PASS",
                       scope="E2 Setup and xApp process smoke only, no UE indications")
        return 0
    except Exception as e:
        trial.evidence("STAGE4I", "FAIL", error=str(e))
        trial.out.joinpath("exception-xapp.txt").write_text(traceback.format_exc())
        return 1
    finally:
        trial.close()

if __name__=="__main__":
    sys.exit(main())
