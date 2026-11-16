#!/usr/bin/env python3
"""Offline negative-contract tests only; do not claim synthesized PASS."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
FILE=ROOT/"experiments/oai-ngap-2026w13/stage5p/verify_three_cycle_recovery.py"
spec=importlib.util.spec_from_file_location("stage5p_verify_three_cycle_recovery",FILE)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Stage5PFailClosedTests(unittest.TestCase):
    def test_missing_evidence_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            passed,reasons=module.verify(temp)
            self.assertFalse(passed)
            self.assertTrue(reasons)

    def test_two_cycles_never_qualify_three(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"results.json").write_text("[]")
            (root/"stage5p-multicycle-summary.json").write_text(json.dumps({
                "schema":"hcran-stage5p-three-consecutive-amf-recoveries-v1",
                "requested_cycles":3,"status":"PASS","production_authorized":False,
                "production_decision":"NO_GO","cycles":[{"status":"PASS"},{"status":"PASS"}],
            }))
            passed,reasons=module.verify(root)
            self.assertFalse(passed)
            self.assertIn("Exactly three complete cycles required",reasons)

    def test_fake_three_cycle_claim_without_packets_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"results.json").write_text(json.dumps([
                {"stage":"STAGE5P","status":"PASS"}] +
                [{"stage":"STAGE5P-CYCLE","status":"PASS","cycle":i} for i in range(1,4)]))
            (root/"stage5p-multicycle-summary.json").write_text(json.dumps({
                "schema":"hcran-stage5p-three-consecutive-amf-recoveries-v1",
                "requested_cycles":3, "status":"PASS",
                "production_authorized":False,"production_decision":"NO_GO",
                "ue_process_identity_sha256":"a"*64,
                "cycles":[{
                    "cycle":i,"status":"PASS",
                    "ue_process_identity_sha256":"a"*64,
                    "production_decision":"NO_GO","duration_seconds":1,
                    "postfault_gtpu_pcap_sha256":"b"*64}
                    for i in range(1,4)],
            }))
            ok,reasons=module.verify(root)
            self.assertFalse(ok)
            self.assertEqual(3, sum("evidence directory missing" in s for s in reasons))

    def test_workflow_never_targets_hp_and_requires_recovery_verifier(self):
        text=(ROOT/".github/workflows/hcran-stage5p-three-cycle-amf-recovery.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",text)
        self.assertNotIn("runs-on: self-hosted",text)
        self.assertIn("verify_three_cycle_recovery.py",text)
        self.assertIn("--cycles 3 --verify-gtpu",text)
        self.assertNotIn("helm upgrade",text)
        self.assertNotIn("kubectl apply",text)
        self.assertNotIn("osm ns-",text)

if __name__=="__main__":
    unittest.main()
