"""Phase 1 multi-RAN protocol static matrix: fail-closed, offline, no network."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/experiments/phase1_protocol_matrix.py"
SPEC = importlib.util.spec_from_file_location("phase1_protocol_matrix", SOURCE)
phase = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phase)


def docs(kind, implementation="OAI", f1u=True, ngu=True):
    if implementation.lower() == "oai":
        config = ("amf_ip_address = ({ ipv4 = \"AMF_IP\"; });\nlocal_s_portd = 38472;" if kind == "cu"
                  else "MACRLCs = ({ remote_n_address = \"CU_IP\"; });\nrfsimulator = ({});")
    else:
        config = ("cu_cp:\n  amf:\n    addr: AMF_IP\n  f1ap:\n    bind_addr: POD_IP" if kind == "cu"
                  else "f1ap:\n  cu_cp_addr: CU_IP\nru_sdr:\n  device_driver: zmq")
    ports = [{"protocol": "SCTP", "port": 38472}]
    if kind == "cu":
        ports.append({"protocol": "SCTP", "port": 38412})
        if ngu:
            ports.append({"protocol": "UDP", "port": 2152})
    elif f1u:
        ports.append({"protocol": "UDP", "port": 2152})
    return [{"kind": "ConfigMap", "data": {"conf": config}},
            {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [
                {"image": "example.invalid/phase1-probe:offline"}]}}}},
            {"kind": "Service", "spec": {"ports": ports}}]


class Phase1ProtocolMatrix(unittest.TestCase):
    def test_registry_covers_expected_architectures(self):
        reg = phase.load_registry()
        self.assertEqual(len(reg), 14)
        self.assertTrue(all(name in reg for name in (
            "cran-oai", "cran-srsran", "oran-oai", "oran-srsran",
            "hcran-oai", "hcran-srsran", "fran-oai", "fran-srsran",
            "vcran-oai", "vcran-srsran", "cloudran-oai", "cloudran-srsran", "fran", "none")))
        self.assertEqual(reg["oran-srsran"]["generation_status"], "blocked")

    def test_ngap_f1ap_are_only_static_config_not_actual_protocol(self):
        cu = phase.check_config(docs("cu"), "cu", "OAI")
        du = phase.check_config(docs("du"), "du", "OAI")
        self.assertEqual(cu["NGAP_STATIC"], "CONFIGURED")
        self.assertEqual(cu["F1AP_STATIC"], "CONFIGURED")
        self.assertEqual(du["F1AP_STATIC"], "CONFIGURED")
        self.assertEqual(du["RF_BACKEND"], "RF_SIM_CONFIGURATION_PRESENT")

    def test_srsran_case_from_registry_is_respected(self):
        cu = phase.check_config(docs("cu", "srsRAN"), "cu", "srsRAN")
        du = phase.check_config(docs("du", "srsRAN"), "du", "srsRAN")
        self.assertEqual(cu["NGAP_STATIC"], "CONFIGURED")
        self.assertEqual(du["F1AP_STATIC"], "CONFIGURED")

    def test_missing_service_ports_are_detected(self):
        cu = docs("cu")
        cu[2]["spec"]["ports"] = [{"protocol": "TCP", "port": 38412}]
        p = phase.check_config(cu, "cu", "OAI")
        self.assertEqual(p["NGAP_STATIC"], "CONFIG_GAP")
        self.assertEqual(p["F1AP_STATIC"], "CONFIG_GAP")
        self.assertEqual(p["NGU_STATIC"], "SERVICE_PORT_ABSENT")
        du = phase.check_config(docs("du", f1u=False), "du", "OAI")
        self.assertEqual(du["F1U_STATIC"], "SERVICE_PORT_ABSENT")

    def test_blocked_srsran_oran_cannot_be_promoted_by_matrix(self):
        reg = phase.load_registry()
        result = phase.analyze_scenario("oran-srsran", reg, renderer=lambda *args: self.fail("must not render"))
        self.assertEqual(result["status"], "REGISTRY_BLOCKED")
        self.assertEqual(result["runtime_protocol_tests"]["NGAP_SETUP"], "NOT_RUN")
        self.assertFalse(result["deployment_performed"])

    def test_edge_only_fran_is_not_ue_protocol_qualification(self):
        reg = phase.load_registry()
        result = phase.analyze_scenario("fran", reg, renderer=lambda *args: self.fail("must not render"))
        self.assertEqual(result["status"], "NOT_SPLIT_RAN")
        self.assertEqual(result["runtime_protocol_tests"]["PDU_SESSION"], "NOT_RUN")

    def test_split_ran_never_claims_live_ue_pass(self):
        reg = phase.load_registry()
        e = copy.deepcopy(reg["cran-oai"])
        e["charts"] = e["charts"][:2]
        def fake(entry, chart, role, pos, root):
            if role == "cu":
                return {"NGAP_STATIC": "CONFIGURED", "F1AP_STATIC": "CONFIGURED",
                        "NGU_STATIC": "SERVICE_PORT_PRESENT", "config": ""}
            return {"F1AP_STATIC": "CONFIGURED", "F1U_STATIC": "SERVICE_PORT_PRESENT",
                    "RF_BACKEND": "RF_SIM_CONFIGURATION_PRESENT", "config": ""}
        out = phase.analyze_scenario("cran-oai", {"cran-oai": e}, renderer=fake)
        self.assertEqual(out["status"], "STATIC_CONFIG_PRESENT_RUNTIME_NOT_TESTED")
        self.assertTrue(all(x == "NOT_RUN" for x in out["runtime_protocol_tests"].values()))
        self.assertFalse(out["hp_cluster_touched"])

    def test_hcran_requires_distinct_du_ids_without_e2(self):
        reg = phase.load_registry()
        def fake(entry, chart, role, pos, root):
            if role == "cu":
                return {"NGAP_STATIC": "CONFIGURED", "F1AP_STATIC": "CONFIGURED",
                        "NGU_STATIC": "SERVICE_PORT_PRESENT", "config": ""}
            return {"F1AP_STATIC": "CONFIGURED", "F1U_STATIC": "SERVICE_PORT_PRESENT",
                    "RF_BACKEND": "RF_SIM_CONFIGURATION_PRESENT",
                    "config": "gNB_DU_ID = 100;"}
        result = phase.analyze_scenario("hcran-oai", reg, renderer=fake)
        self.assertEqual(result["status"], "CONFIG_BLOCKED")
        self.assertIn("HCRAN_DU_IDENTITIES_NOT_DISTINCT", result["blockers"])
        def e2(entry, chart, role, pos, root):
            x = fake(entry, chart, role, pos, root)
            if role == "cu":
                x["config"] = "e2_agent = { near_ric_ip_addr = 1.2.3.4 };"
            else:
                x["config"] = "gNB_DU_ID = " + str(100 + pos) + ";"
            return x
        result = phase.analyze_scenario("hcran-oai", reg, renderer=e2)
        self.assertIn("HCRAN_FORBIDS_E2_AGENT", result["blockers"])

    def test_unknown_scenario_fails_closed(self):
        with self.assertRaises(ValueError):
            phase.analyze_scenario("invented", phase.load_registry())
        with self.assertRaises(ValueError):
            phase.analyze_scenario("none", phase.load_registry())

    def test_duplicate_or_missing_manifest_fails_closed(self):
        bad = docs("cu")
        bad.append(copy.deepcopy(bad[2]))
        with self.assertRaises(ValueError):
            phase.check_config(bad, "cu", "OAI")
        bad2 = docs("du")
        bad2[1]["spec"]["template"]["spec"]["containers"] = []
        with self.assertRaises(ValueError):
            phase.check_config(bad2, "du", "srsRAN")


if __name__ == "__main__":
    unittest.main()
