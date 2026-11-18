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

 def test_merged_usr_alias_blocks_runtime_service_units(self):
  # Runner package inventory may identify the same service content using /lib or /usr/lib.
  cases=(
   'systemd/system/dbus-org.freedesktop.hostname1.service',
   'systemd/system/example.service',
   'systemd/user/example.service',
   'systemd/system-preset/90-systemd.preset',
   'systemd/user-preset/90-systemd.preset',
   'systemd/resolv.conf',
   'tmpfiles.d/systemd.conf',
   'sysusers.d/systemd.conf',
   'udev/rules.d/99-systemd.rules',
   'ssl/private/key.pem',
   'ssl/certs/ca.pem',
   'ssl/openssl.cnf',
  )
  for root in ('/lib/','/usr/lib/'):
   for suffix in cases:
    with self.subTest(path=root+suffix):
     self.assertFalse(t.allowed_file(root+suffix))

 def test_native_systemd_shared_libraries_retained_under_both_roots(self):
  for root in ('/lib/','/usr/lib/'):
   for suffix in ('systemd/libsystemd-shared-249.so', 'x86_64-linux-gnu/libsystemd.so.0'):
    with self.subTest(path=root+suffix):
     self.assertTrue(t.allowed_file(root+suffix))

 def test_masked_service_symlink_is_not_selected_or_followed(self):
  # Selection is a pure review predicate: no host service paths are probed.
  owned=['/lib/systemd/system/test-masked.service',
         '/usr/lib/systemd/system/test-masked.service',
         '/lib/systemd/libsystemd-shared-249.so',
         '/usr/include/stdio.h']
  selected=[p for p in owned if t.allowed_file(p)]
  excluded=[p for p in owned if not t.allowed_file(p)]
  self.assertEqual(selected,owned[2:]);self.assertEqual(excluded,owned[:2])
  self.assertFalse(t.allowed_file('/dev/null'))
  self.assertFalse(t.allowed_file('/dev/zero'))

 def test_symlink_into_dev_null_still_fails_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'masked.service';p.symlink_to('/dev/null')
   # Fixtures are allowed in freeze without approved production selection.
   with self.assertRaisesRegex(Blocked,'UNEXPECTED_TOOLCHAIN_OBJECT'):
    t.freeze([str(p)],'synthetic 1',approved=False)

 def test_environment_d_configs_excluded_under_merged_usr_names(self):
  # Systemd runtime environment configuration is not a native build input.
  for root in ('/usr/lib/','/lib/'):
   for suffix in ('environment.d/99-environment.conf','environment.d/10-provider.conf','environment.d/nested.conf'):
    with self.subTest(name=root+suffix):self.assertFalse(t.allowed_file(root+suffix))
  self.assertFalse(t.allowed_file('/etc/environment'))  # Target is never newly permitted.

 def test_environment_d_exclusion_is_directory_bounded(self):
  # Do not broaden the negative match to unrelated toolchain package paths.
  for root in ('/usr/lib/','/lib/'):
   for suffix in ('environment.d-backup/tool.so','environment.d.extra/tool.so','x86_64-linux-gnu/libsystemd.so.0','systemd/libsystemd-shared-249.so'):
    with self.subTest(name=root+suffix):self.assertTrue(t.allowed_file(root+suffix))

 def test_replay_environment_config_is_removed_before_symlink_visit(self):
  # Reconstruct the selection branch in acquire(): rejected names are not lstat'ed.
  from unittest.mock import patch
  names=('/usr/lib/environment.d/99-environment.conf','/lib/environment.d/99-environment.conf','/usr/lib/systemd/libsystemd-shared-249.so','/usr/include/stdio.h')
  chosen=[];excluded=[]
  with patch.object(Path,'is_symlink',side_effect=AssertionError('excluded config may not be inspected')) as probe:
   for name in names:
    if t.allowed_file(name):chosen.append(name)
    else:excluded.append(name)
  self.assertEqual(chosen,list(names[2:]));self.assertEqual(excluded,list(names[:2]));probe.assert_not_called()

 def test_environment_exclusion_is_bound_in_policy_digest(self):
  import hashlib
  self.assertIn('/usr/lib/environment.d/',t.POLICY['excluded_runtime_prefixes'])
  self.assertEqual(t.POLICY_SHA256,hashlib.sha256(json.dumps(t.POLICY,sort_keys=True).encode()).hexdigest())
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'synthetic').write_text('safe fixture')
   lock=t.freeze([d],'synthetic 1');lock['policy_sha256']='0'*64
   with self.assertRaisesRegex(Blocked,'UNAPPROVED_TOOLCHAIN_POLICY'):
    t.verify(lock,'synthetic 1')

 def test_runtime_exclusion_alias_is_bound_in_policy_hash(self):
  import hashlib
  self.assertEqual(t.POLICY['runtime_exclusion_aliases'],{'/lib/':'/usr/lib/'})
  digest=hashlib.sha256(json.dumps(t.POLICY,sort_keys=True).encode()).hexdigest()
  self.assertEqual(t.POLICY_SHA256,digest)
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'synthetic-tool').write_bytes(b'BUILD_ONLY_FIXTURE')
   lock=t.freeze([d],'synthetic 1');lock['policy_sha256']='0'*64
   with self.assertRaisesRegex(Blocked,'UNAPPROVED_TOOLCHAIN_POLICY'):
    t.verify(lock,'synthetic 1')

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


