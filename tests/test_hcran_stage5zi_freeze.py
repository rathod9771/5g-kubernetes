"""Stage5ZI freeze/staging gate contracts. Offline, no cluster/AMF/UE actions."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"experiments/oai-ngap-2026w13/stage5zi"
M=BASE/"stage5zg-experimental-freeze.json"
VERIFY=BASE/"verify_frozen_staging_source.py"
STAGING_WF=ROOT/".github/workflows/hcran-stage5zi-staging-static.yml"
LIVE_WF=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"
INVENTORY=ROOT/"scripts/hcran_staging_inventory.py"

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

class Stage5ZIFreezeContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod=load(VERIFY,"stage5zi_verify")
        cls.inv=load(INVENTORY,"stage5zi_inventory")

    def baseline(self):
        return json.loads(M.read_text())

    def stub_git(self, manifest):
        pins=manifest["pinned_path_blobs"]
        frozen=manifest["freeze"]["source_commit"]
        def do(*args):
            if args[0]=="cat-file" and args[2]==frozen+"^{commit}":
                return ""
            if args[0]=="rev-parse":
                spec=args[1]
                path=spec.split(":",1)[1]
                return pins[path]
            raise AssertionError("unexpected git read: "+repr(args))
        return do

    def verify_offline(self, data):
        with mock.patch.object(self.mod,"_git",side_effect=self.stub_git(self.baseline())):
            with mock.patch.object(self.mod.subprocess,"run",return_value=mock.Mock(returncode=0)):
                return self.mod.verify(data)

    def test_frozen_live_evidence_and_no_go(self):
        d=self.baseline()
        result=self.verify_offline(d)
        self.assertEqual(result["status"],"STATIC_FREEZE_VALIDATED")
        self.assertEqual(result["source_sha"],"7fa911ece3b510b09351bf19c722007365ba47bd")
        self.assertEqual(result["frozen_source_blobs_verified"],12)
        self.assertEqual(result["experimental_cycles_verified_by_manifest"],6)
        self.assertFalse(result["target_staging_authorized"])
        self.assertFalse(result["production_authorized"])
        self.assertEqual(result["target_inventory"],"NOT_COLLECTED")

    def test_bad_source_commit_fails_closed(self):
        d=self.baseline()
        d["freeze"]["source_commit"]="b"*40
        with self.assertRaises(self.mod.QualificationError):
            self.verify_offline(d)

    def test_recovery_gate_source_drift_fails_closed(self):
        d=self.baseline()
        path="experiments/oai-ngap-2026w13/stage5y/verify_live_amf_ingress.py"
        d["pinned_path_blobs"][path]="b"*40
        with self.assertRaises(self.mod.QualificationError):
            self.verify_offline(d)

    def test_unsafe_or_missing_pinned_path_fails_closed(self):
        d=self.baseline()
        d["pinned_path_blobs"]["../../.ssh/private"]="a"*40
        with self.assertRaises(self.mod.QualificationError):
            self.verify_offline(d)
        d=self.baseline()
        d["pinned_path_blobs"]={}
        with self.assertRaises(self.mod.QualificationError):
            self.verify_offline(d)

    def test_incorrect_evidence_or_production_claim_fails_closed(self):
        for mutation in ("bad_run","five_cycles","bypass_negative","prod_go","invalid_upstream"):
            with self.subTest(mutation=mutation):
                d=self.baseline()
                if mutation=="bad_run":d["passed_live_evidence"]["workflow_run_id"]=1
                if mutation=="five_cycles":d["passed_live_evidence"]["genuine_cycles_passed"]=5
                if mutation=="bypass_negative":d["passed_live_evidence"]["stage5u_negative_controls_rejected_per_trial"]=0
                if mutation=="prod_go":d["production_authorized"]=True
                if mutation=="invalid_upstream":d["freeze"]["upstream_oai_w45_ue_commit"]="e"*40
                with self.assertRaises(self.mod.QualificationError):
                    self.verify_offline(d)

    def test_live_workflow_has_manual_exact_commit_gate(self):
        w=LIVE_WF.read_text()
        trigger=w.split("\non:\n",1)[1].split("\npermissions:",1)[0]
        self.assertIn("workflow_dispatch:",trigger)
        self.assertNotIn("push:",trigger)
        self.assertIn("needs: offline-gate",w)
        self.assertIn("--cycles 3 --verify-gtpu",w)
        self.assertIn("stage5u/audit_repeatability.py",w)
        self.assertIn("stage5y/verify_live_amf_ingress.py",w)

    def test_staging_static_workflow_cannot_mutate_target(self):
        w=STAGING_WF.read_text()
        self.assertIn("experiment/hcran-stage5zi-controlled-staging-20261119",w)
        self.assertIn("verify_frozen_staging_source.py",w)
        self.assertIn("test_hcran_staging_inventory.py",w)
        for forbidden in ("kubectl apply","kubectl patch","kubectl delete",
                          "kubectl scale","helm upgrade","osm ns-","runs-on: self-hosted"):
            self.assertNotIn(forbidden,w)

    def test_existing_real_staging_inventory_is_blocked_without_target_evidence(self):
        report=self.inv.assess(None,None,None,None,None)
        self.assertEqual(report["status"],"BLOCKED")
        self.assertEqual(report["production_decision"],"NO_GO")
        self.assertFalse(report["production_authorized"])

if __name__=="__main__":
    unittest.main()
