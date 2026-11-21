"""Phase 1B2 bounded capture observer preflight: offline rejection contracts.

NO Docker, packet capture, SCTP traffic, radio, 5GC or HP host activity.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "experiments"
import sys
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


testee = load("phase1b2_capture_preflight")


class CaptureCommandContracts(unittest.TestCase):
    def test_preflight_uses_exact_original_sctp_tshark_options(self):
        cmd = testee.capture_command(testee.wire.BRIDGE, Path("/tmp/test-only.pcapng"))
        self.assertEqual(cmd[:5], ["sudo", "-n", "dumpcap", "-i", testee.wire.BRIDGE])
        self.assertEqual(cmd[5:7], ["-f", "sctp"])
        self.assertEqual(cmd[7:11], ["-a", "duration:8", "-a", "filesize:4096"])
        self.assertEqual(cmd[-2:], ["-w", "-"])

    def test_capture_output_owned_by_unprivileged_parent(self):
        source = (SCRIPTS / "phase1b2_capture_preflight.py").read_text()
        runtime = (SCRIPTS / "phase1b2_srsran_ngf1.py").read_text()
        self.assertIn('pcap.open("wb") as capture_stream', source)
        self.assertIn('capture.open("wb") as capture_stream', runtime)
        self.assertIn('stdout=capture_stream', source)
        self.assertIn('stdout=capture_stream', runtime)
        self.assertIn('"-w","-"', runtime)
        self.assertNotIn('stdout=subprocess.DEVNULL,stderr=log', runtime)

    def test_wrong_interface_and_excess_duration_rejected(self):
        for interface, seconds in (("any", 8), ("eth0", 8),
                                   (testee.wire.BRIDGE, 0),
                                   (testee.wire.BRIDGE, 121)):
            with self.subTest(interface=interface, seconds=seconds):
                with self.assertRaises(testee.PreflightError):
                    testee.capture_command(interface, Path("/tmp/no-use"), seconds)

    def test_no_live_5g_procedure_or_subscriber_in_source(self):
        src = (SCRIPTS / "phase1b2_capture_preflight.py").read_text()
        for banned in ("kubectl", "ssh ", "ctr -n", "open5gs-", "srscu", "srsdu",
                       "pdu-session", "docker system prune", "docker run", "docker exec"):
            self.assertNotIn(banned, src)
        self.assertIn('"--internal"', src)
        self.assertIn("process_survived_startup", src)
        self.assertIn("capture_readable", src)


class BoundedErrorCategoryContracts(unittest.TestCase):
    def test_missing_interface(self):
        self.assertEqual(testee.classify_error(
            "tshark: The interface 'br-p1b2srs' was not found"), "NO_SUCH_INTERFACE")

    def test_permission_denied(self):
        self.assertEqual(testee.classify_error(
            "dumpcap: Permission denied"), "PERMISSION_DENIED")

    def test_bad_capture_arguments(self):
        self.assertEqual(testee.classify_error(
            "tshark: Invalid capture filter option filesize:4096"), "CAPTURE_ARGUMENT_REJECTED")

    def test_dumpcap_literal_is_not_a_capture_file(self):
        self.assertEqual(testee.classify_error(
            "dumpcap: Permission denied"), "PERMISSION_DENIED")
        self.assertEqual(testee.classify_error(
            "dumpcap: Could not open file /tmp/test.pcapng: Permission denied"),
            "CAPTURE_OUTPUT_DENIED")
        self.assertEqual(testee.classify_error(
            "dumpcap: Cannot capture on interface br-p1b2srs: Permission denied"),
            "CAPTURE_INTERFACE_DENIED")

    def test_missing_dumpcap(self):
        self.assertEqual(testee.classify_error(
            "tshark: Couldn't run dumpcap in child process"), "DUMPCAP_LAUNCH_FAILED")

    def test_arbitrary_diagnostics_cannot_be_uploaded(self):
        secret = "Authorization: Bearer NEVER_UPLOAD_TEST_TOKEN"
        code = testee.classify_error(secret)
        self.assertEqual(code, "OBSERVER_EXIT_UNCLASSIFIED")
        self.assertNotIn("NEVER_UPLOAD_TEST_TOKEN", code)

    def test_nontext_diagnostics(self):
        self.assertEqual(testee.classify_error(None), "OBSERVER_DIAGNOSTIC_UNAVAILABLE")


class IsolationContracts(unittest.TestCase):
    def test_reject_non_github_or_selfhosted_execution(self):
        for env in ({}, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"},
                    {"GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT": "github-hosted"}):
            with patch.dict("os.environ", env, clear=True):
                with self.assertRaises(testee.PreflightError):
                    testee.runner_only()

    def test_only_private_runner_temp_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runner").mkdir()
            with patch.dict("os.environ", {"RUNNER_TEMP": str(base / "runner")}):
                path = testee.validate_output(base / "runner" / "observer")
                self.assertTrue(path.is_dir())
                with self.assertRaises(testee.PreflightError):
                    testee.validate_output(base / "other")
                with self.assertRaises(testee.PreflightError):
                    testee.validate_output(base / "runner")
                (base / "runner" / "link").symlink_to(base / "other")
                with self.assertRaises(testee.PreflightError):
                    testee.validate_output(base / "runner" / "link")

    def test_failure_receipt_not_protocol_pass(self):
        self.assertIn("NO_5G_PROCEDURES", testee.SCHEMA.replace(
            "phase1b2-observer-preflight-v1", "CAPTURE_STARTUP_ONLY_NO_5G_PROCEDURES"))
        self.assertNotIn("NGAP_F1AP_WIRE_PASS", (SCRIPTS / "phase1b2_capture_preflight.py").read_text())
        self.assertNotIn("tshark -r", testee.wire.NOT_RUN)


if __name__ == "__main__":
    unittest.main()
