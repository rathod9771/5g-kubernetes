"""Preparation tests, not native or daemon results."""
import importlib.util,json,os,pathlib,sys,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
PKG=ROOT/'ci/experimental/open5gs-udm-driver'
spec=importlib.util.spec_from_file_location('l3_udm_driver',PKG/'run_driver.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
sys.path.insert(0,str(ROOT/'scripts'))
import release_impact
from scenario_registry import load_registry

class DriverPreparationTests(unittest.TestCase):
    def test_six_results_and_wrong_port_are_explicit(self):
        for case,port in d.PORTS.items():
            value={'case':case,'status':'PASS','port':port,'prior_port':7777 if case=='E' else 0,'kind':'actual-library-driver'}
            self.assertEqual(d.admit(case,value),value)
        self.assertEqual(d.PORTS['D'],80)
    def test_false_port_fails(self):
        with self.assertRaises(ValueError):d.admit('A',{'case':'A','status':'PASS','port':80})
    def test_no_synthetic_case_can_claim_daemon_pass(self):
        with self.assertRaises(ValueError):d.admit('A',{'case':'A','status':'PASS','port':7777,'prior_port':0,'kind':'daemon'})
    def test_wrong_host_rejected_before_docker(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(d,'docker') as run:
            with self.assertRaises(RuntimeError):d.run(pathlib.Path('/unused'))
            run.assert_not_called()
    def test_probe_checks_http2_and_destination(self):
        data={'http_code':200,'http_version':'2','remote_port':7777,'remote_ip':'172.29.249.2','time_total':.01}
        with mock.patch.object(d,'docker',return_value=b'non-subscriber-static-control\n'+json.dumps(data).encode()):
            self.assertEqual(d.probe('internal',7777)['http_version'],'2')
    def test_probe_rejects_http1(self):
        data={'http_code':200,'http_version':'1.1','remote_port':7777,'remote_ip':'172.29.249.2'}
        with mock.patch.object(d,'docker',return_value=b'non-subscriber-static-control\n'+json.dumps(data).encode()):
            with self.assertRaises(ValueError):d.probe('internal',7777)
    def test_body_is_static_and_bounded(self):
        with mock.patch.object(d,'docker',return_value=b'x'*8193):
            with self.assertRaises(ValueError):d.probe('internal',7777)
    def test_actual_library_driver_has_no_subscriber_input(self):
        s=(PKG/'selection_driver.c').read_text()
        for forbidden in ('supi','imsi','suci','udm_ue','socket(','curl_'):self.assertNotIn(forbidden,s.lower())
        self.assertIn('ogs_sbi_client_find_by_service_name',s)
        self.assertIn('ogs_sbi_client_apiroot',s)
    def test_build_uses_artifact_not_new_libraries(self):
        s=(PKG/'build_driver.sh').read_text()
        self.assertNotIn('ninja ',s);self.assertNotIn('meson compile',s)
        self.assertIn('/artifact/root/opt/open5gs/lib',s)
        self.assertIn('cmp /package-versions.txt',s)
        # Match the validated upstream compile database for its inline headers.
        self.assertIn('-Werror -Wno-sign-compare',s)
    def test_workflow_not_candidate_rebuild_or_daemon_rerun(self):
        s=(ROOT/'.github/workflows/experimental-open5gs-udm-driver.yml').read_text()
        self.assertNotIn('Runtime.Dockerfile',s);self.assertNotIn('runtime.py evidence',s)
        self.assertIn('--network none --cpus 2',s);self.assertIn('env -i PATH=',s)
        for word in ('self-hosted','kubectl','OSM_PASSWORD'):self.assertNotIn(word,s)
    def test_exact_impact_scope(self):
        registry=load_registry(ROOT)
        for p in PKG.iterdir():
            if p.is_file():self.assertEqual(release_impact.classify(str(p.relative_to(ROOT)),registry),('non-package',set(),False))
        self.assertEqual(release_impact.classify('ci/experimental/open5gs-udm-driver-other/run.py',registry)[0],'unknown')
