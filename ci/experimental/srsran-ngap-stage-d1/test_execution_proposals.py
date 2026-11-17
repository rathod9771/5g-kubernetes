"""Preparation tests only. Synthetic files/mocked HTTP; never run pip, venv or a build."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import yaml
import offline_python as off
import wheelhouse_acquire as acq
from pip_acquisition import Blocked
P=Path(__file__).parent
REG=P/'main-registration.WORKFLOW.PROPOSED.yml'
class ExecutionProposals(unittest.TestCase):
 def test_exact_installer_pin(self):
  self.assertEqual(off.PIN['version'],'26.2');self.assertEqual(off.PIN['sha256'],'931c303696af6fa3417112103b1cad26890e5a07eccb5b99783700e33f2b8aad');self.assertEqual(off.PIN['requires_python'],'>=3.10');self.assertEqual(off.PIN['size'],1816475)
 def test_python_platform_compatibility(self):self.assertTrue(off.compatibility((3,10,12),'x86_64'))
 def test_unreviewed_python_platform_blocks(self):
  for v,a in [((3,9),'x86_64'),((3,11),'x86_64'),((3,10),'aarch64')]:
   with self.subTest(v=v,a=a),self.assertRaises(Blocked):off.compatibility(v,a)
 def test_no_ensurepip_or_installer_bootstrap(self):
  commands=off.commands('/usr/bin/python3','/wheels','/pins','/venv');self.assertIn('--without-pip',commands[0]);self.assertIn('/wheels/pip-26.2-py3-none-any.whl/pip',commands[-1]);self.assertNotIn('--upgrade',' '.join(commands[-1]))
 def test_install_is_offline_local_only(self):
  c=off.commands('/python','/wheels','/pins','/venv')[-1]
  for flag in ('--no-index','--no-deps','--require-hashes','--only-binary=:all:','--no-compile'):self.assertIn(flag,c)
  self.assertEqual(c[c.index('--find-links')+1],'/wheels');self.assertFalse(any(x.startswith(('http:','https:')) for x in c))
 def test_zero_retry_controls_preserved(self):
  c=off.commands('/python','/wheels','/pins','/venv')[-1]
  for opt,val in [('--retries','0'),('--resume-retries','0'),('--timeout','15')]:self.assertEqual(c[c.index(opt)+1],val)
 def test_original_seven_wheel_hashes_unchanged(self):
  self.assertEqual(len(off.inventory()),8);self.assertEqual(off.inventory()[:-1],[dict(x,filename=x['wheel']) for x in json.loads((P/'python-wheels.PROPOSED.json').read_text())])
 def record(self):return {'filename':'SYNTHETIC_ONLY.whl','name':'synthetic','version':'1','sha256':__import__('hashlib').sha256(b'SYNTHETIC_NOT_A_PACKAGE').hexdigest()}
 def test_wheelhouse_positive_synthetic(self):
  with tempfile.TemporaryDirectory() as d:
   r=self.record();(Path(d)/r['filename']).write_bytes(b'SYNTHETIC_NOT_A_PACKAGE');self.assertTrue(off.verify_wheelhouse(d,[r]))
 def test_missing_or_extra_wheel_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(Blocked):off.verify_wheelhouse(d,[self.record()])
   (Path(d)/'unexpected').write_bytes(b'x')
   with self.assertRaises(Blocked):off.verify_wheelhouse(d,[])
 def test_corrupt_wheel_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   r=self.record();(Path(d)/r['filename']).write_bytes(b'CORRUPT')
   with self.assertRaises(Blocked):off.verify_wheelhouse(d,[r])
 def test_symlink_wheel_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   r=self.record();(Path(d)/r['filename']).symlink_to('/dev/null')
   with self.assertRaises(Blocked):off.verify_wheelhouse(d,[r])
 def metadata(self):return {'info':{'version':off.PIN['version']},'urls':[{'filename':off.PIN['filename'],'digests':{'sha256':off.PIN['sha256']},'packagetype':'bdist_wheel','yanked':False,'url':off.PIN['url']}]}
 def test_exact_metadata_selection(self):self.assertEqual(acq.select(off.PIN,self.metadata()),off.PIN['url'])
 def test_metadata_pin_mismatch_blocks(self):
  m=self.metadata();m['urls'][0]['digests']['sha256']='0'*64
  with self.assertRaises(Blocked):acq.select(off.PIN,m)
 def test_metadata_url_drift_blocks(self):
  m=self.metadata();m['urls'][0]['url']='https://files.pythonhosted.org/other'
  with self.assertRaises(Blocked):acq.select(off.PIN,m)
 def test_yanked_or_duplicate_metadata_blocks(self):
  for kind in ('yanked','duplicate'):
   m=self.metadata()
   if kind=='yanked':m['urls'][0]['yanked']=True
   else:m['urls'].append(copy.deepcopy(m['urls'][0]))
   with self.subTest(kind=kind),self.assertRaises(Blocked):acq.select(off.PIN,m)
 def test_unapproved_url_no_connection(self):
  with patch.object(acq.http.client,'HTTPSConnection') as c:
   for url in ('http://pypi.org/x','https://evil.invalid/x','https://user:pass@pypi.org/x'):
    with self.assertRaises(Blocked):acq.https_get(url,100)
   c.assert_not_called()
 def test_redirect_no_retry(self):
  with patch.object(acq.http.client,'HTTPSConnection') as factory:
   factory.return_value.getresponse.return_value.status=302
   with self.assertRaises(Blocked):acq.https_get('https://pypi.org/x',100)
   self.assertEqual(factory.call_count,1);factory.return_value.request.assert_called_once()
 def test_registration_is_inert_and_same_path_proposal(self):
  if not REG.exists():self.fail('Missing separately supplied registration review fixture')
  w=yaml.load(REG.read_text(),Loader=yaml.BaseLoader);self.assertEqual(set(w['on']),{'workflow_dispatch'});self.assertEqual(w['permissions'],{})
  for job in w['jobs'].values():self.assertEqual(job['if'],'${{ false }}');self.assertEqual(job['runs-on'],'ubuntu-22.04');self.assertEqual(job['steps'],[{'run':':'}])
 def test_experimental_is_dispatch_only(self):
  w=yaml.load((P/'native-build.WORKFLOW.PROPOSED.yml').read_text(),Loader=yaml.BaseLoader);self.assertEqual(set(w['on']),{'workflow_dispatch'})
  for j in w['jobs'].values():self.assertIn("github.event_name == 'workflow_dispatch'",j['if']);self.assertIn("refs/heads/feature/layer3-srsran-ngap-native-build-d2b",j['if']);self.assertNotIn('||',j['if'])
 def test_actual_workflow_does_not_call_unpinned_pip(self):
  s=(P/'native-build.WORKFLOW.PROPOSED.yml').read_text();self.assertNotIn('python3-pip',s);self.assertNotIn('-m pip',s);self.assertIn('offline_python.py',s);self.assertIn('wheelhouse_acquire.py',s)
 def test_system_package_pin_limit_is_explicit(self):self.assertIn('BLOCKED_SYSTEM_PACKAGE_PINS_NOT_ESTABLISHED',(P/'P2-EXECUTION-PROPOSAL.md').read_text())
if __name__=='__main__':unittest.main()
