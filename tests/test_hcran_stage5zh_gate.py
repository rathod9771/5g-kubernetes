"""Stage5ZH: fail-closed exact-SHA full-offline gate for optional live RFsim.

Never touches the network in tests and never runs a real AMF fault.
"""
import importlib.util
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
GATE=ROOT/"scripts/experiments/hcran_stage5zh_verify_offline_run.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"
SHA="a"*40
BRANCH="experiment/hcran-stage5zh-offline-gated-live-20261119"

def load():
    spec=importlib.util.spec_from_file_location("stage5zh_gate",GATE)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Stage5ZHContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g=load()

    def valid(self):
        return {
            "id":44457643438,
            "repository":{"full_name":"rathod9771/5g-kubernetes"},
            "name":"Offline Project Validation",
            "path":".github/workflows/helm-validate.yml",
            "status":"completed",
            "conclusion":"success",
            "head_sha":SHA,
            "head_branch":BRANCH,
            "event":"push",
        }

    def test_accept_only_exact_committed_full_offline_success(self):
        d=self.g.validate(self.valid(),SHA,BRANCH)
        self.assertEqual(d["status"],"PASS")
        self.assertEqual(d["verified_sha"],SHA)
        self.assertFalse(d["production_authorized"])
        self.assertEqual(d["production_decision"],"NO_GO")

    def test_reject_wrong_run_outcome(self):
        for key,value in (("status","in_progress"),("conclusion","failure"),
                          ("conclusion","cancelled"),("conclusion",None)):
            with self.subTest(key=key,value=value):
                r=self.valid();r[key]=value
                with self.assertRaises(self.g.GateError):
                    self.g.validate(r,SHA,BRANCH)

    def test_reject_other_commit_branch_repo_and_workflow(self):
        for key,value in (
            ("head_sha","b"*40),
            ("head_branch","experiment/hcran-other-run"),
            ("path",".github/workflows/fake.yml"),
            ("name","Fake offline result"),
            ("event","workflow_dispatch"),
            ("repository",{"full_name":"someone-else/fork"}),
        ):
            with self.subTest(key=key):
                r=self.valid();r[key]=value
                with self.assertRaises(self.g.GateError):
                    self.g.validate(r,SHA,BRANCH)
        with self.assertRaises(self.g.GateError):
            self.g.validate(self.valid(),"not-a-sha",BRANCH)
        with self.assertRaises(self.g.GateError):
            self.g.validate(self.valid(),SHA,"main")

    def test_confirmation_is_mandatory_before_any_network(self):
        with mock.patch.dict(os.environ,{
            "HCRAN_ISOLATED_CONFIRM":"",
            "HCRAN_OFFLINE_RUN_ID":"44457643438",
            "GITHUB_SHA":SHA,
            "GITHUB_REF_NAME":BRANCH,
            "GITHUB_TOKEN":"do-not-print",
        }):
            with mock.patch.object(self.g.request,"urlopen") as fetch:
                with self.assertRaises(self.g.GateError):
                    self.g.main()
                fetch.assert_not_called()

    def test_successful_read_only_git_api_mocks_no_token_leak(self):
        payload=json.dumps(self.valid()).encode()
        class FakeHttp:
            def __enter__(self):return io.BytesIO(payload)
            def __exit__(self,*_):return False
        with mock.patch.dict(os.environ,{
            "HCRAN_ISOLATED_CONFIRM":"ISOLATED_RFSIM_NO_DEPLOY",
            "HCRAN_OFFLINE_RUN_ID":"44457643438",
            "GITHUB_SHA":SHA,
            "GITHUB_REF_NAME":BRANCH,
            "GITHUB_TOKEN":"do-not-print",
        }):
            with mock.patch.object(self.g.request,"urlopen",return_value=FakeHttp()) as fetch:
                out=io.StringIO()
                with redirect_stdout(out):
                    self.g.main()
                result=json.loads(out.getvalue())
                self.assertEqual(result["status"],"PASS")
                self.assertNotIn("do-not-print",out.getvalue())
                self.assertIn("/actions/runs/44457643438",fetch.call_args.args[0].full_url)

    def test_invalid_run_id_rejected_before_network(self):
        with mock.patch.dict(os.environ,{
            "HCRAN_ISOLATED_CONFIRM":"ISOLATED_RFSIM_NO_DEPLOY",
            "HCRAN_OFFLINE_RUN_ID":"../../445",
            "GITHUB_SHA":SHA,
            "GITHUB_REF_NAME":BRANCH,
            "GITHUB_TOKEN":"do-not-print",
        }):
            with mock.patch.object(self.g.request,"urlopen") as fetch:
                with self.assertRaises(self.g.GateError):
                    self.g.main()
                fetch.assert_not_called()

    def test_expensive_live_matrix_requires_gate_and_no_automatic_push(self):
        w=WORKFLOW.read_text()
        trigger=w.split("\non:\n",1)[1].split("\npermissions:",1)[0]
        self.assertIn("workflow_dispatch:",trigger)
        self.assertNotIn("push:",trigger)
        self.assertIn("offline_run_id:",trigger)
        self.assertIn("confirm_isolated_no_deploy:",trigger)
        self.assertIn("  offline-gate:\n",w)
        self.assertIn("    needs: offline-gate\n",w)
        self.assertIn("hcran_stage5zh_verify_offline_run.py",w)
        self.assertIn("actions: read",w)
        for evidence in ("runs-on: ubuntu-24.04","trial: [1, 2]",
                         "--cycles 3 --verify-gtpu",
                         "stage5u/audit_repeatability.py",
                         "stage5y/verify_live_amf_ingress.py",
                         "hcran_build_pinned_w45_ue_stage5zg.sh"):
            self.assertIn(evidence,w)
        for banned in ("runs-on: self-hosted","kubectl apply","helm upgrade","osm ns-"):
            self.assertNotIn(banned,w)

if __name__=="__main__":
    unittest.main()
