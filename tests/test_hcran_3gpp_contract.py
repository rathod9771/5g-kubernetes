#!/usr/bin/env python3
"""Offline contract tests for RIC-free, 3GPP-oriented H-CRAN profiles.

Helm renders only. No connection to a 5GC, UE, K8s cluster, OSM or RIC.
Rendered protocol references are NOT 3GPP conformance evidence.
"""
import importlib.util
import pathlib
import re
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPECS = {
    "oai": {
        "cu_chart": "helm/cran-oai/cu",
        "du_chart": "helm/cran-oai/du",
        "f1u": 38472,
        "cu_conf": "gnb.conf",
        "du_conf": "gnb.conf",
    },
    "srsran": {
        "cu_chart": "helm/cran-srsran/cu",
        "du_chart": "helm/cran-srsran/du",
        "f1u": 2152,
        "cu_conf": "cu.yml",
        "du_conf": "du.yml",
    },
}
FORBIDDEN = re.compile(r"\b(?:e2_agent|near_ric_ip_addr|flexric|E2SM[-_]KPM|E2SM[-_]RC)\b", re.I)


def render(chart, profile, release, overrides=()):
    cmd = [
        "helm", "template", release, chart,
        "-f", chart + "/values-hcran" + profile + ".yaml",
        "--set-string", "image.reference=example.invalid/hcran-ci:static-only",
        "--set-string", "image.pullPolicy=Never",
        *overrides,
    ]
    run = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, timeout=45, check=False)
    return run


def objects(output):
    return [d for d in yaml.safe_load_all(output) if isinstance(d, dict)]


class HCRAN3GPPTests(unittest.TestCase):
    def test_ric_free_two_du_configs_and_transport(self):
        for implementation, spec in SPECS.items():
            with self.subTest(impl=implementation):
                bindings = [
                    ("cu", spec["cu_chart"], ""),
                    ("macro", spec["du_chart"], "-macro"),
                    ("small", spec["du_chart"], "-small"),
                ]
                emitted = {}
                for role, chart, suffix in bindings:
                    run = render(chart, suffix, f"hcran-{implementation}-{role}")
                    self.assertEqual(run.returncode, 0, run.stderr)
                    docs = objects(run.stdout)
                    self.assertEqual(len([d for d in docs if d.get("kind")=="Deployment"]),1)
                    self.assertEqual(len([d for d in docs if d.get("kind")=="Service"]),1)
                    configmaps = [d for d in docs if d.get("kind")=="ConfigMap"]
                    self.assertEqual(len(configmaps), 1)
                    rendered = configmaps[0]["data"][
                        spec["cu_conf"] if role == "cu" else spec["du_conf"]
                    ]
                    self.assertIsNone(FORBIDDEN.search(rendered), f"{implementation}-{role}: unexpected E2 RIC agent")
                    self.assertTrue("POD_IP" in rendered)
                    emitted[role] = (docs, rendered)
                cu_docs = emitted["cu"][0]
                self.assertIn("amf_ip_address", emitted["cu"][1]) if implementation=="oai" else self.assertIn("amf:", emitted["cu"][1])
                for role in ("macro", "small"):
                    self.assertIn("f1", emitted[role][1].lower())
                    service = next(x for x in emitted[role][0] if x["kind"]=="Service")
                    self.assertTrue(any(p["protocol"]=="SCTP" and p["port"]==38472
                                        for p in service["spec"]["ports"]))
                    self.assertTrue(any(p["protocol"]=="UDP" and p["port"]==spec["f1u"]
                                        for p in service["spec"]["ports"]))
                self.assertNotEqual(emitted["macro"][1], emitted["small"][1])
                cu_service = next(x for x in cu_docs if x["kind"]=="Service")
                self.assertTrue(any(p["protocol"]=="SCTP" and p["port"]==38412
                                    for p in cu_service["spec"]["ports"]))

    def test_attempts_to_enable_e2_are_rejected_only_for_hcran(self):
        for implementation,spec in SPECS.items():
            for flag in ("e2Agent.enabled", "ric.enabled", "e2.enabled"):
                with self.subTest(impl=implementation,flag=flag):
                    run = render(spec["cu_chart"], "", f"hcran-{implementation}-cu",
                                 ("--set", flag + "=true"))
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn("H-CRAN 3GPP profile refuses", run.stderr)

    def test_profile_explicitly_disables_ric_and_e2(self):
        for implementation,spec in SPECS.items():
            profile = yaml.safe_load((ROOT/spec["cu_chart"]/"values-hcran.yaml").read_text())
            self.assertEqual(profile["ranType"], "hcran")
            self.assertIs(profile["e2Agent"]["enabled"], False)
            self.assertIs(profile["ric"]["enabled"], False)

    def test_3gpp_gate_excludes_ric_and_fails_closed(self):
        path=ROOT/"scripts/ci_oai_ngap_readiness.py"
        spec=importlib.util.spec_from_file_location("hcran_readiness",path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        all_gate_names={n for n,_,_ in module.KNOWN}|set(module.NOT_VERIFIED)
        for excluded in ("E2_", "KPM_", "FLEXRIC", "RC_CONTROL", "NEAR_RT_RIC"):
            self.assertFalse(any(excluded in name for name in all_gate_names),
                             f"RIC gate found: {excluded}")
        for gate in ("INTRA_GNB_INTER_DU_MOBILITY", "RRC_REESTABLISHMENT_AFTER_RADIO_FAILURE",
                     "PDU_SESSION_MODIFICATION_RELEASE_AND_QOS",
                     "AMF_ONLY_FAULT_UNASSISTED_UE_RECOVERY", "STAGING_OSM_ROLLBACK"):
            self.assertIn(gate,module.NOT_VERIFIED)
        self.assertGreaterEqual(len(module.NOT_VERIFIED),10)

    def test_prometheus_grafana_monitoring_config_retained(self):
        for p in ("monitoring/kube-prometheus-stack-values.yaml",
                  "monitoring/open5gs-podmonitor.yaml",
                  "monitoring/ran-exporter/ran_exporter.py"):
            self.assertTrue((ROOT/p).is_file(),p)
        values=(ROOT/"monitoring/kube-prometheus-stack-values.yaml").read_text()
        self.assertIn("grafana",values.lower())


if __name__=="__main__":
    unittest.main()
