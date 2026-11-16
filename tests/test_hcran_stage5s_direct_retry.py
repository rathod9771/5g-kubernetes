"""Stage5S: prove direct or bounded real NAS ordering without weakening fault gates.

Synthetic local fragments check the parser ONLY, not live UE/AMF acceptance.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
S5I=ROOT/"experiments/oai-ngap-2026w13/stage5i/run_bounded_recovery.py"
VERIFY=ROOT/"experiments/oai-ngap-2026w13/stage5i/verify_bounded_recovery.py"

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Stage5SAuthenticNASFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner=load("stage5s_runner",S5I)
        cls.verifier=load("stage5s_verifier",VERIFY)

    def make_log(self,flow):
        return "\n".join(flow)+"\n"

    def counts(self,attempts,retries):
        return {
            "Generate initial NAS message: Service Request":attempts,
            "Stage5I: schedule one bounded Service Request retry":retries,
        }

    def test_immediate_service_reject_is_valid_no_retry_required(self):
        raw=self.make_log(self.runner.DIRECT_FLOW)
        chain,missing,kind=self.runner.choose_recovery_path(raw,self.counts(1,0))
        self.assertFalse(missing)
        self.assertEqual(kind,"direct_service_reject")
        self.assertEqual([x["marker"] for x in chain],list(self.verifier.DIRECT_FLOW))

    def test_bounded_retry_before_reject_still_required_when_observed(self):
        raw=self.make_log(self.runner.RETRIED_FLOW)
        chain,missing,kind=self.runner.choose_recovery_path(raw,self.counts(2,1))
        self.assertFalse(missing)
        self.assertEqual(kind,"bounded_retry")
        self.assertEqual([x["marker"] for x in chain],list(self.verifier.RETRIED_FLOW))

    def test_no_service_reject_after_bounded_attempts_fails(self):
        raw=self.make_log(self.runner.RETRIED_FLOW[:3])
        chain,missing,kind=self.runner.choose_recovery_path(raw,self.counts(2,1))
        self.assertIsNone(kind)
        self.assertTrue(missing)
        self.assertNotEqual(len(chain),len(self.verifier.RETRIED_FLOW))

    def test_no_service_reject_on_first_request_fails(self):
        raw=self.runner.DIRECT_FLOW[0] + "\n" + "\n".join(self.runner.DIRECT_FLOW[2:])
        chain,missing,kind=self.runner.choose_recovery_path(raw,self.counts(1,0))
        self.assertIsNone(kind)
        self.assertTrue(missing)

    def test_fake_retry_without_second_service_request_fails(self):
        raw=self.make_log(self.runner.DIRECT_FLOW)
        chain,missing,kind=self.runner.choose_recovery_path(raw,self.counts(1,1))
        self.assertIsNone(kind)
        self.assertTrue(missing)

    def test_extra_request_cannot_masquerade_as_direct_path(self):
        raw=self.make_log(self.runner.DIRECT_FLOW)
        chain,missing,kind=self.runner.choose_recovery_path(raw,self.counts(2,0))
        self.assertIsNone(kind)
        self.assertTrue(missing)

    def test_protocol_and_gtpu_proof_remain_mandatory(self):
        with tempfile.TemporaryDirectory() as temp:
            ok,errors=self.verifier.check(temp)
            self.assertFalse(ok)
            self.assertTrue(errors)
        source=VERIFY.read_text()
        self.assertIn("postfault_gtpu_pcap_sha256",source)
        self.assertIn("post-AMF-gtpu-evidence.json",source)
        self.assertIn("Retry failure/exhaustion",source)
        self.assertIn("both_ue_icmp_recovered",source)
        self.assertIn("False positive/negative bounded Service Request retry claim",source)

    def test_workflow_no_hp_or_automatic_deployment(self):
        workflow=(ROOT/".github/workflows/hcran-stage5s-direct-or-retry-evidence.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",workflow)
        self.assertIn("trial: [1, 2]",workflow)
        self.assertIn("--cycles 3 --verify-gtpu",workflow)
        self.assertIn("verify_three_cycle_recovery.py",workflow)
        for unsafe in ("runs-on: self-hosted","kubectl apply","helm upgrade","osm ns-"):
            self.assertNotIn(unsafe,workflow)

if __name__=="__main__":
    unittest.main()
