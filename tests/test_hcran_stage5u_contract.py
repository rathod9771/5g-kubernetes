"""Stage5U trace and negative-control acceptance safety contracts.

Synthetic verifier mutations in this module are only source contracts.
The actual nine controls run on copies of each *real* RFsim trial evidence.
"""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"experiments/oai-ngap-2026w13/stage5u/audit_repeatability.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5u-repeatability-negative-controls.yml"

class Stage5UContracts(unittest.TestCase):
    def test_isolated_4x3_repeatability(self):
        workflow=WORKFLOW.read_text()
        self.assertIn("runs-on: ubuntu-24.04",workflow)
        self.assertIn("trial: [1, 2, 3, 4]",workflow)
        self.assertIn("max-parallel: 2",workflow)
        self.assertIn("--cycles 3 --verify-gtpu",workflow)
        self.assertIn("stage5s/verify_three_cycle_recovery.py",workflow)
        self.assertIn("stage5u/audit_repeatability.py",workflow)
        self.assertNotIn("runs-on: self-hosted",workflow)
        for forbidden in ("kubectl apply","helm upgrade","osm ns-","actions/download-artifact"):
            self.assertNotIn(forbidden,workflow)

    def test_same_pinned_stage5t_binary_builder(self):
        workflow=WORKFLOW.read_text()
        self.assertIn("scripts/hcran_build_pinned_w45_ue_stage5t.sh",workflow)
        self.assertIn("scripts/hcran_build_pinned_stage3_ngap_cu.sh",workflow)
        self.assertIn("hcran-stage5t-pinned-presib1:run-",workflow)
        self.assertNotIn("scripts/hcran_build_pinned_w45_ue_stage5s.sh",workflow)

    def test_all_corrupted_evidence_copies_must_be_rejected(self):
        source=SCRIPT.read_text()
        names=(
            "missing_real_gtpu_pcap","corrupt_gtpu_digest",
            "stale_before_pdu_ip","changed_ue_process_identity",
            "missing_third_recovery_cycle","missing_nas_service_reject",
            "missing_small_f1u_downlink","missing_per_cycle_runner_pass",
            "missing_bounded_protocol_trace"
        )
        for item in names:
            self.assertIn('"'+item+'"',source)
        self.assertIn("tempfile.TemporaryDirectory",source)
        self.assertIn("make_minimal_copy(root,dest)",source)
        self.assertIn('"rejected":notvalid', source.replace(" ",""))
        self.assertIn("len(report[\"negative_controls\"])==len(mutations())",source)
        self.assertIn("verify_stage5u(dest)",source)

    def test_no_unsafe_evidence_mutation_or_overclaim(self):
        source=SCRIPT.read_text()
        self.assertIn('from verify_three_cycle_recovery import verify as verify_three',source)
        self.assertIn('timeline.get("collection_issues")!=[]',source)
        self.assertIn('byrole.get("mac_bwp_deferral",0)>0',source)
        self.assertIn('byrole.get("nas_retries_exhausted",0)>0',source)
        self.assertIn('"production_authorized":False',source)
        self.assertIn('"production_decision":"NO_GO"',source)
        self.assertNotIn("docker exec",source)
        self.assertNotIn("kubectl",source)
        self.assertNotIn("helm upgrade",source)

if __name__=="__main__":
    unittest.main()
