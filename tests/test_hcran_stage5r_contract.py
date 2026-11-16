"""Fail-closed Stage5R protocol trace collection contracts (offline only)."""
import ast
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"experiments/oai-ngap-2026w13/stage5r/run_protocol_trace.py"

class Stage5RContracts(unittest.TestCase):
    def test_python_source_compiles_without_network(self):
        compile(SCRIPT.read_text(),str(SCRIPT),"exec")

    def test_no_raw_ue_output_is_written_by_observer(self):
        source=SCRIPT.read_text()
        tree=ast.parse(source)
        self.assertIn("class DiagnosticCollector",source)
        self.assertIn("def classify_lines(",source)
        self.assertIn("stage5r-protocol-timeline.json",source)
        self.assertIn("MAX_EVENTS = 1800",source)
        self.assertIn("MAX_ROLE_CATEGORY_EVENTS = 100",source)
        self.assertNotIn('write_text(line',source)
        self.assertNotIn('write_bytes(',source)
        self.assertNotIn("docker cp",source)
        self.assertNotIn("git push",source)
        self.assertNotIn("kubectl apply",source)
        self.assertTrue(tree.body)

    def test_workflow_is_isolated_and_runs_real_stage5p_gate(self):
        w=(ROOT/".github/workflows/hcran-stage5r-protocol-trace.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",w)
        self.assertIn("trial: [1, 2]",w)
        self.assertIn("run_protocol_trace.py",w)
        self.assertIn("verify_three_cycle_recovery.py",w)
        self.assertIn("--cycles 3 --verify-gtpu",w)
        self.assertIn("source-rebuilt CU",w)
        self.assertNotIn("runs-on: self-hosted",w)
        self.assertNotIn("actions/download-artifact",w)
        self.assertNotIn("helm upgrade",w)
        self.assertNotIn("kubectl apply",w)
        self.assertNotIn("osm ns-",w)

    def test_classifier_sanitizes_synthetic_subscriber_data(self):
        import sys
        spec=importlib.util.spec_from_file_location("stage5r",SCRIPT)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        secret="208930000000001"
        raw=f"{secret} Stage5M: defer random access (Stage5Q diagnostic count=8 mac_state=3 ra_state=0 ul_count=0 dl_count=0 mib=0 configured_ul0=0 configured_dl0=0)"
        classified=mod.classify_lines("ue-small",[raw])
        self.assertEqual(classified[0][0],"mac_bwp_deferral")
        self.assertEqual(classified[0][1]["ul_count"],0)
        self.assertNotIn(secret,str(classified))
        with tempfile.TemporaryDirectory() as td:
            d=mod.DiagnosticCollector(td)
            for _ in range(150):
                d.add("ue-small","mac_bwp_deferral",{"ul_count":0})
            self.assertEqual(d.counts["ue-small"]["mac_bwp_deferral"],150)
            self.assertEqual(len(d.events),100)
            self.assertTrue(d.limit_reached)

if __name__=="__main__":
    unittest.main()