class PythonSiteCustomizeProvenance(unittest.TestCase):
 def test_exact_target_only_not_general_etc(self):
  self.assertTrue(t.allowed_file('/usr/lib/python3.10/sitecustomize.py'))
  self.assertTrue(t.allowed_file('/etc/python3.10/sitecustomize.py'))
  for name in ('/etc/environment','/etc/ssl/private/key','/etc/python3.11/sitecustomize.py','/etc/python3.10/other.py','/etc/python3.10/sitecustomize.py/evil'):
   with self.subTest(name=name):self.assertFalse(t.allowed_file(name))

 def test_exact_package_owned_link_and_target_pass(self):
  package_paths=('/usr/lib/python3.10/sitecustomize.py','/etc/python3.10/sitecustomize.py','/usr/lib/python3.10/os.py')
  self.assertIsNone(t.validate_python_sitecustomize_package_inputs(package_paths))
  chosen={p for p in package_paths if t.allowed_file(p)}
  self.assertEqual(chosen,set(package_paths))

 def test_missing_package_owned_target_or_link_blocks(self):
  for package_paths in (('/usr/lib/python3.10/sitecustomize.py',),('/etc/python3.10/sitecustomize.py',)):
   with self.subTest(package_paths=package_paths),self.assertRaisesRegex(Blocked,'UNOWNED_PYTHON_SITECUSTOMIZE'):
    t.validate_python_sitecustomize_package_inputs(package_paths)

 def test_only_exact_symlink_target_is_approved(self):
  self.assertIsNone(t.validate_python_sitecustomize_symlink('/usr/lib/python3.10/sitecustomize.py',Path('/etc/python3.10/sitecustomize.py')))
  for dest in ('/dev/null','/etc/environment','/etc/python3.10/unreviewed.py','/tmp/other.py'):
   with self.subTest(dest=dest),self.assertRaisesRegex(Blocked,'UNREVIEWED_PYTHON_SITECUSTOMIZE_TARGET'):
    t.validate_python_sitecustomize_symlink(t.PYTHON_SITE_LINK,Path(dest))

 def test_symlink_target_bytes_are_frozen_and_verified(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);actual=root/'config.py';actual.write_bytes(b'ORIGINAL_SYNTHETIC_PYTHON_CONFIG')
   alias=root/'alias.py';alias.symlink_to(actual)
   lock=t.freeze([str(alias)],'synthetic 1')
   self.assertIn(str(actual),lock['files'])
   self.assertEqual(lock['files'][str(actual)]['sha256'],t.streaming_hash(actual))
   actual.write_bytes(b'MUTATED_SYNTHETIC_PYTHON_CONFIG')
   with self.assertRaisesRegex(Blocked,'TOOLCHAIN_CONTENT_OR_INPUT_SET_DRIFT'):t.verify(lock,'synthetic 1')

 def test_python_target_cached_bytecode_is_exactly_allowlisted(self):
  root='/etc/python3.10/__pycache__/'
  for name in ('sitecustomize.cpython-310.pyc','sitecustomize.cpython-310.opt-1.pyc','sitecustomize.cpython-310.opt-2.pyc'):
   with self.subTest(name=name):self.assertTrue(t.allowed_file(root+name))
  for name in ('sitecustomize.cpython-311.pyc','sitecustomize.cpython-310.opt-3.pyc',
               'usercustomize.cpython-310.pyc','sitecustomize.cpython-310.pyc/evil'):
   with self.subTest(name=name):self.assertFalse(t.allowed_file(root+name))

 def test_unapproved_python_config_surfaces_remain_blocked(self):
  for name in ('/etc/python3.10/__pycache__/other.pyc','/etc/python3.10/other.py',
               '/etc/python3.10/sitecustomize.py.bak','/etc/python3.11/sitecustomize.py',
               '/etc/environment','/dev/null'):
   with self.subTest(name=name):self.assertFalse(t.allowed_file(name))

 def test_approved_freeze_requires_exact_link_and_hashes_target(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);target=root/'sitecustomize-target.py';target.write_bytes(b'APPROVED_TEST_ONLY')
   link=root/'sitecustomize-link.py';link.symlink_to(target)
   allowed={str(link),str(target)}
   with patch.object(t,'PYTHON_SITE_LINK',str(link)),patch.object(t,'PYTHON_SITE_TARGET',str(target)),patch.object(t,'allowed_file',side_effect=lambda n:n in allowed):
    lock=t.freeze([str(link)],'synthetic 1',approved=True)
    self.assertIn(str(target),lock['files'])
    self.assertEqual(lock['files'][str(target)]['sha256'],t.streaming_hash(target))
    self.assertTrue(t.verify(lock,'synthetic 1'))
    alternate=root/'UNAPPROVED_TARGET';alternate.write_bytes(b'REJECT_THIS')
    link.unlink();link.symlink_to(alternate)
    with self.assertRaisesRegex(Blocked,'UNREVIEWED_PYTHON_SITECUSTOMIZE_TARGET'):
     t.verify(lock,'synthetic 1')

 def test_new_python_policy_bound_in_sha256(self):
  import hashlib
  rules=t.POLICY['reviewed_python_sitecustomize']
  self.assertEqual(rules['link'],t.PYTHON_SITE_LINK)
  self.assertEqual(rules['target'],t.PYTHON_SITE_TARGET)
  self.assertEqual(rules['ownership'],'BOTH_PATHS_IN_SELECTED_DPKG_PACKAGE_LIST')
  self.assertEqual(t.POLICY_SHA256,hashlib.sha256(json.dumps(t.POLICY,sort_keys=True).encode()).hexdigest())
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'synthetic').write_bytes(b'OFFLINE')
   lock=t.freeze([d],'synthetic 1')
   lock['policy_sha256']='0'*64
   with self.assertRaisesRegex(Blocked,'UNAPPROVED_TOOLCHAIN_POLICY'):
    t.verify(lock,'synthetic 1')
