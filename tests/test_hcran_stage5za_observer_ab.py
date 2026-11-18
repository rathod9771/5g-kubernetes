"""Stage5ZA observer-toggle isolation contracts. Synthetic; never live proof."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/oai-ngap-2026w13"
SELECTOR = HERE / "stage5s/cu_amf_selection_stage5z.py"
TRACE = HERE / "stage5s/run_protocol_trace.py"
ANALYZER = HERE / "stage5z/analyze_amf_selection_stage5z.py"
WORKFLOW = ROOT / ".github/workflows/hcran-stage5y-amf-ingress-observer.yml"

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Stage5ZAObserverContracts(unittest.TestCase):
    def test_experimental_cu_selection_only_toggle(self):
        m = load(SELECTOR, "stage5za_selector")
        self.assertTrue(m.observer_enabled({}))
        self.assertTrue(m.observer_enabled({"STAGE5ZA_CU_OBSERVER":"on"}))
        self.assertFalse(m.observer_enabled({"STAGE5ZA_CU_OBSERVER":"off"}))
        for bad in ("yes","", "0", "true", "disabled"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    m.observer_enabled({"STAGE5ZA_CU_OBSERVER":bad})

    def test_toggling_changes_only_optional_cu_log_categories(self):
        trace = TRACE.read_text()
        self.assertIn('self.stage5za_cu_observer_enabled = observer_enabled()', trace)
        self.assertIn('role == "cu" and self.stage5za_cu_observer_enabled', trace)
        self.assertIn('self.add(role, key, metadata)', trace)
        self.assertIn('observe_amf_files', trace)  # retain cell AMF ingress
        self.assertIn('stage5za_cu_selection_observer_mode', trace)
        self.assertIn('actual = probe.fault_and_recover', trace)
        self.assertIn('collector.start()', trace)

    def test_both_modes_keep_real_stage5y_verifier_and_negative_controls(self):
        w = WORKFLOW.read_text()
        self.assertIn("experiment/hcran-stage5za-observer-ab-20261118", w)
        self.assertIn("trial: [1, 2]", w)
        self.assertIn("observer_mode: ['on', 'off']", w)
        self.assertIn('STAGE5ZA_CU_OBSERVER: ${{ matrix.observer_mode }}', w)
        self.assertIn("--cycles 3 --verify-gtpu", w)
        for req in ("stage5u/audit_repeatability.py", "stage5y/verify_live_amf_ingress.py",
                    "stage5z/analyze_amf_selection_stage5z.py"):
            self.assertIn(req, w)
        self.assertNotIn("runs-on: self-hosted", w)
        for banned in ("kubectl apply", "helm upgrade", "osm ns-"):
            self.assertNotIn(banned, w)
        a = ANALYZER.read_text()
        self.assertIn("CONTROL_DIAGNOSTIC_ONLY", a)
        self.assertIn("cu_selection_observer_mode", a)

if __name__ == "__main__":
    unittest.main()
