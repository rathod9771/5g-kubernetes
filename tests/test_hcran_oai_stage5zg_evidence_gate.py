"""Synthetic negative tests of the exact OAI Stage5ZG historical acceptance gate."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "scripts/experiments/hcran_oai_stage5zg_evidence_gate.py"
spec = importlib.util.spec_from_file_location("hcran_oai_stage5zg_evidence_gate", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def fixture():
    run = {"id": m.RUN_ID, "head_sha": m.PIN, "status": "completed",
           "conclusion": "success", "event": "push",
           "head_branch": "experiment/hcran-stage5zg-sib1-message-handoff-20261119"}
    jobs = [{"id": k, "name": f"Stage5Y pinned 2UE 3-fault live AMF-cell ingress proof trial {k}/2",
             "conclusion": "success",
             "steps": [{"name": n, "conclusion": "success"} for n in m.NEEDED]}
            for k in (1,2)]
    artifacts = [{"name": f"hcran-stage5y-repeatability-trial-{k}", "expired": False}
                 for k in (1,2)]
    return run, jobs, artifacts

class EvidenceGateTests(unittest.TestCase):
    def test_accept_exact_two_complete_trials(self):
        result = m.inspect(*fixture())
        self.assertEqual(result["cycle_count_claim"], 6)
        self.assertEqual(len(result["trials"]), 2)
        self.assertEqual(result["production_decision"], "NO_GO")
        self.assertEqual(result["other_faults"], "NOT_VALIDATED")

    def test_reject_bad_run_status_sha_and_event(self):
        for key, bad in (("head_sha", "0"*40), ("conclusion", "failure"),
                         ("event", "workflow_dispatch"), ("id", 1)):
            with self.subTest(key=key):
                r,j,a=fixture()
                r[key]=bad
                with self.assertRaises(ValueError):m.inspect(r,j,a)

    def test_reject_missing_failed_or_duplicate_trial(self):
        r,j,a=fixture()
        with self.assertRaises(ValueError):m.inspect(r,j[:1],a)
        with self.assertRaises(ValueError):m.inspect(r,j+[j[0]],a)
        j[0]["conclusion"]="failure"
        with self.assertRaises(ValueError):m.inspect(r,j,a)

    def test_reject_missing_live_acceptance_step(self):
        r,j,a=fixture()
        j[0]["steps"][0]["conclusion"]="skipped"
        with self.assertRaises(ValueError):m.inspect(r,j,a)

    def test_reject_expired_or_missing_independent_artifact(self):
        r,j,a=fixture()
        a[1]["expired"]=True
        with self.assertRaises(ValueError):m.inspect(r,j,a)
        a[1]["expired"]=False
        with self.assertRaises(ValueError):m.inspect(r,j,a[:1])

if __name__ == "__main__":
    unittest.main()
