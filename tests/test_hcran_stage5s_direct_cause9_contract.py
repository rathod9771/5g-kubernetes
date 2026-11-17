"""Stage5S source-only contract tests. Synthetic logs are test fixtures, NOT a runtime PASS."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"experiments/oai-ngap-2026w13/stage5s"
def load(name):
    p=SRC/(name+".py")
    spec=importlib.util.spec_from_file_location("test_stage5s_"+name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class Stage5SContracts(unittest.TestCase):
    def test_direct_cause9_no_retry_qualifies_exact_path(self):
        m=load("run_bounded_recovery_stage5s")
        direct="\n".join(m.FLOW_DIRECT)
        selected,missing=m.ordered(direct,m.FLOW_DIRECT)
        self.assertFalse(missing)
        self.assertEqual([x["marker"] for x in selected],list(m.FLOW_DIRECT))
        _,retry_missing=m.ordered(direct,m.FLOW)
        self.assertTrue(retry_missing)

    def test_retry_path_has_independent_ordered_chain(self):
        m=load("run_bounded_recovery_stage5s")
        retry="\n".join(m.FLOW)
        selected,missing=m.ordered(retry,m.FLOW)
        self.assertFalse(missing)
        self.assertEqual(len(selected),8)

    def test_no_cause9_never_qualifies(self):
        m=load("run_bounded_recovery_stage5s")
        truncated="\n".join(x for x in m.FLOW_DIRECT if "cause #9" not in x)
        self.assertTrue(m.ordered(truncated,m.FLOW_DIRECT)[1])
        self.assertTrue(m.ordered(truncated,m.FLOW)[1])

    def test_all_runtime_claims_grounded_in_selected_path(self):
        runner=(SRC/"run_bounded_recovery_stage5s.py").read_text()
        checker=(SRC/"verify_bounded_recovery_stage5s.py").read_text()
        self.assertNotIn('"bounded_service_request_retry_observed_for_both":True',runner)
        self.assertIn('"nas_flow_type_by_ue"',runner)
        self.assertIn('main.get("nas_flow_type_by_ue")!=paths',checker)
        self.assertIn('selected_type=="retry"',checker)
        self.assertIn('selected_type=="direct_cause9"',checker)
        self.assertIn("missing real postfault GTPU proof",runner)
        self.assertIn("postfault_gtpu_pcap_sha256",checker)
        self.assertIn('"production_decision":"NO_GO"',runner)

    def test_three_cycle_wire_verification_remains_unmodified(self):
        stage5p=(ROOT/"experiments/oai-ngap-2026w13/stage5p/run_three_cycle_recovery.py").read_text()
        stage5s=(SRC/"run_three_cycle_recovery.py").read_text()
        # Experimental three-cycle runner differs only in Stage5S imports.
        normalized=stage5s.replace('str(ROOT / "stage5s"), ','').replace(
            'run_bounded_recovery_stage5s import build','run_bounded_recovery import build').replace(
            'verify_bounded_recovery_stage5s import check as check_nas',
            'verify_bounded_recovery import check as check_nas')
        self.assertEqual(normalized,stage5p)
        self.assertIn('len(set(captures)) != 3',stage5s)

    def test_no_deploy_and_disposable_host(self):
        w=(ROOT/".github/workflows/hcran-stage5s-direct-cause9-gate.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",w)
        self.assertNotIn("runs-on: self-hosted",w)
        self.assertIn("--cycles 3 --verify-gtpu",w)
        self.assertIn("stage5s/verify_three_cycle_recovery.py",w)
        for forbidden in ("helm upgrade","kubectl apply","osm ns-","deploy to production"):
            self.assertNotIn(forbidden,w)

if __name__=="__main__":
    unittest.main()
