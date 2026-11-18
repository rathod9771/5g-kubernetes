"""Offline publication admission regression tests; no acquisition/build execution."""
import copy,json,math,subprocess,sys,tempfile,unittest
from unittest.mock import patch
import native_build_admission as nba
from test_native_build_admission import fixture as admission_fixture
from pathlib import Path
import yaml
import build_policy as bp
import pip_acquisition as pip
import test_build_harness as original
import finalize_build_evidence as finalizer
P=Path(__file__).parent
def synthetic_native_pins(case):
    graph,review,expanded,pins=admission_fixture()
    for name,value in [('PINNED_OPEN5GS_GRAPH',nba._sha(graph)),
                       ('PINNED_EXPANSION_STDOUT','2'*64),
                       ('PINNED_COMMANDS',pins)]:
        p=patch.object(nba,name,value);p.start();case.addCleanup(p.stop)

class Publication(unittest.TestCase):
  def setUp(self):synthetic_native_pins(self)
 def workflow(self):return yaml.load((P/'native-build.WORKFLOW.PROPOSED.yml').read_text(),Loader=yaml.BaseLoader)
 def fixture(self):return original.Harness().complete()
 def rejected(self,edit):
  x=self.fixture();edit(x)
  with self.assertRaises(bp.Blocked):bp.verify_complete(*x)
 # The five previously failing publication checks, now mandatory.
 def test_dispatch_only(self):self.assertEqual(set(self.workflow()['on']),{'workflow_dispatch'})
 def test_exact_branch_and_event_guard(self):
  for job in self.workflow()['jobs'].values():
   self.assertIn("github.ref == 'refs/heads/feature/layer3-srsran-ngap-native-build-d2b'",job['if']);self.assertIn("github.event_name == 'workflow_dispatch'",job['if']);self.assertNotIn("||",job['if'])
 def test_pip_explicit_zero_retries(self):
  argv=pip.install_command('/venv/python','/pins.txt')
  for key,value in [('--retries','0'),('--resume-retries','0'),('--timeout','15')]:self.assertEqual(argv[argv.index(key)+1],value)
 def test_missing_elapsed_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].pop('elapsed_seconds'))
 def test_nested_timeout_rejected(self):
  self.rejected(lambda x:x[0]['cmake_configuration'].update(commands=[self.command(timeout=True)]))
 def command(self,**kw):
  return dict({'argv':['/usr/bin/cmake'],'started_at':'2026-11-17T00:00:00+00:00','ended_at':'2026-11-17T00:00:01+00:00','elapsed_seconds':1,'exit_code':0,'signal':None,'timeout':False,'forced':False,'log_budget_exceeded':False,'classification':'PASS','log':'command.log','log_sha256':'a'*64},**kw)
 def test_good_nested_receipt(self):
  x=self.fixture();x[0]['cmake_configuration']['commands']=[self.command()];self.assertTrue(bp.verify_complete(*x))
 def test_incorrect_branch_rejected_by_exact_comparison(self):
  guard=self.workflow()['jobs']['build-only']['if'];self.assertNotIn('refs/heads/main',guard);self.assertNotIn('stage-c3',guard);self.assertNotIn("github.event_name == 'push'",guard)
 def test_publication_cannot_trigger_workflow(self):
  self.assertNotIn('push',self.workflow()['on']);self.assertNotIn('pull_request',self.workflow()['on'])
  for name in ('README.md','NATIVE-COMPILE-PLAN.md'):
   text=(P/name).read_text();self.assertNotIn('One branch push triggers',text);self.assertNotIn('push trigger permits',text);self.assertNotIn('feature/layer3-srsran-ngap-runtime-stage-d1',text)
 def test_manual_registration_missing_is_blocked(self):self.assertIn('Manual dispatch — BLOCKED', (P/'PUBLICATION-AND-ACQUISITION.md').read_text());self.assertNotIn('repository_dispatch',self.workflow()['on'])
 def test_unknown_pip_pin_blocks(self):
  with self.assertRaisesRegex(pip.Blocked,'APPROVED_PIP_VERSION_REQUIRED'):pip.admission('25.2',' '.join(pip.OPTIONS),pip.POLICY)
 def test_pip_supported_mock(self):self.assertTrue(pip.admission('25.2',' '.join(pip.OPTIONS),dict(pip.POLICY,approved_pip_version='25.2')))
 def test_pip_wrong_version_blocks(self):
  with self.assertRaises(pip.Blocked):pip.admission('22.0.2',' '.join(pip.OPTIONS),dict(pip.POLICY,approved_pip_version='25.2'))
 def test_pip_unsupported_option_blocks(self):
  for missing in pip.OPTIONS:
   with self.subTest(missing=missing),self.assertRaises(pip.Blocked):pip.admission('25.2',' '.join(x for x in pip.OPTIONS if x!=missing),dict(pip.POLICY,approved_pip_version='25.2'))
 def test_pip_policy_drift_blocks(self):
  with self.assertRaises(pip.Blocked):pip.admission('25.2',' '.join(pip.OPTIONS),dict(pip.POLICY,approved_pip_version='25.2',resume_retries=5))
 def test_pip_no_inherited_settings(self):
  env=pip.clean_environment('/private/home','/private/tmp');self.assertEqual(env['PIP_CONFIG_FILE'],'/dev/null');self.assertEqual(set(env),{'PATH','HOME','TMPDIR','LANG','LC_ALL','PIP_CONFIG_FILE'})
 def test_pip_no_prompts_or_checks(self):
  argv=pip.install_command('/venv/python','/pins.txt')
  for flag in ('--no-input','--disable-pip-version-check','--no-cache-dir','--require-hashes','--no-deps','--only-binary=:all:'):self.assertIn(flag,argv)
 def test_pip_offline_worker_does_not_download(self):self.assertNotIn('pip', (P/'build_worker.py').read_text())
 def test_missing_timestamps_rejected(self):
  for key in ('started_at','ended_at'):
   with self.subTest(key=key):self.rejected(lambda x:x[0]['cmake_configuration'].pop(key))
 def test_malformed_timestamps_rejected(self):
  for value in ('',None,'2026-99-99T00:00:00Z','2026-11-17','2026-11-17T00:00:00+05:30'):
   with self.subTest(value=value):self.rejected(lambda x:x[0]['cmake_configuration'].update(started_at=value))
 def test_invalid_durations_rejected(self):
  for value in (-1,None,True,float('nan'),float('inf'),'1'):
   with self.subTest(value=str(value)):self.rejected(lambda x:x[0]['cmake_configuration'].update(elapsed_seconds=value))
 def test_clock_step_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(elapsed_seconds=4))
 def test_backward_wall_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(ended_at='2026-11-16T23:59:59Z'))
 def test_timeout_zero_exit_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(timeout=True))
 def test_signal_zero_exit_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(signal=9))
 def test_alias_timeout_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(timed_out=True))
 def test_alias_signal_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(termination_signal=9))
 def test_missing_and_nonzero_exit_rejected(self):
  self.rejected(lambda x:x[0]['cmake_configuration'].pop('exit_code'));self.rejected(lambda x:x[0]['cmake_configuration'].update(exit_code=1))
 def test_boolean_exit_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(exit_code=False))
 def test_missing_supervisor_completion_rejected(self):self.rejected(lambda x:x[3].pop('supervisor_completed'))
 def test_supervisor_missing_timing_rejected(self):self.rejected(lambda x:x[3].pop('elapsed_seconds'))
 def test_supervisor_unsuccessful_terminal_rejected(self):self.rejected(lambda x:x[3]['observed_properties'].update(ExecMainStatus='9'))
 def test_missing_resource_evidence_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].pop('resource_limit_status'))
 def test_missing_phase_validation_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(validation_completed=False))
 def test_missing_output_hash_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(outputs={}))
 def test_invalid_hash_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(log_sha256='not-a-hash'))
 def test_command_outside_phase_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(commands=[self.command(ended_at='2026-11-17T00:00:02Z',elapsed_seconds=2)]))
 def test_forced_and_truncated_commands_rejected(self):
  for key in ('forced','log_budget_exceeded'):
   with self.subTest(key=key):self.rejected(lambda x:x[0]['cmake_configuration'].update(commands=[self.command(**{key:True})]))
 def test_missing_command_identity_rejected(self):self.rejected(lambda x:x[0]['cmake_configuration'].update(commands=[self.command(argv=[])]))
 def test_unsupported_runner_still_blocks(self):
  x=original.Harness().limits();x['network_isolated']=False
  with self.assertRaises(bp.Blocked):bp.resource_admission(x)
 def test_pip_cli_isolated_import_no_side_effects(self):
  r=subprocess.run([sys.executable,'-I',str(P/'pip_acquisition.py'),'--not-approved'],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=3)
  self.assertNotEqual(r.returncode,0);self.assertIn('HOSTED_ACQUISITION_ONLY',r.stderr);self.assertNotIn('ModuleNotFoundError',r.stderr)
 def test_missing_required_command_rejected(self):self.rejected(lambda x:x[0]['srsran_compilation'].update(commands=[]))
 def test_contradictory_supervisor_signal_rejected(self):self.rejected(lambda x:x[3].update(termination_signal=9))
 def test_contradictory_supervisor_timeout_rejected(self):self.rejected(lambda x:x[3].update(timed_out=True))
 def evidence(self,root):
  root=Path(root);w=root/'worker';w.mkdir();x=self.fixture()
  log=w/'phase.log';log.write_text('SYNTHETIC_PHASE_LOG');cmd=w/'command.log';cmd.write_text('SYNTHETIC_COMMAND_LOG')
  for phase,r in x[0].items():
   r.update(log=str(log),log_sha256=bp.digest(log),outputs={'phase.log':bp.digest(log)})
   for c in r['commands']:c.update(log=str(cmd),log_sha256=bp.digest(cmd))
   (w/(phase+'.receipt.json')).write_text(json.dumps(r))
  for name,value in [('toolchain.json',x[1]),('binary-inventory.json',x[2]),('enforcement.json',original.Harness().limits())]:(w/name).write_text(json.dumps(value))
  graph,review,expanded,_=admission_fixture()
  (w/'open5gs-commands.txt').write_text(graph)
  (w/'open5gs-rsp-review.json').write_text(json.dumps(review))
  (w/'open5gs-rsp-expanded.json').write_text(json.dumps(expanded))
  (w/'d2b-native-admission.json').write_text(json.dumps(nba.admit_reviewed_open5gs_graph(graph,review,expanded)))
  (root/'post-worker-guard-accounting.json').write_text(json.dumps({
      'classification':'POST_WORKER_SNAPSHOT_REQUIRES_TERMINAL_CONFIRMATION',
      'proof':{'worker_exit_code':0,'mode':'POST_WORKER_QUIESCENT_GUARD_HELD',
               'worker_accounting_complete':True,'terminal_cgroup_empty':False,
               'native_compilation_authorized':False}}))
  (root/'resource-usage.json').write_text(json.dumps({
      'usage':x[4],'accounting_mode':'POST_WORKER_QUIESCENT_GUARD_HELD',
      'terminal_accounting_complete':True,'guard_only_at_capture':True,
      'capture_boundary':'POST_WORKER_PRE_GUARD_EXIT'}))
  # Existing native fixture retained; add mandatory synthetic auxiliary evidence.
  from test_workflow_evidence import auxiliary_fixture
  auxiliary_fixture(root)
  import datetime
  base=datetime.datetime(2026,11,17,tzinfo=datetime.timezone.utc)
  for n,phase in enumerate(bp.PHASES):
   f=w/(phase+'.receipt.json');r=json.loads(f.read_text())
   a=(base+datetime.timedelta(seconds=2*n)).isoformat();b=(base+datetime.timedelta(seconds=2*n+1)).isoformat()
   r.update(started_at=a,ended_at=b,elapsed_seconds=1)
   for c in r['commands']:c.update(started_at=a,ended_at=a,elapsed_seconds=0)
   f.write_text(json.dumps(r))
  from test_workflow_evidence import inspection_fixture
  inspection_fixture(root)
  from test_workflow_evidence import toolchain_fixture
  toolchain_fixture(root)
  final=dict(x[3],status='PASS_BUILD_ONLY_ZERO_NATIVE_TESTS');self.rehash(root,final);return final
 def rehash(self,root,final):
  root=Path(root);final['evidence_sha256']={str(f.relative_to(root)):bp.digest(f) for f in root.rglob('*') if f.is_file() and f.name!='supervisor-final.json' and 'auxiliary' not in f.relative_to(root).parts and f.name!='preservation.json' and 'inputs' not in f.relative_to(root).parts}
  (root/'supervisor-final.json').write_text(json.dumps(final))
 def test_finalizer_complete_evidence_pass(self):
  with tempfile.TemporaryDirectory() as d:
   self.evidence(d);self.assertEqual(finalizer.validate(d)['classification'],'PASS_BUILD_ONLY')
 def test_finalizer_corrupted_command_log_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   final=self.evidence(d);(Path(d)/'worker/command.log').write_text('CORRUPTED');self.rehash(d,final)
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_corrupted_artifact_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   self.evidence(d);(Path(d)/'worker/toolchain.json').write_text('{}')
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_missing_supervisor_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   self.evidence(d);(Path(d)/'supervisor-final.json').unlink()
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_missing_resource_file_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   final=self.evidence(d);(Path(d)/'resource-usage.json').unlink();self.rehash(d,final)
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_invalid_timing_even_when_rehashed_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   final=self.evidence(d);f=Path(d)/'worker/cmake_configuration.receipt.json';v=json.loads(f.read_text());del v['elapsed_seconds'];f.write_text(json.dumps(v));self.rehash(d,final)
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_incomplete_supervisor_even_when_manifest_valid_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   final=self.evidence(d);del final['supervisor_completed'];self.rehash(d,final)
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_unbound_receipt_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   final=self.evidence(d);del final['evidence_sha256']['worker/cmake_configuration.receipt.json'];(Path(d)/'supervisor-final.json').write_text(json.dumps(final))
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
 def test_finalizer_unsupported_resource_readback_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   final=self.evidence(d);f=Path(d)/'worker/enforcement.json';v=json.loads(f.read_text());v['network_isolated']=False;f.write_text(json.dumps(v));self.rehash(d,final)
   with self.assertRaises(bp.Blocked):finalizer.validate(d)
if __name__=='__main__':unittest.main()
