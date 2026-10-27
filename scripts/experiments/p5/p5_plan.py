#!/usr/bin/env python3
"""P5 phase 1: inspect an offline P2 candidate and propose a NON-EXECUTABLE plan.

No network, no credential access, no runtime-state reads, no publication, no
Kubernetes/OSM actions, and no deployment authorization. A GitHub manifest is a
claim, not a signed attestation. All proposed steps remain blocked.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from release_fetch import FetchError, read_directory
from release_manifest import MANIFEST_NAME, WORKFLOW_PATH, ManifestError, verify as verify_manifest
from release_verify import VerificationRejected, check_layout
from scenario_registry import RegistryError, load_registry
import p5_readiness

REPOSITORY = "rathod9771/5g-kubernetes"
SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
MAX_FILES = 256
MAX_BYTES = 64 << 20


class PlanRejected(ValueError):
    pass


def plan(candidate_dir, expected_commit, expected_branch, root=ROOT):
    """Analyze *untrusted* candidate bytes; never turn a proposal into authority."""
    if not isinstance(expected_commit, str) or not SHA.fullmatch(expected_commit):
        raise PlanRejected("INVALID_EXPECTED_COMMIT")
    if (not isinstance(expected_branch, str) or not BRANCH.fullmatch(expected_branch)
            or ".." in expected_branch or "//" in expected_branch or expected_branch.endswith("/")):
        raise PlanRejected("INVALID_EXPECTED_BRANCH")
    safety = p5_readiness.assess(root)
    if not safety["existing_policy_locked"]:
        raise PlanRejected("EXISTING_APPROVAL_POLICY_CHANGED")

    # Reuse P3's bounded, no-symlink artifact loading and SHA256SUMS contract.
    files = read_directory(candidate_dir, max_files=MAX_FILES, max_bytes=MAX_BYTES)
    check_layout(files)
    registry = load_registry(root)
    expected = {
        "repository": REPOSITORY,
        "branches": (expected_branch,),
        "commit": expected_commit,
        "workflow_path": WORKFLOW_PATH,
    }
    verdict = verify_manifest(files[MANIFEST_NAME], files.__getitem__, expected, registry)
    # The verifier deliberately has NO attestation callback. A structurally
    # valid and byte-consistent candidate is still not GitHub-trusted.
    if verdict["provenance"] != "UNVERIFIED" or verdict["publication_eligible"] or verdict["deployment_eligible"]:
        raise PlanRejected("UNEXPECTED_MANIFEST_VERDICT")
    manifest = json.loads(files[MANIFEST_NAME])
    entries = {e["key"]: e for e in registry["scenarios"] + registry.get("installer_components", [])}
    proposals = []
    for package in sorted(manifest["packages"], key=lambda item: item["scenario"]):
        key = package["scenario"]
        entry = entries[key]    # registry identity already checked by verify_manifest()
        proposals.append({
            "scenario": key,
            "kind": package["kind"],
            "knf": {"descriptor_id": entry["knf_package"], "sha256": package["knf"]["sha256"]},
            "nsd": {"descriptor_id": entry["nsd_package"], "sha256": package["nsd"]["sha256"]},
            "kdu_order": [chart["kdu"] for chart in entry["charts"]],
            "effect": "PROPOSED_ONLY",
            "approved_target": None,
            "approved_ns_uuid": None,
        })
    return {
        "schema": "5g-kubernetes/p5-offline-deployment-plan",
        "schema_version": 1,
        "status": "BLOCKED",
        "deployment_authorized": False,
        "catalog_publication_authorized": False,
        "mutations_performed": False,
        "trust": "UNVERIFIED",
        "target_intent": "dell-p4",
        "target_observed": False,
        "source": {
            "repository": REPOSITORY,
            "branch": expected_branch,
            "commit": expected_commit,
            "manifest_sha256": hashlib.sha256(files[MANIFEST_NAME]).hexdigest(),
        },
        "change_impact": {
            "status": manifest["change_impact"].get("status"),
            "ran_scenarios": sorted(manifest["change_impact"].get("ran_scenarios", [])),
            "core_components": sorted(manifest["change_impact"].get("core_components", [])),
        },
        "proposed_packages": proposals,
        "blockers": sorted(set(safety["blockers"]) | {
            "NO_DELL_TRUSTED_ATTESTATION", "NO_APPROVED_LIVE_TARGET_SNAPSHOT",
            "NO_EXPLICIT_OPERATOR_APPROVAL", "NO_CD_AGENT", "CANDIDATE_PLAN_IS_NOT_EXECUTABLE"
        }),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True, help="Locally downloaded P2 release artifact directory")
    parser.add_argument("--expected-commit", required=True, help="Full Git SHA from the operator's chosen run")
    parser.add_argument("--expected-branch", required=True, help="Branch expected for that run, never inferred from manifest")
    args = parser.parse_args(argv)
    try:
        result = plan(args.candidate_dir, args.expected_commit, args.expected_branch)
    except (PlanRejected, p5_readiness.ReadinessError, FetchError, ManifestError,
            VerificationRejected, RegistryError, OSError, ValueError, KeyError, TypeError) as error:
        # Fixed error class only; never echo attacker-controlled path, URLs or contents.
        print(json.dumps({"status": "REJECTED", "reason_code": type(error).__name__,
                          "deployment_authorized": False, "mutations_performed": False}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
