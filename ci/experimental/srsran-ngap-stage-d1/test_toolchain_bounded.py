"""Offline fixtures/mocked query and clocks only. No system inventory or native execution."""
import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import toolchain_lock as t
import build_receipts as receipts
from build_policy import Blocked
class BoundedToolchain(unittest.TestCase):
 def fixture(self,d,count=1):
  for i in range(count):(Path(d)/('file'+str(i))).write_bytes(b'SYNTHETIC_NOT_NATIVE')
 def test_large_inventory(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d,1024);lock=t.freeze([d],'SYNTHETIC 1');self.assertEqual(len(lock['files']),1024);self.assertTrue(t.verify(lock,'SYNTHETIC 1'))
 def test_file_count_budget(self):
  with tempfile.TemporaryDirectory() as d,patch.object(t,'MAX_FILES',2):
   self.fixture(d,3)
   with self.assertRaisesRegex(Blocked,'FILE_BUDGET'):t.freeze([d],'p 1')
 def test_byte_budget(self):
  with tempfile.TemporaryDirectory() as d,patch.object(t,'MAX_BYTES',1):
   self.fixture(d)
   with self.assertRaisesRegex(Blocked,'BYTE_BUDGET'):t.freeze([d],'p 1')
 def test_hash_deadline(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d)
   with self.assertRaisesRegex(Blocked,'TIMEOUT'):t.streaming_hash(Path(d)/'file0',deadline=0)
 def test_enumeration_deadline(self):
  with self.assertRaisesRegex(Blocked,'TIMEOUT'):list(t.entries(['/nonexistent'],0))
 def test_missing_path(self):
  with self.assertRaisesRegex(Blocked,'MISSING_TOOLCHAIN'):t.freeze(['/NO_SUCH_SYNTHETIC_INPUT'],'p 1')
 def test_changed_hash(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d);lock=t.freeze([d],'p 1');(Path(d)/'file0').write_bytes(b'CHANGED')
   with self.assertRaises(Blocked):t.verify(lock,'p 1')
 def test_added_header_in_search_directory(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d);lock=t.freeze([str(Path(d)/'file0')],'p 1');(Path(d)/'unselected.h').write_text('NEW')
   with self.assertRaises(Blocked):t.verify(lock,'p 1')
 def test_package_drift(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d);lock=t.freeze([d],'p 1')
   with self.assertRaisesRegex(Blocked,'PACKAGE_VERSION_DRIFT'):t.verify(lock,'p 2')
 def test_symlink_target_contents(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d);link=Path(d)/'link';link.symlink_to('file0');lock=t.freeze([str(link)],'p 1');(Path(d)/'file0').write_text('DRIFT')
   with self.assertRaises(Blocked):t.verify(lock,'p 1')
 def test_unapproved_policy(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d);lock=t.freeze([d],'p 1');lock['policy_sha256']='0'*64
   with self.assertRaises(Blocked):t.verify(lock,'p 1')
 def test_production_rejects_fixture_selection(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d);lock=t.freeze([d],'p 1')
   with self.assertRaisesRegex(Blocked,'UNAPPROVED_TOOLCHAIN_SELECTION'):t.verify(lock,'p 1',require_approved=True)
 def database(self):return {'compiler':{'version':'1','depends':'libc (>= 1), provider | alternative','provides':''},'libc':{'version':'1','depends':'','provides':''},'provider':{'version':'1','depends':'','provides':''},'alternative':{'version':'1','depends':'','provides':''}}
 def test_dependency_all_installed_alternatives(self):self.assertEqual(t.dependency_closure(self.database(),['compiler']),['alternative','compiler','libc','provider'])
 def test_virtual_dependency(self):
  db=self.database();db['compiler']['depends']='virtual';db['provider']['provides']='virtual (= 1)';self.assertEqual(t.dependency_closure(db,['compiler']),['compiler','provider'])
 def test_unresolved_dependency_blocks(self):
  db=self.database();db['compiler']['depends']='missing'
  with self.assertRaisesRegex(Blocked,'UNRESOLVED'):t.dependency_closure(db,['compiler'])
 def test_package_budget(self):
  with patch.object(t,'MAX_PACKAGES',1),self.assertRaisesRegex(Blocked,'PACKAGE_BUDGET'):t.dependency_closure(self.database(),['compiler'])
 def test_malformed_database(self):
  with self.assertRaises(Blocked):t.parse_database('MALFORMED')
 def test_no_broad_runtime_roots(self):self.assertNotIn('/usr/share',t.ROOTS);self.assertNotIn('/usr/lib',t.ROOTS);self.assertNotIn('/usr/include',t.ROOTS)
 def test_explicit_exclusions_and_support(self):
  for name in ['/usr/share/cmake-3.22/Modules/FindThreads.cmake','/usr/include/stdio.h','/usr/lib/gcc/x/cc1','/usr/lib/systemd/libsystemd-shared-249.so']:self.assertTrue(t.allowed_file(name))
  for name in ['/usr/share/doc/private','/usr/share/man/x','/etc/ssl/private/key','/usr/lib/ssl/private']:self.assertFalse(t.allowed_file(name))
 def test_diagnostics_survive_incomplete(self):
  with tempfile.TemporaryDirectory() as d:
   diag=t.Diagnostics(d);diag('file_hashing',current_path='/usr/bin/cc',files=1)
   value=json.loads((Path(d)/'toolchain-progress.json').read_text());self.assertEqual(value['classification'],'INCOMPLETE');self.assertEqual(value['stage'],'file_hashing')
 def test_query_timeout_receipt(self):
  with tempfile.TemporaryDirectory() as d,patch.object(t,'QUERY_SECONDS',0),patch.object(t.subprocess,'Popen') as popen:
   popen.return_value.poll.return_value=None;popen.return_value.returncode=-9
   with self.assertRaisesRegex(Blocked,'TIMEOUT'):t.query(['/usr/bin/dpkg-query','-W'],d,'SYNTHETIC-query',t.Diagnostics(d))
   r=json.loads((Path(d)/'SYNTHETIC-query.receipt.json').read_text());self.assertTrue(r['timeout']);self.assertEqual(r['signal'],9);popen.assert_called_once()
 def test_parent_timeout_is_not_pass(self):
  with tempfile.TemporaryDirectory() as d,patch.object(t,'command',return_value={'classification':'INCOMPLETE','timeout':True}) as run:
   with self.assertRaises(Blocked):t.run_bounded('--acquire',d,{},d)
   self.assertEqual(run.call_args.args[-1],180);self.assertEqual(json.loads((Path(d)/'toolchain-operation.receipt.json').read_text())['classification'],'INCOMPLETE')
 def test_phase_checkpoint_precedes_action(self):
  with tempfile.TemporaryDirectory() as d:
   def check():
    r=json.loads((Path(d)/'source_verification.receipt.json').read_text());self.assertEqual(r['classification'],'INCOMPLETE');self.assertIsNone(r['ended_at'])
   receipts.Recorder(d,{}).phase('source_verification',[],d,check)
 def test_incomplete_acquisition_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'dependency_acquisition.receipt.json').write_text('{"classification":"INCOMPLETE"}')
   with self.assertRaises(Blocked):t.verify_acquisition(d)
 def test_missing_diagnostics_prevent_overall_pass(self):
  from test_publication import Publication
  import finalize_build_evidence as finalizer
  with tempfile.TemporaryDirectory() as d:
   Publication().evidence(d);(Path(d)/'worker/acquisition-toolchain-operation.receipt.json').unlink()
   with self.assertRaises(Blocked):finalizer.validate(d)
 def test_policy_drift_prevents_overall_pass(self):
  from test_publication import Publication
  import finalize_build_evidence as finalizer
  with tempfile.TemporaryDirectory() as d:
   final=Publication().evidence(d);p=Path(d)/'worker/toolchain-lock.json';r=json.loads(p.read_text());r['policy_sha256']='0'*64;p.write_text(json.dumps(r));Publication().rehash(d,final)
   with self.assertRaises(Blocked):finalizer.validate(d)
 def test_root_packages_cover_existing_install_plan(self):
  import re
  text=(Path(t.__file__).parent/'native-build.WORKFLOW.PROPOSED.yml').read_text()
  line=next(x for x in text.splitlines() if 'apt-get' in x and 'install -y' in x)
  packages=line.split('--no-install-recommends ',1)[1].split()
  self.assertTrue(set(packages)<=set(t.ROOT_PACKAGES))
 def test_incomplete_progress_blocks_overall_pass(self):
  from test_publication import Publication
  import workflow_evidence as evidence
  with tempfile.TemporaryDirectory() as d:
   Publication().evidence(d);p=Path(d)/'worker/acquisition-toolchain-progress.json';p.write_text('{"classification":"INCOMPLETE"}')
   with self.assertRaises(Blocked):evidence.validate_toolchain_diagnostics(d)
 def test_broken_symlink_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   link=Path(d)/'link';link.symlink_to('missing')
   with self.assertRaisesRegex(Blocked,'UNRESOLVED_TOOLCHAIN_SYMLINK'):t.freeze([str(link)],'p 1')
 def test_symlink_cycle_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/'a';b=Path(d)/'b';a.symlink_to('b');b.symlink_to('a')
   with self.assertRaisesRegex(Blocked,'UNRESOLVED_TOOLCHAIN_SYMLINK'):t.freeze([str(a)],'p 1')
 def test_generated_bytecode_is_hashed(self):
  with tempfile.TemporaryDirectory() as d:
   module=Path(d)/'module.py';module.write_text('# synthetic');cache=Path(d)/'__pycache__';cache.mkdir();bytecode=cache/'module.cpython-310.pyc';bytecode.write_bytes(b'NOT_REAL_BYTECODE')
   paths=t.include_generated_caches({str(module)});self.assertIn(str(cache),paths);lock=t.freeze(sorted(paths),'p 1');bytecode.write_bytes(b'CHANGED')
   with self.assertRaises(Blocked):t.verify(lock,'p 1')
 def test_generated_cache_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   module=Path(d)/'module.py';module.write_text('# synthetic');(Path(d)/'__pycache__').symlink_to('/tmp')
   with self.assertRaises(Blocked):t.include_generated_caches({str(module)})
 def test_traversal_and_hashing_have_distinct_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   self.fixture(d,3);events=[];t.freeze([d],'p 1',lambda stage,**kw:events.append(stage))
   self.assertIn('filesystem_enumeration',events);self.assertIn('file_hashing',events);self.assertLess(events.index('filesystem_enumeration'),events.index('file_hashing'))
