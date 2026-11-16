"""Stage5S protocol acceptance contracts: fast path is valid, but NO fabricated PASS."""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=ROOT/"experiments/oai-ngap-2026w13/stage5s"
def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,SRC/filename)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

runner=load("s5s_runner","run_adaptive_recovery.py")
verifier=load("s5s_verifier","verify_adaptive_recovery.py")

FAST=(
    "Generate initial NAS message: Service Request\n"
    "Received NAS Service Reject message with cause #9\n"
    "Stage5G: cause #9, cleared stale GUTI and NAS security\n"
    "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE\n"
    "Received Registration Accept\n"
    "PDU Session Establishment Accept\n"
)
RETRY=(
    "Generate initial NAS message: Service Request\n"
    "Stage5I: schedule one bounded Service Request retry\n"
    "Generate initial NAS message: Service Request\n"
    +FAST.split("Received NAS Service Reject message",1)[1].join(["Received NAS Service Reject message",""])
)

class Stage5SContracts(unittest.TestCase):
    def test_immediate_cause9_without_retry_is_real_valid_order(self):
        chain,missing=runner.ordered(FAST,runner.FLOW)
        self.assertFalse(missing)
        self.assertEqual([x["marker"] for x in chain],list(runner.FLOW))
        self.assertEqual(FAST.count("Stage5I: schedule one bounded Service Request retry"),0)

    def test_observed_bounded_retry_requires_second_request(self):
        log=(
          "Generate initial NAS message: Service Request\n"
          "Stage5I: schedule one bounded Service Request retry\n"
          "Generate initial NAS message: Service Request\n"
          "Received NAS Service Reject message with cause #9\n"
          "Stage5G: cause #9, cleared stale GUTI and NAS security\n"
          "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE\n"
          "Received Registration Accept\n"
          "PDU Session Establishment Accept\n"
        )
        chain,missing=runner.ordered(log,runner.RETRY_FLOW)
        self.assertFalse(missing)
        self.assertEqual(len(chain),len(runner.RETRY_FLOW))

    def test_fake_retry_without_second_request_fails(self):
        fake=FAST.replace("Received NAS Service Reject message",
                          "Stage5I: schedule one bounded Service Request retry\nReceived NAS Service Reject message")
        _,missing=runner.ordered(fake,runner.RETRY_FLOW)
        self.assertTrue(missing)

    def test_no_cause9_or_reg_accept_is_never_valid(self):
        for token in ("Stage5G: cause #9", "Received Registration Accept",
                      "PDU Session Establishment Accept"):
            with self.subTest(token=token):
                _,missing=runner.ordered(FAST.replace(token,"ABSENT"),runner.FLOW)
                self.assertTrue(missing)

    def test_nonexistent_evidence_does_not_pass(self):
        with tempfile.TemporaryDirectory() as d:
            ok,why=verifier.check(d)
            self.assertFalse(ok)
            self.assertTrue(why)

    def test_workflow_rebuilds_and_does_not_deploy(self):
        wf=(ROOT/".github/workflows/hcran-stage5s-fastpath-acceptance.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04",wf)
        self.assertIn("run_protocol_trace_fastpath.py",wf)
        self.assertIn("verify_three_cycle_fastpath.py",wf)
        self.assertIn("--cycles 3 --verify-gtpu",wf)
        self.assertNotIn("actions/download-artifact",wf)
        self.assertNotIn("kubectl apply",wf)
        self.assertNotIn("helm upgrade",wf)

if __name__=="__main__":
    unittest.main()
