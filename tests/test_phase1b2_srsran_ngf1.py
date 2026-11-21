"""Phase 1B2 offline contracts. No Docker, SCTP, real PCAP or live procedure."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/"experiments"/(name+".py"))
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
trial=load("phase1b2_srsran_ngf1")
gate=load("phase1b2_verify_offline_run")
BRANCH="experiment/phase1b2-cran-srsran-ngf1-20261121"
SHA="a"*40

def good():
    return {"NGSetupRequest":[11],"NGSetupResponse":[12],
            "F1SetupRequest":[20],"F1SetupResponse":[22]}

def run_meta():
    return dict(repository={"full_name":"rathod9771/5g-kubernetes"},
                name="Offline Project Validation",path=".github/workflows/helm-validate.yml",
                status="completed",conclusion="success",head_sha=SHA,
                head_branch=BRANCH,event="push",id=123)

class Gates(unittest.TestCase):
    def test_same_source(self):
        self.assertEqual(gate.validate(run_meta(),SHA,BRANCH)["status"],"PASS")
    def test_wrong_source_or_failed(self):
        for k,v in [("head_sha","b"*40),("head_branch","main"),
                    ("conclusion","failure"),("status","in_progress"),("event","workflow_dispatch")]:
            m=run_meta();m[k]=v
            with self.subTest(k=k),self.assertRaises(gate.GateError):
                gate.validate(m,SHA,BRANCH)
    def test_wrong_repo_or_workflow(self):
        for k,v in [("repository",{"full_name":"other/repo"}),("path","other.yml")]:
            m=run_meta();m[k]=v
            with self.assertRaises(gate.GateError):
                gate.validate(m,SHA,BRANCH)
    def test_main_and_oai_not_authorized(self):
        for branch in ["main","experiment/phase1b1-cran-oai-single-ue-20261121"]:
            with self.assertRaises(gate.GateError):
                gate.validate(run_meta(),SHA,branch)

class WireEvidence(unittest.TestCase):
    def test_ordered_frame_contract(self):
        trial.validate(good(),[])
    def test_missing_or_zero_frame(self):
        for k in good():
            for v in [[],[0],["1"],[True]]:
                m=good();m[k]=v
                with self.assertRaises(trial.EvidenceError):
                    trial.validate(m,[])
    def test_explicit_failure_not_accepted(self):
        with self.assertRaises(trial.EvidenceError):
            trial.validate(good(),[14])
    def test_reversed_setup_invalid(self):
        for req,rsp in (("NGSetupRequest","NGSetupResponse"),("F1SetupRequest","F1SetupResponse")):
            m=good();m[req]=[100];m[rsp]=[99]
            with self.assertRaises(trial.EvidenceError):
                trial.validate(m,[])
    def test_exact_expected_peer_filters(self):
        for label,(proto,src,dst,port) in trial.MESSAGES.items():
            f=trial.filter_expr(label,proto,src,dst,port)
            self.assertIn(proto+"."+label+"_element",f)
            self.assertIn("ip.src == "+trial.IP[src],f)
            self.assertIn("ip.dst == "+trial.IP[dst],f)
            self.assertIn("sctp.dstport == "+str(port),f)
        self.assertIn("NAS_REGISTRATION",trial.NOT_RUN)
    def test_no_capture_no_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"missing.pcapng"
            with self.assertRaises(trial.EvidenceError):
                trial.decode(p)
    def test_tshark_reconstruction_contract_only(self):
        # Fake decoder outputs for the negative/positive verifier unit test,
        # NEVER emit a runtime PASS or upload this synthetic input.
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"unit-test-only.pcapng";p.write_bytes(b"x"*256)
            seen=[]
            def fake(*args,**kw):
                expr=args[args.index("-Y")+1];seen.append(expr)
                if "Failure" in expr:return ""
                for k,v in good().items():
                    if k+"_element" in expr:return str(v[0])+"\n"
                return ""
            result=trial.decode(p,fake)
            self.assertEqual(len(seen),5)
            self.assertEqual(result["message_frame_counts"],dict.fromkeys(good(),1))
            def absent(*args,**kw):return ""
            with self.assertRaises(trial.EvidenceError):
                trial.decode(p,absent)

class ConfigContracts(unittest.TestCase):
    def test_cu_exact_profile_and_address(self):
        def docs(label):
            return [{"kind":"Deployment","metadata":{"name":"cran-srsran-cu","labels":{"ran-type":label}},
                     "spec":{"template":{"metadata":{"labels":{"ran-type":label}}}}},
                    {"kind":"ConfigMap","metadata":{"name":"cran-srsran-cu-config"},
                     "data":{"cu.yml":yaml.safe_dump({"gnb_id":3585,"cu_cp":{"f1ap":{"bind_addr":"POD_IP"},
                     "amf":{"addr":"AMF_IP","bind_addr":"POD_IP"}},"log":{"filename":"/tmp/cu.log","all_level":"warning"}})}}]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"cu.yaml"
            p.write_text(yaml.safe_dump_all(docs("cran")))
            self.assertEqual(trial.render_config(p,"cu")["cu_cp"]["amf"]["addr"],trial.IP["amf"])
            p.write_text(yaml.safe_dump_all(docs("hcran")))
            with self.assertRaises(trial.EvidenceError):
                trial.render_config(p,"cu")

if __name__=="__main__":unittest.main()
