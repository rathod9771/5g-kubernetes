"""Execute copied production guardian as a module against isolated local fixtures.

Only external adapters/periodic payload are replaced; guardian, /proc accounting,
SQLite reader, timeout launcher, signal handling and subprocess traversal are real.
No production config, journal, APIs or collection is accessible from this fixture.
"""
import json,os,shutil,signal,sqlite3,subprocess,sys,tempfile,time,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class GuardianEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.package=self.root/'scripts/assurance'
        self.package.mkdir(parents=True);(self.package/'__init__.py').touch()
        for name in ('guarded_trial.py','memory.py','guard.py','diagnostics.py'):
            shutil.copy2(ROOT/'scripts/assurance'/name,self.package/name)
        (self.root/'ran-selector/venv/bin').mkdir(parents=True)
        (self.root/'ran-selector/venv/bin/python3').symlink_to(sys.executable)
        self.pre=self.root/'pre';self.pre.mkdir();self.trial=self.root/'trial'
        (self.root/'.runtime').mkdir();self.bin=self.root/'bin';self.bin.mkdir()
        pods=[]
        for role in ('cu','du'):
            pods.append({'kind':'Pod','metadata':{'name':role,'uid':role,'labels':{'component':role,'osm.etsi.org/ns-id':'ran'}},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{'ready':True,'name':role,'restartCount':0,'containerID':role,'imageID':role}]}})
        (self.root/'pods.json').write_text(json.dumps({'items':pods}))
        pre={'namespace':'offline','protected_ns':{'ran':{'_id':'ran'},'core':{'_id':'core'}},'operations':[],'observed_at':time.time(),'alertmanager_url':'http://offline.invalid','pods':{p['metadata']['name']:{'uid':p['metadata']['uid'],'containers':[{k:c.get(k) for k in ('name','restartCount','containerID','imageID')} for c in p['status']['containerStatuses']]} for p in pods}}
        (self.pre/'preflight.json').write_text(json.dumps(pre))
        (self.root/'scripts/runtime_config.py').write_text("def load_config():\n return {'OSM_KUBECONFIG_PATH':'offline-never-used'}\n")
        (self.root/'scripts/ran_health.py').write_text("def established(text,port,*args):\n return text.strip()=='offline-established'\n")
        (self.package/'osm.py').write_text("class ReadOnlyOSM:\n def __init__(self,*args):self.paths=set();self.stats={'gets':0,'authentication':0}\n def _get(self,path):\n  if path.endswith('ns_lcm_op_occs'):return []\n  return {'nsState':'READY','currentOperation':'IDLE','operational-status':'running'}\n")
        kubectl=self.bin/'kubectl';kubectl.write_text('#!'+sys.executable+'\nimport pathlib,sys\nr=pathlib.Path(__file__).resolve().parents[1]\nprint((r/"pods.json").read_text() if "get" in sys.argv else "offline-established")\n');kubectl.chmod(0o700)
        with sqlite3.connect(self.root/'.runtime/assurance.sqlite') as db:
            db.execute('create table objects(kind text,id text,data text)');db.execute('create table audit(seq integer,kind text,object_id text,data text)')
            db.execute('insert into objects values(?,?,?)',('control','governance',json.dumps({'mode':'OBSERVE_ONLY','emergency_stop':False})))
        self.payload('normal')
        self.env=dict(os.environ,PYTHONPATH=str(self.root/'scripts'),PATH=str(self.bin)+':'+os.environ['PATH'])
    def payload(self,mode):
        code='import os,signal,subprocess,sys,time\nfrom pathlib import Path\nr=Path(__file__).resolve().parents[2]\n(r/"payload-started").write_text(str(os.getpid()))\n'
        if mode=='overlap':
            code+='children=[subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)","assurance.cli"]) for _ in range(2)]\n'
        else:code+='children=[subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])]\n'
        code+='(r/"child-pids").write_text(" ".join(str(p.pid) for p in children))\ndef stop(*args):\n for p in children:p.terminate()\n for p in children:p.wait()\n sys.exit(0)\nsignal.signal(signal.SIGTERM,stop)\n'
        code+= 'time.sleep(2)\nstop()\n' if mode=='normal' else 'time.sleep(40)\nstop()\n'
        (self.package/'periodic.py').write_text(code)
    def start(self,duration=150,cycles=2):
        command=[sys.executable,'-m','assurance.guarded_trial','--preflight-dir',str(self.pre),'--trial-dir',str(self.trial),'--duration',str(duration),'--max-cycles',str(cycles)]
        p=subprocess.Popen(command,cwd=self.root,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.cleanup,p);return p
    def cleanup(self,p):
        if p.poll() is None:p.terminate()
        try:p.communicate(timeout=25)
        except subprocess.TimeoutExpired:p.kill();p.communicate()
        file=self.root/'child-pids'
        if file.exists():
            for pid in file.read_text().split():
                try:os.kill(int(pid),signal.SIGKILL)
                except ProcessLookupError:pass
    def finish(self,p,expected=0):
        stdout,stderr=p.communicate(timeout=30);self.assertEqual(p.returncode,expected,stdout+stderr)
        report=json.loads((self.pre/'runtime-guard.json').read_text())
        file=self.root/'child-pids'
        if file.exists():
            for pid in file.read_text().split():self.assertFalse(Path('/proc',pid).exists(),'child survived guardian shutdown')
        return report
    def real_collector_fixture(self, adapter_failure=False):
        # Keep all three executable layers and persistence code real. Only remote
        # adapter boundaries, runtime config and state validation are isolated.
        for source in (ROOT/'scripts/assurance').glob('*.py'):
            if source.name not in ('osm.py','observe.py'):shutil.copy2(source,self.package/source.name)
        (self.root/'config').mkdir()
        shutil.copy2(ROOT/'config/assurance-policies.json',self.root/'config/assurance-policies.json')
        target=self.root/'docs/layer3/l3-2';target.mkdir(parents=True)
        shutil.copy2(ROOT/'docs/layer3/l3-2/policy-validation.json',target/'policy-validation.json')
        (self.root/'state.yaml').write_text('{}')
        config={'OSM_KUBECONFIG_PATH':'offline','RUNTIME_DIR':str(self.root/'.runtime'),'ACTIVE_STATE_PATH':str(self.root/'state.yaml')}
        (self.root/'scripts/runtime_config.py').write_text('def load_config(**kwargs):\n return '+repr(config)+'\n')
        (self.root/'scripts/state_schema.py').write_text("def validate_state(*args):return {'context':{'namespace':'offline'}}\n")
        (self.root/'scripts/scenario_registry.py').write_text('def load_registry():return {}\n')
        if adapter_failure:
            body="def collect(*args,**kwargs):raise ValueError('subscriber-secret password=bad')\n"
        else:
            body="import time\ndef collect(*args,**kwargs):\n return {'observed_at':time.time(),'osm':{'ran':{'provenance':'LIVE'},'core':{'provenance':'LIVE'}},**{k:{'provenance':'LIVE','value':[]} for k in ('kubernetes','nodes','kubernetes_warnings','prometheus','prometheus_targets','alertmanager','alert_groups')}},[]\n"
        (self.package/'observe.py').write_text(body)
        # Recreate only the disposable fixture DB with the real schema.
        (self.root/'.runtime/assurance.sqlite').unlink()
        sys.path.insert(0,str(ROOT/'scripts'))
        from assurance.store import Store
        store=Store(self.root/'.runtime/assurance.sqlite')
        with store.transaction() as db:store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
    def test_full_guardian_periodic_cli_success(self):
        self.real_collector_fixture()
        report=self.finish(self.start(cycles=1))
        result=json.loads((self.trial/'result.json').read_text())
        self.assertEqual(result['cycles'][0]['status'],'LIVE');self.assertIsNone(report['guard_failure'])
    def test_full_guardian_periodic_cli_adapter_exception(self):
        self.real_collector_fixture(adapter_failure=True)
        self.finish(self.start(cycles=1),1)
        result=json.loads((self.trial/'result.json').read_text())
        failure=result['cycles'][0]['diagnostic']
        self.assertEqual(failure['stage'],'child_exit',json.dumps(failure));self.assertEqual(failure['exit_code'],1)
        self.assertEqual(failure['stderr_excerpt'],'Python exception class: ValueError')
        self.assertNotIn('subscriber-secret',json.dumps(result))
    def test_real_startup_duration_tree_watchdog_and_normal_exit(self):
        report=self.finish(self.start())
        self.assertIsNone(report['guard_failure']);self.assertTrue(report['memory_samples'])
        self.assertGreater(report['memory_samples'][0]['trial_process_count'],0)
        self.assertGreater(report['guardian_baseline']['rss_bytes'],0)
        self.assertGreater(report['guardian_image_hwm_bytes'],0)
        command=json.loads((self.pre/'trial-process.json').read_text())['command']
        for flag,value in [('--duration','150'),('--max-cycles','2'),('--interval','60')]:self.assertEqual(command[command.index(flag)+1],value)
        self.assertIn('165s',command);self.assertIn('--stop-on-source-error',command);self.assertTrue(report['protected_checks'])
    def test_real_argument_rejection_precedes_startup(self):
        p=self.start(duration=601);out,err=p.communicate(timeout=5)
        self.assertEqual(p.returncode,2);self.assertIn('Bounded trial limits',err);self.assertFalse((self.pre/'guard-started.json').exists())
    def test_real_sigterm_cancellation_reaps_children(self):
        self.payload('long');p=self.start()
        deadline=time.monotonic()+5
        while not (self.root/'child-pids').exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue((self.root/'child-pids').exists());p.send_signal(signal.SIGTERM)
        report=self.finish(p,1);self.assertIn('KeyboardInterrupt',report['guard_failure'])
    def test_real_timeout_is_reported_without_restart(self):
        self.payload('long');report=self.finish(self.start(duration=1),1)
        self.assertTrue(report['exit_code'] or report['guard_failure']);self.assertLess(report['elapsed_seconds'],25)
        self.assertTrue((self.pre/'guard-started.json').exists())
    def test_real_fail_closed_overlapping_collectors(self):
        self.payload('overlap');report=self.finish(self.start(),1)
        self.assertIn('Overlapping',report['guard_failure'])
    def test_real_fail_closed_governance(self):
        with sqlite3.connect(self.root/'.runtime/assurance.sqlite') as db:db.execute("update objects set data=?",(json.dumps({'mode':'LIVE_AUTO'}),))
        self.payload('long');report=self.finish(self.start(),1)
        self.assertIn('Governance',report['guard_failure'])

if __name__=='__main__':unittest.main()
