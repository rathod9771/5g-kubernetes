"""Non-native runtime policy tests; not daemon integration evidence."""
import importlib.util,json,os,pathlib,subprocess,sys,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
PKG=ROOT/'ci/experimental/open5gs-udm-runtime'
sys.path.insert(0,str(PKG))
import runtime as rt
import verify_artifact as va
sys.path.insert(0,str(ROOT/'scripts'))
import release_impact
from scenario_registry import load_registry

class RuntimePolicyTests(unittest.TestCase):
    def test_host_is_rejected_before_docker(self):
        with mock.patch.dict(os.environ,{},clear=True), mock.patch.object(rt,'docker') as d:
            with self.assertRaisesRegex(RuntimeError,'hosted'): rt.run(pathlib.Path('/unused'))
            d.assert_not_called()
    def test_real_subprocess_host_rejection(self):
        p=subprocess.run([sys.executable,str(PKG/'runtime.py'),'/unused'],env={'PATH':os.defpath},capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0); self.assertIn('GitHub-hosted runner required',p.stderr)
    def test_branch_is_rejected(self):
        with mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','GITHUB_REF':'refs/heads/main'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'branch'):rt.run(pathlib.Path('/unused'))
    def test_fresh_synthetic_config(self):
        s=rt.config('udm','172.29.248.3','172.29.248.2')
        self.assertIn('7777',s)
        for bad in ('supi','imsi','hnet','key:','mongodb','scp:'): self.assertNotIn(bad,s)
    def test_http2_success(self):
        m={'http_version':'2','http_code':404,'time_total':0.01,'remote_ip':'172.29.248.3','remote_port':7777}
        self.assertEqual(rt.admit_probe(m,404),m)
    def test_http1_rejected(self):
        with self.assertRaises(RuntimeError):rt.admit_probe({'http_version':'1.1','http_code':200},200)
    def test_bad_duration_rejected(self):
        with self.assertRaises(RuntimeError):rt.admit_probe({'http_version':'2','http_code':200,'time_total':6},200)
    def test_wrong_status_rejected(self):
        with self.assertRaises(RuntimeError):rt.admit_probe({'http_version':'2','http_code':500},200)
    def test_cleanup_only_owned_names(self):
        with mock.patch.object(rt,'docker') as d:
            self.assertEqual(rt.cleanup(['owned-nrf','owned-udm'],'owned-network'),[])
            self.assertEqual(d.call_args_list,[mock.call('rm','-f','owned-udm'),mock.call('rm','-f','owned-nrf'),mock.call('network','rm','owned-network')])
    def test_cleanup_failure_is_visible(self):
        with mock.patch.object(rt,'docker',side_effect=RuntimeError()): self.assertEqual(rt.cleanup(['owned'],'network'),['owned','network'])
    def test_bad_run_provenance_fails_before_read(self):
        with self.assertRaisesRegex(ValueError,'provenance'):va.verify(pathlib.Path('/unused'),pathlib.Path('/unused'),{'id':1})
    def test_original_pins(self):
        pins=json.loads((PKG/'artifact-pins.json').read_text())
        self.assertEqual(pins['experimental-image.docker.tar.gz'],va.ARCHIVE)
        self.assertEqual(len(va.OCI.split(':')[1]),64)
        self.assertTrue(any(p.endswith('/open5gs-udmd') for p in pins))
    def test_runtime_scope_and_neighbors(self):
        registry=load_registry(ROOT)
        for name in PKG.iterdir():
            if name.is_file(): self.assertEqual(release_impact.classify(str(name.relative_to(ROOT)),registry),('non-package',set(),False))
        self.assertEqual(release_impact.classify('ci/experimental/open5gs-udm-runtime-other/a.py',registry)[0],'unknown')
    def test_workflow_is_not_native_build(self):
        s=(ROOT/'.github/workflows/experimental-open5gs-udm-runtime.yml').read_text()
        self.assertNotIn('docker build',s);self.assertNotIn('self-hosted',s)
        self.assertIn('45121234959',s);self.assertIn('if: always()',s)
        native=(ROOT/'.github/workflows/experimental-open5gs-udm-sbi.yml').read_text()
        self.assertNotIn('open5gs-udm-runtime',native)
    def test_required_daemon_cases_not_claimed(self):
        self.assertIn('BLOCKED',rt.BLOCKER)
        self.assertIn('return 1',(PKG/'runtime.py').read_text())
