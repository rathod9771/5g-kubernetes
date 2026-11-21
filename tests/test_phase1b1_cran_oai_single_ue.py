"""Phase 1B1 CI contract and falsified-evidence rejection tests, no network."""
import copy
import datetime as dt
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE = ROOT / "scripts" / "experiments"


def load(name):
    p = CODE / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, p)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


proof = load("phase1b1_verify_one_ue")
gate = load("phase1b1_verify_offline_run")
SHA = "a" * 40
BRANCH = "experiment/phase1b1-cran-oai-single-ue-20261121"


def accepted_events():
    now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
    events = []
    for i, stage in enumerate(proof.STAGES):
        obj = {"time": (now + dt.timedelta(seconds=i)).isoformat(),
               "seconds": float(i),
               "stage": stage, "status": "PASS"}
        if stage == "PHASE1B1":
            obj["scope"] = ("actual one-UE C-RAN OAI registration/PDU/TUN, "
                            "bidirectional F1-U NG-U G-PDUs and loss-free ICMP; "
                            "no AMF fault, no H-CRAN, no production deployment")
        events.append(obj)
    return events


def accepted_run():
    return {"id": 12345, "repository": {"full_name": "rathod9771/5g-kubernetes"},
            "name": "Offline Project Validation",
            "path": ".github/workflows/helm-validate.yml",
            "status": "completed", "conclusion": "success",
            "head_sha": SHA, "head_branch": BRANCH, "event": "push"}


class OfflineGateTests(unittest.TestCase):
    def test_good_run_same_sha_and_branch(self):
        result = gate.validate(accepted_run(), SHA, BRANCH)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["production_authorized"])

    def test_older_or_other_run_rejected(self):
        for field, val in (("head_sha", "b" * 40), ("head_branch", "other"),
                           ("conclusion", "failure"), ("event", "workflow_dispatch"),
                           ("status", "in_progress"), ("name", "fake")):
            with self.subTest(field=field):
                item = accepted_run()
                item[field] = val
                with self.assertRaises(gate.GateError):
                    gate.validate(item, SHA, BRANCH)

    def test_no_production_or_main_dispatch(self):
        for branch in ("main", "experiment/phase1b-cran-oai-runtime-20261121",
                       "experiment/hcran-stage5zg-qualified-freeze-20261119"):
            with self.subTest(branch=branch), self.assertRaises(gate.GateError):
                gate.validate(accepted_run(), SHA, branch)

    def test_wrong_repo_rejected(self):
        r = accepted_run()
        r["repository"]["full_name"] = "unrelated/repository"
        with self.assertRaises(gate.GateError):
            gate.validate(r, SHA, BRANCH)


class EvidenceTests(unittest.TestCase):
    def test_valid_event_sequence_is_not_itself_live_protocol_pass(self):
        self.assertGreater(len(proof.STAGES), 12)
        self.assertEqual(proof.ordered_events(accepted_events()), len(proof.STAGES) - 1)
        self.assertIn("NGU", proof.STAGES[13])

    def test_missing_and_reordered_events_rejected(self):
        a = accepted_events()
        for case in (a[:-1], a[:9] + a[10:],
                     a[:2] + a[3:4] + a[2:3] + a[4:],
                     a[:2] + a[2:3] + a[2:]):
            with self.assertRaises(proof.ProofError):
                proof.ordered_events(case)

    def test_fail_and_incomplete_events_rejected(self):
        for status in ("FAIL", "INCOMPLETE", "NOT_RUN"):
            a = accepted_events()
            a[-1]["status"] = status
            with self.assertRaises(proof.ProofError):
                proof.ordered_events(a)

    def test_extra_error_event_rejected(self):
        a = accepted_events()
        a.append({"stage": "STAGE4K", "status": "FAIL"})
        with self.assertRaises(proof.ProofError):
            proof.ordered_events(a)

    def test_timestamp_or_elapsed_corruption_rejected(self):
        for name, value in (("time", "nonsense"),
                            ("time", "2026-11-21T05:33:00"),
                            ("seconds", -1),
                            ("seconds", 5000),
                            ("seconds", True)):
            with self.subTest(name=name, value=value):
                a = accepted_events()
                a[7][name] = value
                with self.assertRaises(proof.ProofError):
                    proof.ordered_events(a)

    def test_broad_claim_or_wrong_scope_rejected(self):
        a = accepted_events()
        a[-1]["scope"] = "every 3GPP Rel16 protocol is certified"
        with self.assertRaises(proof.ProofError):
            proof.ordered_events(a)

    def test_missing_binary_packets_are_not_fabricated_into_a_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td)
            (p / "results.json").write_text(json.dumps(accepted_events()))
            with self.assertRaises(proof.ProofError):
                proof.verify(p, p)

    def test_runner_confined_to_disposable_Docker_and_C_RAN_manifests(self):
        src = (CODE / "phase1b1_cran_oai_single_ue.py").read_text()
        for expected in ('"cran-oai-cu-config"', '"cran-oai-du-config"',
                         '"phase1b1-cran-oai-ue"', '"PHASE1B1"',
                         '"T5-real-NGSetupResponse"', '"T7-F1SetupResponse"',
                         '"--rfsim"', 'super().ue()'):
            self.assertIn(expected, src)
        for disallowed in ("kubectl", "ssh ", "helm upgrade", "docker system prune",
                           "hcran-oai-macro-config", "hcran-oai-cu-config"):
            self.assertNotIn(disallowed, src)


if __name__ == "__main__":
    unittest.main()
