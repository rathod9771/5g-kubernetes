#!/usr/bin/env python3
"""Fail-closed, read-only preflight for an explicitly dispatched RFsim run.

The experimental one-DU C-RAN srsRAN NG/F1 Docker trial must not start until
full Offline Project Validation succeeded for the EXACT workflow commit and
branch. Does not accept an older run, unrelated workflow, failed run,
replayed run for a different commit or anything outside the own repo.

Only GitHub Actions' ephemeral GITHUB_TOKEN is used to GET one run record.
No token material is logged. NO DEPLOY.
"""
import json
import os
import re
import sys
from urllib import request

REPO = "rathod9771/5g-kubernetes"
OFFLINE_WORKFLOW = ".github/workflows/helm-validate.yml"
OFFLINE_NAME = "Offline Project Validation"
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN = re.compile(r"^[1-9][0-9]{0,15}$")
BRANCH = re.compile(r"^experiment/phase1b2-cran-srsran-ngf1-[0-9]{8}$")

class GateError(RuntimeError):
    pass

def validate(run, expected_sha, expected_branch):
    if not SHA.fullmatch(expected_sha):
        raise GateError("invalid intended commit SHA")
    if not BRANCH.fullmatch(expected_branch) or ".." in expected_branch:
        raise GateError("invalid isolated experimental ref")
    if not isinstance(run, dict):
        raise GateError("invalid GitHub Actions run response")
    if (run.get("repository") or {}).get("full_name") != REPO:
        raise GateError("offline result belongs to another repository")
    if run.get("name") != OFFLINE_NAME or run.get("path") != OFFLINE_WORKFLOW:
        raise GateError("offline result is not the full project-validation workflow")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise GateError("offline validation has not passed")
    if run.get("head_sha") != expected_sha:
        raise GateError("offline validation SHA does not match LIVE workflow SHA")
    if run.get("head_branch") != expected_branch:
        raise GateError("offline validation branch does not match LIVE workflow branch")
    if run.get("event") != "push":
        raise GateError("offline validation must originate from normal source push")
    return {
        "schema": "phase1b2-cran-srsran-offline-same-commit-gate-v1",
        "status": "PASS",
        "offline_run_id": run.get("id"),
        "verified_sha": expected_sha,
        "verified_branch": expected_branch,
        "offline_workflow": OFFLINE_WORKFLOW,
        "production_authorized": False,
        "production_decision": "NO_GO",
    }

def main():
    confirmation = os.environ.get("PHASE1B2_ISOLATED_CONFIRM", "")
    if confirmation != "ISOLATED_SRSCU_SRSDU_NGF1_NO_DEPLOY":
        raise GateError("explicit isolated/no-deploy confirmation missing")
    run_id = os.environ.get("PHASE1B2_OFFLINE_RUN_ID", "")
    if not RUN.fullmatch(run_id):
        raise GateError("offline run ID missing or invalid")
    sha = os.environ.get("GITHUB_SHA", "")
    branch = os.environ.get("GITHUB_REF_NAME", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise GateError("ephemeral actions read token unavailable")
    url = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}"
    req = request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "phase1b2-cran-srsran-offline-gate",
    })
    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read(1024 * 1024 + 1)
    except Exception as exc:
        raise GateError("unable to verify offline run via read-only GitHub API: " + type(exc).__name__) from None
    if len(raw) > 1024 * 1024:
        raise GateError("GitHub run metadata larger than approved limit")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise GateError("invalid GitHub run metadata") from None
    result = validate(data, sha, branch)
    # This output never contains credentials or subscriber data.
    print(json.dumps(result, sort_keys=True), flush=True)

if __name__ == "__main__":
    try:
        main()
    except GateError as exc:
        print("FAIL CLOSED Phase1B: "+str(exc),file=sys.stderr,flush=True)
        raise SystemExit(1)
