"""Offline structure/ownership regression; native execution is CI-only."""
import hashlib,json,pathlib,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
FIX=ROOT/'ci/experimental/open5gs-udm-response-fix'
class ResponseFixTests(unittest.TestCase):
 def test_patch_ownership_and_privacy(self):
  patch=(FIX/'upstream-response.patch').read_text()
  self.assertIn('stream_id = xact->assoc_stream_id;',patch)
  self.assertIn('ogs_sbi_stream_find_by_id(stream_id)',patch)
  self.assertIn('OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE, NULL,',patch)
  self.assertIn('"Required UDR service unavailable", NULL, NULL',patch)
  self.assertEqual(patch.count('+        ogs_sbi_xact_remove(xact);'),2)
  self.assertNotIn('+                    udm_ue->supi',patch)
 def test_guard_and_original_archive_pinned(self):
  source=ROOT/'ci/experimental/open5gs-udm-sbi/inputs/source.tar.gz'
  self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),'2657f22b164c471379418adbabc78b94e626a0efca58ef7b1b4c824ababe8f2b')
  self.assertEqual(json.loads((FIX/'source-pins.json').read_text())['guard_context_sha256'],'0b5207bde626ba5282187e3c14721c20def84c9a53d9ab31ee6eb0e6c8e7ee93')
 def test_sanitizer_scope_and_counts(self):
  s=(FIX/'test_lifecycle.py').read_text()
  self.assertIn("('address','undefined')",s);self.assertIn("==8",s)
  c=(FIX/'lifecycle.c').read_text()
  self.assertIn('free(x)',c);self.assertIn('errors==1&&removed==1',c)
 def test_strict_runtime_admission(self):
  s=(FIX/'run_integration.py').read_text()
  for required in ("a['http_code']==503","a['time_total']<2","a.get('only_generic_fields')","response.unlink()","candidate.json"):
   self.assertIn(required,s)
  self.assertIn("result['status']='PARTIAL' if instances else 'FAIL'",s)
 def test_new_workflow_isolated(self):
  s=(ROOT/'.github/workflows/experimental-open5gs-udm-response-fix.yml').read_text()
  for required in ('runs-on: ubuntu-22.04','contents: read','--network none','--cpus 2','--pids-limit 256','2.7.2-guard.2'):
   self.assertIn(required,s)
  for forbidden in ('kubectl','self-hosted','secrets.','helm upgrade'):
   self.assertNotIn(forbidden,s)
 def test_manifest(self):
  p=subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=FIX,capture_output=True)
  self.assertEqual(p.returncode,0,p.stdout.decode())
 def test_only_scoped_impact(self):
  import importlib.util,sys
  sys.path.insert(0,str(ROOT/'scripts'))
  spec=importlib.util.spec_from_file_location('impact',ROOT/'scripts/release_impact.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  self.assertIn('ci/experimental/open5gs-udm-response-fix/',m.NON_PACKAGE_PREFIXES)

 def test_error_projection_preserves_single_response_count(self):
  import importlib.util,sys
  from unittest.mock import patch
  from types import SimpleNamespace
  spec=importlib.util.spec_from_file_location('response_runtime',FIX/'run_integration.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  module.IMAGE='sha256:'+'a'*64
  with tempfile.TemporaryDirectory() as d:
   private=pathlib.Path(d);cfg=private/'attempt.conf';cfg.touch()
   (private/'response.json').write_text(json.dumps({'status':503,'title':'Required UDR service unavailable'}))
   (private/'headers.txt').write_text('HTTP/2 503\r\ncontent-type: application/problem+json\r\n\r\n')
   meta={'http_code':503,'http_version':'2','time_total':.01,'remote_ip':'172.29.250.3','remote_port':7777}
   with patch.object(module.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(meta).encode(),stderr=b'',returncode=0)):
    result=module.request('disposable',private,cfg)
   self.assertEqual(result['response_count'],1)
   self.assertEqual(result['problem_status'],503)
   self.assertTrue(result['only_generic_fields'])
   self.assertFalse((private/'response.json').exists());self.assertFalse((private/'headers.txt').exists())
