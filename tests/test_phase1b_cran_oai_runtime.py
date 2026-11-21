"""Phase 1B0 contract gates: tested without Docker, AMF, Kubernetes or UE."""
import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "scripts/experiments"


def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("phase1b_verify_offline_run")
proof = load("phase1b_verify_ngf1")
runner = load("phase1b_cran_oai_ngf1")
SHA = "914506f7f615bf3ff41d5a301c7def0f2b105b0a"
BRANCH = "experiment/phase1b-cran-oai-runtime-20261121"


def good_run():
    return dict(id=44868622250,
                repository=dict(full_name="rathod9771/5g-kubernetes"),
                name="Offline Project Validation",
                path=".github/workflows/helm-validate.yml",
                status="completed", conclusion="success", head_sha=SHA,
                head_branch=BRANCH, event="push")


def events():
    base = dt.datetime(2026, 11, 21, 0, 0, tzinfo=dt.timezone.utc)
    return [dict(stage=name, result="PASS",
                 time_utc=(base + dt.timedelta(seconds=i)).isoformat(),
                 elapsed_seconds=i + 1,
                 **({"scope": "real C-RAN CU/one-DU NG Setup and F1 Setup; no UE/PDU/user-plane"}
                    if name == "PHASE1B0" else {}))
            for i, name in enumerate(proof.EVENTS)]


class GateContracts(unittest.TestCase):
    def test_good_exact_branch_and_run(self):
        r = gate.validate(good_run(), SHA, BRANCH)
        self.assertEqual(r["status"], "PASS")
        self.assertFalse(r["production_authorized"])

    def test_wrong_sha_rejected(self):
        with self.assertRaises(gate.GateError):
            gate.validate(good_run(), "b" * 40, BRANCH)

    def test_wrong_branch_and_same_branch_sha_rejected(self):
        with self.assertRaises(gate.GateError):
            gate.validate(good_run(), SHA, "main")
        d = good_run()
        d["head_branch"] = "experiment/hcran-other"
        with self.assertRaises(gate.GateError):
            gate.validate(d, SHA, BRANCH)

    def test_run_must_be_full_offline_completed_push(self):
        for key, value in [
            ("status", "in_progress"), ("conclusion", "failure"),
            ("event", "workflow_dispatch"), ("name", "Fake green"),
            ("path", ".github/workflows/phase1-multiran-protocol-static.yml"),
            ("head_sha", "b" * 40),
        ]:
            with self.subTest(key=key):
                d = good_run()
                d[key] = value
                with self.assertRaises(gate.GateError):
                    gate.validate(d, SHA, BRANCH)

    def test_wrong_repository_rejected(self):
        d = good_run()
        d["repository"]["full_name"] = "attacker/fork"
        with self.assertRaises(gate.GateError):
            gate.validate(d, SHA, BRANCH)


class RuntimeEvidenceContracts(unittest.TestCase):
    def test_only_ngf1_smoke_qualifies(self):
        result = proof.verify(events())
        self.assertEqual(result["status"], "NGAP_F1AP_RUNTIME_SMOKE_PASS_UE_NOT_TESTED")
        self.assertEqual(result["scenario"], "cran-oai")
        self.assertEqual(result["not_run"], list(proof.TESTS_NOT_RUN))
        self.assertFalse(result["hp_cluster_touched"])
        self.assertFalse(result["production_authorized"])

    def test_missing_duplicate_or_reordered_events_rejected(self):
        valid = events()
        for altered in (valid[:-1], valid[:3] + valid[4:],
                        valid[:3] + [valid[3]] + valid[3:],
                        valid[:3] + valid[4:5] + valid[3:4] + valid[5:]):
            with self.subTest(n=len(altered)):
                with self.assertRaises(proof.EvidenceError):
                    proof.verify(altered)

    def test_no_synthetic_pass_or_false_ue_claim(self):
        good = events()
        for stage in ("T1-CU-real-NGSetupResponse", "T4-F1SetupResponse-to-DU-101"):
            bad = copy.deepcopy(good)
            next(e for e in bad if e["stage"] == stage)["result"] = "INCOMPLETE"
            with self.assertRaises(proof.EvidenceError):
                proof.verify(bad)
        bad = copy.deepcopy(good)
        bad[-1]["scope"] = "all 3GPP tests passed"
        with self.assertRaises(proof.EvidenceError):
            proof.verify(bad)

    def test_timestamps_and_time_budget_rejected(self):
        changes = [("time_utc", "not a date"), ("time_utc", "2026-11-21T12:00:00"),
                   ("elapsed_seconds", -1), ("elapsed_seconds", 3600)]
        for key, val in changes:
            bad = events()
            bad[2][key] = val
            with self.assertRaises(proof.EvidenceError):
                proof.verify(bad)
        bad = events()
        bad[4]["elapsed_seconds"] = 0
        with self.assertRaises(proof.EvidenceError):
            proof.verify(bad)

    def test_fails_on_any_explicit_runner_failure(self):
        bad = events()
        bad.append(dict(stage="cleanup-capture", result="FAIL"))
        with self.assertRaises(proof.EvidenceError):
            proof.verify(bad)


class RunnerSourceContracts(unittest.TestCase):
    def test_real_single_du_architecture_does_not_use_hcran_label(self):
        self.assertEqual(runner.NET, "phase1b-cran-oai-ngf1")
        self.assertEqual(set(runner.NAMES), {"amf", "nrf", "cu", "du"})
        self.assertEqual(runner.NGAP.pattern, r"Received NGSetupResponse from AMF")
        self.assertTrue(runner.F1REQ.search("Received F1 Setup Request from gNB_DU 101"))
        self.assertTrue(runner.F1RESP.search("DU 101 successfully sending F1 Setup Response"))
        self.assertIn("cran-oai-cu-config", (BASE / "phase1b_cran_oai_ngf1.py").read_text())
        self.assertIn("cran-oai-du-config", (BASE / "phase1b_cran_oai_ngf1.py").read_text())
        self.assertNotIn("hcran-oai", (BASE / "phase1b_cran_oai_ngf1.py").read_text())

    def test_runner_uses_only_disposable_docker_scope(self):
        src = (BASE / "phase1b_cran_oai_ngf1.py").read_text()
        for banned in ("kubectl", "helm upgrade", "ssh ", "delete --all", "docker system prune"):
            self.assertNotIn(banned, src)
        self.assertIn('self.require_live("cu", "du", "amf")', src)


if __name__ == "__main__":
    unittest.main()
