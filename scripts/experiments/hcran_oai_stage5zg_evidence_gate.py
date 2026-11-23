#!/usr/bin/env python3
"""GET-only GitHub provenance audit of the actual OAI Stage5ZG 2x3-cycle run.

Passing this audit is *historical* recovered-service evidence, not a new
runtime trial or production certification. Never fetch raw logs or secrets.
"""
import argparse
import json
import os
from pathlib import Path
import urllib.request

REPO = "rathod9771/5g-kubernetes"
RUN_ID = 44457384676
PIN = "7fa911ece3b510b09351bf19c722007365ba47bd"
NEEDED = ("Live Stage5R protocol timeline plus genuine three-cycle Stage5P acceptance",
          "Stage5U verify full RFsim protocol trace and 9 negative evidence controls",
          "Stage5Y independently require both live AMF cell ingress categories per genuine recovery cycle")

def inspect(run, jobs, artifacts):
    if (run.get("id") != RUN_ID or run.get("head_sha") != PIN or
            run.get("status") != "completed" or run.get("conclusion") != "success" or
            run.get("event") != "push" or
            run.get("head_branch") != "experiment/hcran-stage5zg-sib1-message-handoff-20261119"):
        raise ValueError("Historical run provenance/status mismatch")
    good = []
    for trial in (1, 2):
        matched = [j for j in jobs if j.get("name", "").endswith(f"trial {trial}/2")]
        if len(matched) != 1:
            raise ValueError(f"Expected one distinct trial {trial}")
        job = matched[0]
        if job.get("conclusion") != "success":
            raise ValueError(f"Trial {trial} not successful")
        steps = job.get("steps", [])
        for needed in NEEDED:
            rows = [s for s in steps if s.get("name") == needed and s.get("conclusion") == "success"]
            if len(rows) != 1:
                raise ValueError(f"Trial {trial} acceptance gate missing: {needed}")
        good.append({"trial": trial, "job_id": job["id"], "required_acceptance_steps_passed": len(NEEDED)})
    expected = {"hcran-stage5y-repeatability-trial-1", "hcran-stage5y-repeatability-trial-2"}
    available = {a.get("name") for a in artifacts if not a.get("expired", True)}
    if not expected <= available:
        raise ValueError("Missing or expired independent redacted trial evidence artifacts")
    return {"schema": "hcran-oai-stage5zg-historical-acceptance-v1",
            "status": "HISTORICAL_TWO_SESSIONS_THREE_AMF_CYCLES_EACH_ACCEPTED",
            "historical_run": RUN_ID, "source_sha": PIN, "trials": good,
            "cycle_count_claim": 6, "evidence_scope": "workflow acceptance and retained archive metadata; not independent raw-packet re-decoding",
            "recovery_scope": "AMF-only, two unchanged synthetic UE processes, isolated RFsim",
            "other_faults": "NOT_VALIDATED", "qos_security_conformance": "NOT_VALIDATED",
            "hp_cluster_touched": False, "production_decision": "NO_GO"}

def gh(path):
    token = os.getenv("GH_TOKEN")
    if not token:
        raise ValueError("GitHub token missing")
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hcran-evidence-audit"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run = gh(f"actions/runs/{RUN_ID}")
    jobs = gh(f"actions/runs/{RUN_ID}/jobs?per_page=100")["jobs"]
    artifacts = gh(f"runs/{RUN_ID}/artifacts?per_page=100")["artifacts"] if False else gh(f"actions/runs/{RUN_ID}/artifacts?per_page=100")["artifacts"]
    receipt = inspect(run, jobs, artifacts)
    p = Path(args.out)
    p.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
if __name__ == "__main__":
    main()
