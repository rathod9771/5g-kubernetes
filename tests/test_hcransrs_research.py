"""H-CRAN srsRAN research-only Phase H0/H1: fail-closed offline contracts.

No RF peer, UE, Open5GS, Kubernetes, Docker runtime or HP interaction.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))


def load(name):
    source = ROOT / "scripts" / "experiments" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, source)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


wire = load("hcransrs_ngf1")
gate = load("hcransrs_verify_offline_run")
preflight = load("hcransrs_observer_preflight")
BRANCH = "experiment/hcran-srsran-3gpp-20261122"
SHA = "a" * 40


def events():
    return {"NGSetupRequest": [10], "NGSetupResponse": [12],
            "F1SetupRequest_macro": [21], "F1SetupResponse_macro": [24],
            "F1SetupRequest_small": [31], "F1SetupResponse_small": [35]}


def ci_run():
    return {
        "repository": {"full_name": "rathod9771/5g-kubernetes"},
        "name": "Offline Project Validation",
        "path": ".github/workflows/helm-validate.yml",
        "status": "completed", "conclusion": "success",
        "head_sha": SHA, "head_branch": BRANCH, "event": "push", "id": 999
    }


class GitHubGate(unittest.TestCase):
    def test_exact_commit_full_offline_success_only(self):
        self.assertEqual(gate.validate(ci_run(), SHA, BRANCH)["status"], "PASS")

    def test_reject_wrong_sha_branch_repo_status_workflow(self):
        for field, value in (
                ("head_sha", "b"*40), ("head_branch", "main"),
                ("status", "in_progress"), ("conclusion", "failure"),
                ("event", "workflow_dispatch"),
                ("name", "Fake Validation"),
                ("path", ".github/workflows/single-unit.yml"),
                ("repository", {"full_name": "another/repo"})):
            with self.subTest(field=field):
                item = ci_run()
                item[field] = value
                with self.assertRaises(gate.GateError):
                    gate.validate(item, SHA, BRANCH)

    def test_reject_c_ran_hcran_oai_and_main_workflow_ref(self):
        for branch in ("main", "experiment/phase1b2-cran-srsran-ngf1-20261121",
                       "experiment/hcran-stage5zg-qualified-freeze-20261119"):
            with self.subTest(branch=branch), self.assertRaises(gate.GateError):
                gate.validate(ci_run(), SHA, branch)


class DualF1Wire(unittest.TestCase):
    def test_two_distinct_du_addresses_and_standard_ports(self):
        self.assertEqual(len(wire.IP), 5)
        self.assertEqual(len(set(wire.IP.values())), 5)
        self.assertEqual(set(wire.MESSAGES), set(events()))
        self.assertNotEqual(wire.IP["macro"], wire.IP["small"])
        self.assertEqual(wire.MESSAGES["NGSetupResponse"][-1], 38412)
        for role in ("macro", "small"):
            self.assertEqual(wire.MESSAGES["F1SetupRequest_"+role],
                             ("f1ap", role, "cu", 38472))
            self.assertEqual(wire.MESSAGES["F1SetupResponse_"+role],
                             ("f1ap", "cu", role, 38472))
        self.assertEqual(wire.NET, "hcransrs-ngf1")

    def test_correct_two_du_ng_f1_sequence(self):
        wire.validate(events(), [])

    def test_single_du_cannot_claim_hcran(self):
        for role in ("macro", "small"):
            for kind in ("Request", "Response"):
                data = events()
                data["F1Setup"+kind+"_"+role] = []
                with self.subTest(role=role, kind=kind), self.assertRaises(wire.EvidenceError):
                    wire.validate(data, [])

    def test_malformed_duplicate_or_failure_rejected(self):
        for key in events():
            for bad in ([], [0], ["1"], [True]):
                x = events()
                x[key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(wire.EvidenceError):
                    wire.validate(x, [])
        with self.assertRaises(wire.EvidenceError):
            wire.validate(events(), [5])

    def test_bad_frame_order_rejected_for_each_du(self):
        for request, response in (
                ("NGSetupRequest", "NGSetupResponse"),
                ("F1SetupRequest_macro", "F1SetupResponse_macro"),
                ("F1SetupRequest_small", "F1SetupResponse_small")):
            x = events()
            x[request] = [100]
            x[response] = [99]
            with self.subTest(request=request), self.assertRaises(wire.EvidenceError):
                wire.validate(x, [])

    def test_exact_directional_sctp_filters_per_du(self):
        for key, (protocol, src, dst, port) in wire.MESSAGES.items():
            expression = wire.filter_expr(key.split("_")[0], protocol, src, dst, port)
            self.assertIn("ip.src == " + wire.IP[src], expression)
            self.assertIn("ip.dst == " + wire.IP[dst], expression)
            self.assertIn(str(port), expression)
            self.assertIn("sctp", expression)
            self.assertIn(protocol, expression)

    def test_real_capture_required_no_mocked_evidence_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(wire.EvidenceError):
                wire.decode(Path(tmp) / "not-captured.pcapng")
        for absent in ("VIRTUAL_RADIO_UE", "NAS_REGISTRATION", "PDU_SESSION",
                       "F1U_TRAFFIC", "NGU_TRAFFIC", "INTER_DU_MOBILITY",
                       "DRB_QOS", "RF_CONFORMANCE"):
            self.assertIn(absent, wire.NOT_RUN)


def synthetic_render(role, *, gnb=3585, du=100, pci=101, sector=1, bandwidth=20):
    name = "hcran-srsran-" + role
    config = {
        "log": {"filename": "/tmp/app.log", "all_level": "warning"},
    }
    if role == "cu":
        config.update({"gnb_id": gnb, "cu_up": {"f1u": {
            "socket": [{"bind_addr": "POD_IP"}]}},
            "cu_cp": {
                "max_nof_dus": 2, "f1ap": {"bind_addr": "POD_IP"},
                "amf": {"addr": "AMF_IP", "bind_addr": "POD_IP",
                        "supported_tracking_areas": [
                            {"tac": 1, "plmn_list": [
                                {"plmn": "20893", "tai_slice_support_list":
                                 [{"sst": 1, "sd": 66051}]}]}]}}})
    else:
        config.update({
            "gnb_id": gnb, "gnb_du_id": du, "f1ap": {
                "cu_cp_addr": "CU_IP", "bind_addr": "POD_IP"},
            "f1u": {"socket": [{"bind_addr": "POD_IP"}], "bind_port": 2152},
            "cell_cfg": {"pci": pci, "sector_id": sector,
                         "channel_bandwidth_MHz": bandwidth,
                         "plmn": "20893", "tac": 1, "dl_arfcn": 632628,
                         "band": 78, "common_scs": 30},
            "ru_sdr": {"device_driver": "zmq",
                       "device_args": "tx_port=tcp://127.0.0.1:2000,rx_port=tcp://127.0.0.1:2001",
                       "tx_gain": 75 if role == "macro" else 35}
        })
    docs = [
        {"kind": "Deployment", "metadata": {"name": name, "labels": {"ran-type": "hcran"}},
         "spec": {"template": {"metadata": {"labels": {"ran-type": "hcran"}}}}},
        {"kind": "ConfigMap", "metadata": {"name": name + "-config"},
         "data": {("cu" if role == "cu" else "du") + ".yml":
                  yaml.safe_dump(config, sort_keys=False)}},
    ]
    return yaml.safe_dump_all(docs)


class HCRAN3GPPProfile(unittest.TestCase):
    def verify(self, role, **kwargs):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (role + ".yaml")
            p.write_text(synthetic_render(role, **kwargs))
            return wire.render_config(p, role)

    def test_cu_two_du_capacity_and_n2_identity(self):
        config = self.verify("cu")
        self.assertEqual(config["cu_cp"]["max_nof_dus"], 2)
        self.assertEqual(config["cu_cp"]["f1ap"]["bind_addr"], wire.IP["cu"])
        self.assertEqual(config["cu_up"]["f1u"]["socket"], [{"bind_addr": wire.IP["cu"]}])
        # H1 is signaling-only: NG-U loopback avoids UDP/2152 collision;
        # this address is NOT acceptable evidence for UE or N3 traffic.
        self.assertEqual(config["cu_up"]["ngu"]["socket"], [{"bind_addr": "127.0.3.1"}])
        self.assertNotEqual(config["cu_up"]["ngu"]["socket"][0]["bind_addr"],
                            config["cu_up"]["f1u"]["socket"][0]["bind_addr"])

    def test_exact_macro_and_small_identities(self):
        a = self.verify("macro")
        b = self.verify("small", du=101, pci=102, sector=2, bandwidth=10)
        self.assertEqual((a["gnb_du_id"], b["gnb_du_id"]), (100,101))
        self.assertEqual((a["cell_cfg"]["pci"], b["cell_cfg"]["pci"]), (101,102))
        self.assertNotEqual(a["f1u"]["socket"], b["f1u"]["socket"])

    def test_reject_duplicated_cell_identity_or_wrong_gnb(self):
        for role, args in (("small", {"du": 100, "pci": 101, "sector": 1}),
                           ("macro", {"gnb": 3584}),
                           ("cu", {"gnb": 3584})):
            with self.subTest(role=role), self.assertRaises(wire.EvidenceError):
                self.verify(role, **args)

    def test_wrong_hcran_label_rejected(self):
        text = synthetic_render("cu").replace("ran-type: hcran", "ran-type: cran")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cu.yaml"
            p.write_text(text)
            with self.assertRaises(wire.EvidenceError):
                wire.render_config(p, "cu")


class CaptureSafety(unittest.TestCase):
    def test_dedicated_capture_engine_writes_stdout(self):
        cmd = preflight.capture_command(wire.BRIDGE, Path("/tmp/not-actually-used"))
        self.assertEqual(cmd[:5], ["sudo","-n","dumpcap","-i",wire.BRIDGE])
        self.assertEqual(cmd[-2:], ["-w", "-"])

    def test_non_github_runner_blocked(self):
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT":"github-hosted"}):
            with self.assertRaises(preflight.PreflightError):
                preflight.runner_only()

    def test_only_runner_temp_output_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base/"temp").mkdir()
            with mock.patch.dict("os.environ", {"RUNNER_TEMP": str(base/"temp")}):
                with self.assertRaises(preflight.PreflightError):
                    preflight.validate_output(base / "not-runner")

    def test_capture_only_does_not_claim_protocol(self):
        self.assertNotIn("NGAP_F1AP_WIRE_PASS",
                         (ROOT/"scripts/experiments/hcransrs_observer_preflight.py").read_text())


class Isolation(unittest.TestCase):
    def test_build_never_installs_to_hp_or_publishes_registry(self):
        build = (ROOT/"scripts/experiments/hcransrs_build.sh").read_text()
        self.assertIn("RUNNER_ENVIRONMENT", build)
        self.assertIn("11c9bbabb69873752500d676f55e0034f6caa5c5",build)
        for unsafe in ("kubectl ", "helm install", "ctr -n", "docker push",
                       "ssh ", "systemctl restart", "docker system prune"):
            self.assertNotIn(unsafe, build)


if __name__ == "__main__":
    unittest.main()
