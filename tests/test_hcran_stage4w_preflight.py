"""Offline adversarial tests for pinned OAI F1 mobility feasibility auditor."""
import hashlib
import importlib.util
from pathlib import Path
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mobility_audit", ROOT / "scripts/hcran_stage4w_mobility_preflight.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def sample():
    source = ("\n".join(audit.SOURCE_MARKERS)).encode()
    tutorial = ("\n".join(audit.TUTORIAL_MARKERS)).encode()
    hashes = {
        "rrc_gNB_mobility.c": audit.git_blob_sha(source),
        "handover-tutorial.md": audit.git_blob_sha(tutorial),
    }
    return source, tutorial, hashes


class MobilitySourcePreflightTests(TestCase):
    def test_pinned_source_support_does_not_imply_runtime_success(self):
        source, tutorial, hashes = sample()
        with mock.patch.dict(audit.PINNED, hashes):
            result = audit.review(source, tutorial, "build_oai --gNB -w SIMU")
        self.assertEqual(result["source_support"], "PINNED_SOURCE_HAS_F1_HANDOVER_PATH")
        self.assertEqual(result["runtime_status"], "BLOCKED")
        self.assertEqual(result["release_decision"], "NO_GO")
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["real_f1_handover_observed"])
        self.assertTrue(any("telnetsrv" in msg for msg in result["reasons"]))

    def test_source_drift_rejected_even_if_markers_present(self):
        source, tutorial, hashes = sample()
        with mock.patch.dict(audit.PINNED, hashes):
            result = audit.review(source + b"\nmodified", tutorial, "--build-lib telnetsrv")
        self.assertEqual(result["source_support"], "NOT_VERIFIED")
        self.assertEqual(result["release_decision"], "NO_GO")
        self.assertFalse(result["deploy_authorized"])

    def test_tutorial_drift_rejected(self):
        source, tutorial, hashes = sample()
        with mock.patch.dict(audit.PINNED, hashes):
            result = audit.review(source, tutorial + b"!", "")
        self.assertEqual(result["source_support"], "NOT_VERIFIED")
        self.assertEqual(result["runtime_status"], "BLOCKED")

    def test_missing_handover_implementation_rejected(self):
        source, tutorial, hashes = sample()
        source = source.replace(b"nr_rrc_f1_ho_complete", b"no_function_found")
        hashes["rrc_gNB_mobility.c"] = audit.git_blob_sha(source)
        with mock.patch.dict(audit.PINNED, hashes):
            result = audit.review(source, tutorial, "--build-lib telnetsrv")
        self.assertEqual(result["source_support"], "NOT_VERIFIED")
        self.assertFalse(result["deploy_authorized"])

    def test_even_telnet_enabled_compilation_is_not_handover_evidence(self):
        source, tutorial, hashes = sample()
        with mock.patch.dict(audit.PINNED, hashes):
            result = audit.review(source, tutorial, "build_oai --build-lib telnetsrv")
        self.assertEqual(result["source_support"], "PINNED_SOURCE_HAS_F1_HANDOVER_PATH")
        self.assertFalse(result["real_f1_handover_observed"])
        self.assertFalse(result["ue_pdu_session_continuity_observed"])
        self.assertEqual(result["runtime_status"], "BLOCKED")