if __name__=='__main__':unittest.main()

class HostedChildEnvironment(unittest.TestCase):
 def probe(self,env):
  import subprocess,sys
  return json.loads(subprocess.check_output([sys.executable,'-I','-S','-B','-c','import os,json;print(json.dumps(dict(os.environ)))'],env=env,text=True,timeout=3))
 def test_real_child_exact_allowlist(self):
  import acquire_sources as a
  from build_policy import environment
  inherited={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','SECRET':'synthetic','HTTPS_PROXY':'synthetic','PATH':'/untrusted','RUNNER_TEMP':'/synthetic'}
  expected=environment(inherited,'/synthetic/home','/synthetic/tmp')
  actual=self.probe(a.acquisition_child_environment(inherited,'/synthetic/home','/synthetic/tmp'))
  self.assertEqual(actual,{**expected,'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'})
 def test_missing_or_wrong_hosted_flags(self):
  import acquire_sources as a
  for flags in ({},{'GITHUB_ACTIONS':'true'},{'RUNNER_ENVIRONMENT':'github-hosted'},{'GITHUB_ACTIONS':'false','RUNNER_ENVIRONMENT':'github-hosted'},{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted'}):
   with self.subTest(flags=flags),self.assertRaisesRegex(Blocked,'HOSTED_TOOLCHAIN_ACQUISITION_ONLY'):
    a.acquisition_child_environment(flags,'/home','/tmp')
 def test_compiler_and_loader_overrides_rejected(self):
  import acquire_sources as a
  for key in ('CC','CXX','CFLAGS','LDFLAGS','LD_PRELOAD','PYTHONPATH','TEST'):
   with self.subTest(key=key),self.assertRaisesRegex(Blocked,'UNSAFE_INHERITED_ENVIRONMENT'):
    a.acquisition_child_environment({'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted',key:'synthetic'},'/home','/tmp')
 def test_real_child_guard_rejects_unauthorized(self):
  import subprocess,sys
  from build_policy import environment
  for flags in ({},{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted'},{'GITHUB_ACTIONS':'false','RUNNER_ENVIRONMENT':'github-hosted'}):
   with tempfile.TemporaryDirectory() as d:
    env={**environment({},d,d),**flags}
    result=subprocess.run([sys.executable,'-B',str(Path(t.__file__)), '--acquire',d],env=env,capture_output=True,text=True,timeout=3)
    self.assertNotEqual(result.returncode,0)
    self.assertIn('HOSTED_TOOLCHAIN_ACQUISITION_ONLY',result.stderr)
    self.assertEqual(list(Path(d).iterdir()),[])
 def test_main_passes_validated_environment_to_actual_subprocess(self):
  import acquire_sources as a
  import os,sys
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'dependency-command-policy.json').write_text('{"sources":{}}')
   seen=[]
   def child(mode,out,env,cwd):
    self.assertEqual(mode,'--acquire');seen.append(self.probe(env))
   def phase(name,commands,cwd,verify,**kw):
    self.assertEqual(commands,[]);verify()
   with patch.object(a,'__file__',str(root/'acquire_sources.py')),patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_TEMP':d,'SECRET':'synthetic','HTTPS_PROXY':'synthetic'},clear=True),patch.object(sys,'argv',['acquire_sources.py','--github-acquire-only']),patch.object(a,'run_bounded',side_effect=child),patch.object(a.Recorder,'phase',side_effect=phase):
    a.main()
   self.assertEqual(len(seen),1);self.assertEqual(seen[0]['GITHUB_ACTIONS'],'true');self.assertEqual(seen[0]['RUNNER_ENVIRONMENT'],'github-hosted')
   self.assertNotIn('SECRET',seen[0]);self.assertNotIn('HTTPS_PROXY',seen[0]);self.assertNotIn('RUNNER_TEMP',seen[0])
 def test_native_worker_environment_remains_unprivileged(self):
  from build_policy import environment
  env=self.probe(environment({'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'},'/synthetic/home','/synthetic/tmp'))
  self.assertNotIn('GITHUB_ACTIONS',env);self.assertNotIn('RUNNER_ENVIRONMENT',env)
