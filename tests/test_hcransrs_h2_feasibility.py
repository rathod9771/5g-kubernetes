"""Offline H2 feasibility contract. Synthetic fixture only, no RF/UE/network."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hcransrs_h2_feasibility", ROOT / "scripts/experiments/hcransrs_h2_feasibility.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class H2Feasibility(unittest.TestCase):
    def test_canonical_srsue_incompatible_with_both_n78_cells(self):
        data = mod.audit(ROOT)
        self.assertEqual(data["h2"], "BLOCKED_INCOMPATIBLE_UE")
        self.assertEqual(data["end_to_end"], "NOT_VALIDATED")
        self.assertEqual(data["production_decision"], "NO_GO")
        self.assertFalse(data["hp_cluster_touched"])
        self.assertEqual({r["du_id"] for r in data["du_results"]}, {100,101})
        for cell in data["du_results"]:
            self.assertEqual((cell["band"], cell["scs_khz"], cell["duplex"]), (78,30,"TDD"))
            self.assertFalse(cell["srsue_prototype_compatible"])
            self.assertEqual(cell["h3_nas_pdu"], "NOT_RUN")
            self.assertEqual(cell["h4_f1u_ngu"], "NOT_RUN")
            self.assertEqual(cell["h5_mobility"], "BLOCKED_UE_UNSUPPORTED")

    def test_compatible_fdd_is_not_a_runtime_pass(self):
        profile = {"radio": {"duId": 100, "pci": 101, "channel_bandwidth_MHz": 10}}
        # A bandwidth match by itself must never authorize UE testing.
        result = mod.classify(profile)
        self.assertFalse(result["srsue_prototype_compatible"])
        self.assertEqual(result["h2_rrc"], "NOT_RUN")

    def test_identity_and_radio_template_drift_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td)
            for name in mod.CANONICAL + ("helm/cran-srsran/du/templates/du.yaml",):
                p = dst / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text((ROOT/name).read_text())
            p = dst / mod.CANONICAL[1]
            data = yaml.safe_load(p.read_text())
            data["radio"]["duId"] = 100
            p.write_text(yaml.safe_dump(data))
            with self.assertRaises(ValueError):
                mod.audit(dst)
            p.write_text((ROOT / mod.CANONICAL[1]).read_text())
            tpl = dst / "helm/cran-srsran/du/templates/du.yaml"
            tpl.write_text(tpl.read_text().replace("common_scs: 30", "common_scs: 15"))
            with self.assertRaises(ValueError):
                mod.audit(dst)

if __name__ == "__main__":
    unittest.main()
