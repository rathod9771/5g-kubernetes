#!/usr/bin/env python3
"""P5 phase 0: read-only readiness assessment, not an OSM deployment controller.

Never consumes credentials, calls OSM/Kubernetes, modifies release state, or
authorizes promotion. A successful check means the existing locks are intact,
NOT that continuous deployment is ready.
"""
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = "config/release-verification.json"
LIFECYCLE = "scripts/lifecycle.py"


class ReadinessError(ValueError):
    pass


def _approval_is_closed(source):
    try:
        module = ast.parse(source)
    except SyntaxError as error:
        raise ReadinessError("LIFECYCLE_PARSE_FAILED") from error
    values = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
                t.id == "P4_KDU_UPGRADE_APPROVAL" for t in node.targets):
            values.append(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and \
                node.target.id == "P4_KDU_UPGRADE_APPROVAL":
            values.append(node.value)
    if len(values) != 1:
        raise ReadinessError("APPROVAL_GATE_AMBIGUOUS")
    return isinstance(values[0], ast.Constant) and values[0].value is None


def assess(root=ROOT):
    """Offline repository contract. All output is fixed reason codes, no secrets."""
    root = Path(root)
    try:
        policy = json.loads((root / POLICY).read_text(encoding="utf-8"))
        gate_closed = _approval_is_closed((root / LIFECYCLE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReadinessError("REQUIRED_SOURCE_UNAVAILABLE") from error
    if not isinstance(policy, dict) or policy.get("schema") != "5g-kubernetes/release-verification-policy" \
            or policy.get("schema_version") != 1:
        raise ReadinessError("UNEXPECTED_P3_POLICY")
    target = policy.get("target")
    pinned_hp = target == {"environment": "hp-lab", "architecture": "linux/amd64"}
    pinned_branch = policy.get("branch") == "reproducible-installer"
    pinned_event = policy.get("event") == "push"
    # These invariants protect existing production policy. We do not repurpose
    # the HP verifier or silently open the KDU approval gate for Dell testing.
    locked = pinned_hp and pinned_branch and pinned_event and gate_closed
    blockers = ["NO_DELL_VERIFIED_RELEASE", "NO_DELL_RUNTIME_OBSERVATION",
                "NO_OPERATOR_APPROVAL", "NO_TARGET_SIDE_CD_AGENT"]
    if pinned_hp:
        blockers.append("P3_POLICY_PINNED_TO_HP")
    else:
        blockers.append("P3_POLICY_CHANGED_REQUIRES_REVIEW")
    if pinned_branch:
        blockers.append("P3_BRANCH_NOT_P4")
    else:
        blockers.append("P3_BRANCH_CHANGED_REQUIRES_REVIEW")
    if gate_closed:
        blockers.append("P4_APPROVAL_GATE_CLOSED")
    else:
        blockers.append("P4_GATE_CHANGED_REQUIRES_REVIEW")
    return {
        "schema": "5g-kubernetes/p5-readiness",
        "schema_version": 1,
        "target": "dell-p4",
        "status": "BLOCKED",
        "deployment_authorized": False,
        "mutations_performed": False,
        "existing_policy_locked": locked,
        "blockers": sorted(blockers),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-locked", action="store_true",
                        help="Fail CI if the existing HP policy or committed P4 approval gate changed")
    args = parser.parse_args(argv)
    try:
        result = assess()
    except ReadinessError as error:
        print(json.dumps({"status": "ERROR", "reason": str(error), "deployment_authorized": False}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.assert_locked and not result["existing_policy_locked"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
