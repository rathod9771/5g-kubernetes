"""P5 offline safety contract; no Kubernetes, OSM, or network involved."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/experiments/p5"))
import p5_readiness


class P5ReadinessTests(unittest.TestCase):
    def fixture(self, directory, *, gate="None", target=None, branch="reproducible-installer"):
        root = Path(directory)
        (root / "scripts").mkdir()
        (root / "config").mkdir()
        (root / "scripts/lifecycle.py").write_text("P4_KDU_UPGRADE_APPROVAL = " + gate + "\n")
        (root / "config/release-verification.json").write_text(json.dumps({
            "schema": "5g-kubernetes/release-verification-policy",
            "schema_version": 1,
            "target": target if target is not None else {
                "environment": "hp-lab", "architecture": "linux/amd64"
            },
            "branch": branch,
            "event": "push",
        }))
        return root

    def test_repo_policy_stays_closed(self):
        status = p5_readiness.assess(ROOT)
        self.assertTrue(status["existing_policy_locked"])
        self.assertEqual(status["status"], "BLOCKED")
        self.assertFalse(status["deployment_authorized"])
        self.assertFalse(status["mutations_performed"])
        self.assertIn("P4_APPROVAL_GATE_CLOSED", status["blockers"])

    def test_offline_contract_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            status = p5_readiness.assess(self.fixture(temp))
        self.assertTrue(status["existing_policy_locked"])
        self.assertIn("P3_POLICY_PINNED_TO_HP", status["blockers"])
        self.assertIn("NO_TARGET_SIDE_CD_AGENT", status["blockers"])
        self.assertFalse(status["deployment_authorized"])

    def test_approval_gate_change_requires_review(self):
        with tempfile.TemporaryDirectory() as temp:
            status = p5_readiness.assess(self.fixture(temp, gate="{}"))
        self.assertFalse(status["existing_policy_locked"])
        self.assertIn("P4_GATE_CHANGED_REQUIRES_REVIEW", status["blockers"])
        self.assertFalse(status["deployment_authorized"])

    def test_unreviewed_target_policy_change_requires_review(self):
        with tempfile.TemporaryDirectory() as temp:
            status = p5_readiness.assess(self.fixture(temp, target={
                "environment": "dell-p4", "architecture": "linux/amd64"
            }))
        self.assertFalse(status["existing_policy_locked"])
        self.assertIn("P3_POLICY_CHANGED_REQUIRES_REVIEW", status["blockers"])

    def test_unreviewed_branch_change_requires_review(self):
        with tempfile.TemporaryDirectory() as temp:
            status = p5_readiness.assess(self.fixture(temp, branch="p4/kdu-upgrade-experiment"))
        self.assertFalse(status["existing_policy_locked"])
        self.assertIn("P3_BRANCH_CHANGED_REQUIRES_REVIEW", status["blockers"])

    def test_ambiguous_gate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            (root / "scripts/lifecycle.py").write_text(
                "P4_KDU_UPGRADE_APPROVAL = None\nP4_KDU_UPGRADE_APPROVAL = {}\n")
            with self.assertRaisesRegex(p5_readiness.ReadinessError, "APPROVAL_GATE_AMBIGUOUS"):
                p5_readiness.assess(root)

    def test_missing_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(p5_readiness.ReadinessError, "REQUIRED_SOURCE_UNAVAILABLE"):
                p5_readiness.assess(temp)


if __name__ == "__main__":
    unittest.main()
