"""Offline proof that local Layer 3 protocol inventory is read-only and excludes H-CRAN."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "layer3" / "local_protocol_observer.py"
spec = importlib.util.spec_from_file_location("layer3_non_hcran_probe", PATH)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def pod(name, namespace="5g", containers=("ran",), labels=None):
    return {
        "metadata": {"name": name, "namespace": namespace, "labels": labels or {}},
        "spec": {"containers": [{"name": c} for c in containers]},
        "status": {"phase": "Running"},
    }


class TestLocalObserver(unittest.TestCase):
    def test_all_hcran_variants_excluded(self):
        items = [
            pod("hcran-oai-cu"), pod("hcran-oai-macro"), pod("hcran-oai-small"),
            pod("hcran-srsran-cu"), pod("hcran-srsran-du"),
            pod("cran-srsran-cu", labels={"scenario": "hcran"}),
            pod("oran-oai-du", labels={"owner": "hybrid-cran"}),
        ]
        self.assertEqual(observer.select(items, 35), [])

    def test_non_hcran_oai_and_srsran_included(self):
        cases = [
            "cran-srsran-cu", "vcran-srsran-du", "fran-srsran-fog",
            "cloud-ran-srsran-cu", "oran-srsran-du",
            "cran-oai-cu", "vcran-oai-du", "fran-oai-du",
            "oran-oai-cu", "cloud-ran-oai-gnb", "open5gs-amf-0",
        ]
        selected = observer.select([pod(n) for n in cases], 35)
        self.assertEqual([x["pod"] for x in selected], sorted(cases))

    def test_excludes_unrelated_pods_and_ue(self):
        self.assertEqual(observer.select(
            [pod("grafana"), pod("etcd"), pod("oai-nr-ue"), pod("cran-srsran-ue")], 35), [])

    def test_refuses_unbounded_inventory(self):
        with self.assertRaisesRegex(RuntimeError, "limit"):
            observer.select([pod("cran-oai-du-" + str(n)) for n in range(40)], 10)

    def test_log_markers_are_not_conformance(self):
        for text, marker in [
            ("Tx PDU: NGSetupRequest", "ngap_ng_setup_request"),
            ("Rx PDU: NGSetupResponse", "ngap_ng_setup_response"),
            ("F1SetupRequest", "f1ap_f1_setup_request"),
            ("InitialUEMessage", "ngap_initial_ue_message"),
        ]:
            self.assertIsNotNone(observer.PATTERNS[marker].search(text))

    def test_safe_output_no_overwrite_and_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "verdict.json"
            observer.safe_output(target, {"standards_conformance": "NOT_ASSESSED"})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(RuntimeError):
                observer.safe_output(target, {"x": 2})

    def test_main_inventory_uses_only_kubectl_get(self):
        pods = {"items": [pod("cran-oai-cu"), pod("hcran-srsran-du"), pod("open5gs-amf-0")]}
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            return json.dumps(pods)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "receipt.json"
            with mock.patch.object(observer, "run", side_effect=fake_run), mock.patch.object(
                observer.os, "geteuid", return_value=1000
            ):
                code = observer.main(["--output", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(calls, [["kubectl", "get", "pods", "-A", "-o", "json"]])
            report = json.loads(target.read_text())
            self.assertEqual(report["kubernetes_mutations"], 0)
            self.assertEqual(report["standards_conformance"], "NOT_ASSESSED")
            self.assertEqual(len(report["pod_inventory"]), 2)


if __name__ == "__main__":
    unittest.main()
