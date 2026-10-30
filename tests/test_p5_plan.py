"""Read-only P5 Phase-1 release planning contract."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/experiments/p5"))
import p5_plan
from scenario_registry import load_registry

COMMIT = "a" * 40
BRANCH = "reproducible-installer"
REPO = "rathod9771/5g-kubernetes"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture_files():
    registry = load_registry(ROOT)
    entry = next(s for s in registry["scenarios"] if s["key"] == "cran-oai")
    knf = "packages/" + entry["knf_package"] + ".tar.gz"
    nsd = "packages/" + entry["nsd_package"] + ".tar.gz"
    prov = "provenance/cran-oai.provenance.json"
    files = {knf: b"synthetic KNF", nsd: b"synthetic NSD", prov: b"synthetic provenance"}
    hashes = {k: digest(v) for k, v in files.items()}
    manifest = {
        "schema": "5g-kubernetes/release-manifest",
        "schema_version": 1,
        "repository": REPO,
        "source": {"commit": COMMIT, "branch": BRANCH, "ref": "refs/heads/" + BRANCH},
        "workflow": {
            "path": ".github/workflows/release-candidate.yml",
            "ref": REPO + "/.github/workflows/release-candidate.yml@refs/heads/" + BRANCH,
            "run_id": 42, "run_attempt": 1, "runner_environment": "github-hosted",
        },
        "channel": "release",
        "ci_validation": {"status": "passed"},
        "change_impact": {
            "status": "determined",
            "buildable": ["cran-oai"],
            "ran_scenarios": ["cran-oai"],
            "core_components": [],
        },
        "packages": [{
            "scenario": "cran-oai", "kind": "ran",
            "knf": {"descriptor_id": entry["knf_package"], "artifact": knf, "sha256": hashes[knf]},
            "nsd": {"descriptor_id": entry["nsd_package"], "artifact": nsd, "sha256": hashes[nsd]},
            "provenance": {"artifact": prov, "sha256": hashes[prov]},
        }],
        "artifacts": hashes,
        "images": {"components": {"oai-cu": {"status": "MISSING_DIGEST"}},
                   "scenarios": {"cran-oai": {"components": ["oai-cu"], "chart_images": []}}},
        "eligibility": {"publication_allowed": False, "automatic_deployment_allowed": False,
                        "blockers": ["image digest missing"]},
        "target": {"environment": "hp-lab", "architecture": "linux/amd64"},
    }
    files["release-manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    files["SHA256SUMS"] = "".join(digest(value) + "  " + name + "\n"
                                   for name, value in sorted(files.items())).encode()
    return files


class P5PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p5-offline-plan-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "candidate"
        self.path.mkdir()
        self.files = fixture_files()
        self.write_files()

    def write_files(self):
        for path in sorted(self.path.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
        for name, data in self.files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def test_consistent_unsigned_candidate_is_only_proposed(self):
        result = p5_plan.plan(self.path, COMMIT, BRANCH)
        self.assertEqual((result["status"], result["trust"]), ("BLOCKED", "UNVERIFIED"))
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["catalog_publication_authorized"])
        self.assertFalse(result["mutations_performed"])
        self.assertFalse(result["target_observed"])
        self.assertIn("NO_DELL_TRUSTED_ATTESTATION", result["blockers"])
        self.assertEqual([p["scenario"] for p in result["proposed_packages"]], ["cran-oai"])
        self.assertEqual(result["proposed_packages"][0]["kdu_order"], ["cu", "du"])
        self.assertIsNone(result["proposed_packages"][0]["approved_ns_uuid"])

    def test_rejects_altered_bytes_even_when_manifest_unchanged(self):
        self.files[next(k for k in self.files if k.startswith("packages/"))] = b"tampered"
        self.write_files()
        with self.assertRaises(Exception):
            p5_plan.plan(self.path, COMMIT, BRANCH)

    def test_rejects_wrong_commit_or_branch(self):
        for commit, branch in (("b"*40, BRANCH), (COMMIT, "feature/other")):
            with self.subTest(commit=commit, branch=branch), self.assertRaises(Exception):
                p5_plan.plan(self.path, commit, branch)

    def test_rejects_unexpected_extra_file(self):
        self.files["extra.json"] = b"{}"
        self.write_files()
        with self.assertRaises(Exception):
            p5_plan.plan(self.path, COMMIT, BRANCH)

    def test_rejects_directory_symlink(self):
        # Avoid side effects to the external tree. Reading must reject links.
        (self.path / "extra-link").symlink_to(self.path / "release-manifest.json")
        with self.assertRaises(Exception):
            p5_plan.plan(self.path, COMMIT, BRANCH)

    def test_refuses_modified_p4_gate(self):
        with mock.patch.object(p5_plan.p5_readiness, "assess", return_value={"existing_policy_locked": False}):
            with self.assertRaisesRegex(p5_plan.PlanRejected, "EXISTING_APPROVAL_POLICY_CHANGED"):
                p5_plan.plan(self.path, COMMIT, BRANCH)

    def test_invalid_source_identity_rejected(self):
        with self.assertRaisesRegex(p5_plan.PlanRejected, "INVALID_EXPECTED_COMMIT"):
            p5_plan.plan(self.path, "short", BRANCH)
        with self.assertRaisesRegex(p5_plan.PlanRejected, "INVALID_EXPECTED_BRANCH"):
            p5_plan.plan(self.path, COMMIT, "../main")

    def test_unverified_manifest_cannot_claim_automatic_deployment(self):
        manifest = json.loads(self.files["release-manifest.json"])
        manifest["eligibility"]["automatic_deployment_allowed"] = True
        self.files["release-manifest.json"] = json.dumps(manifest).encode()
        self.files["SHA256SUMS"] = "".join(digest(v) + "  " + n + "\n"
                                           for n, v in sorted(self.files.items()) if n != "SHA256SUMS").encode()
        self.write_files()
        with self.assertRaises(Exception):
            p5_plan.plan(self.path, COMMIT, BRANCH)

    def test_cli_rejects_invalid_path_with_no_credentials(self):
        result = p5_plan.main(["--candidate-dir", str(self.path / "missing"),
                               "--expected-commit", COMMIT, "--expected-branch", BRANCH])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
