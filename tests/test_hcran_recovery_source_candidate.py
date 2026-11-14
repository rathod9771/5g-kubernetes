"""Fail-closed source integrity tests for opt-in H-CRAN research recovery candidate.
No fetch, image build, privilege escalation, remote cluster or deployment.
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from scenario_registry import load_registry  # noqa: E402
import release_impact  # noqa: E402

MANIFEST_PATH = ROOT / "docs/hcran-recovery/source-candidate-manifest.json"

class HCRANRecoverySourceCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text())
        cls.registry = load_registry(ROOT)

    def test_explicit_no_go_and_pinned_evidence(self):
        m = self.manifest
        self.assertEqual(m["production_decision"], "NO_GO")
        self.assertIs(m["production_authorized"], False)
        self.assertEqual(m["upstream_oai_commit"], "dfb296ab867050a7bde81b393fa31b757ada2c83")
        self.assertEqual(m["source_build_run_id"], 43588828565)
        self.assertEqual(m["repeated_test_run_id"], 43590147030)
        self.assertEqual(m["independent_recovery_trials"], 3)
        self.assertEqual(len(m["source_blobs"]), 5)

    def test_source_files_are_exact_blob_sha_and_opt_in(self):
        for path, expected_sha in self.manifest["source_blobs"].items():
            with self.subTest(path=path):
                file = ROOT / path
                self.assertTrue(file.is_file() and not file.is_symlink())
                data = file.read_bytes()
                blob_sha = hashlib.sha1(b"blob " + str(len(data)).encode() + bytes([0]) + data).hexdigest()
                self.assertEqual(blob_sha, expected_sha)
                kind, affected, full = release_impact.classify(path, self.registry)
                self.assertEqual((kind, affected, full), ("non-package", set(), False))
        self.assertNotIn("hcran_build_pinned_w45_ue_t3517_tunrenewal_raguard", (ROOT / "install.sh").read_text())

    def test_required_recovery_patches_and_ra_guard_present(self):
        p = self.manifest["source_blobs"]
        self.assertIn("experiments/oai-ngap-2026w13/stage5g/patch_oai_ue_cause9.py",p)
        self.assertIn("experiments/oai-ngap-2026w13/stage5i/patch_oai_ue_t3517.py",p)
        self.assertIn("experiments/oai-ngap-2026w13/stage5k/patch_ue_tun_renewal.py",p)
        guard = (ROOT/"experiments/oai-ngap-2026w13/stage5m/patch_ra_bwp_guard.py").read_text()
        self.assertIn("mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL",guard)
        self.assertIn("if (!init_RA(mac))",guard)

if __name__ == "__main__":
    unittest.main()
