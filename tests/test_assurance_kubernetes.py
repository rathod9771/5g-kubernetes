"""Offline real-subprocess boundaries and Kubernetes evidence contracts."""
import errno
import json
import os
from pathlib import Path
import resource
import select
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.kubernetes import KubernetesReader, KubernetesReadError
from assurance.adapters import Kubernetes, service_health

def exited_by(pid, deadline):
    """True if a killed non-child process exits (zombie or reaped) before deadline.

    Its /proc state changes asynchronously (R while exiting, Z, X, then gone), so
    wait on a pidfd instead of sampling /proc. Callers set the deadline well before
    the process could exit on its own, so only cleanup can satisfy it."""
    try:fd=os.pidfd_open(pid)
    except OSError as error:
        # ESRCH: reaped. EINVAL: the PID no longer has a process leader (being released).
        if error.errno not in (errno.ESRCH,errno.EINVAL):raise
        return time.monotonic()<deadline
    try:return bool(select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]) and time.monotonic()<deadline
    finally:os.close(fd)

class KubernetesRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.script=Path(self.tmp.name)/'fake-kubectl'
    def reader(self, code, **kwargs):
        self.script.write_text('#!'+sys.executable+'\n'+code);self.script.chmod(0o700)
        return KubernetesReader({'OSM_KUBECONFIG_PATH':'offline-only'},'n',executable=str(self.script),**kwargs)
    def call(self, reader):return reader(['get','nodes','-o','json'])
    def test_valid_response_and_budget(self):
        r=self.reader('print(\'{"items":[]}\')')
        for _ in range(3):self.assertEqual(self.call(r),{'items':[]})
        with self.assertRaises(KubernetesReadError):self.call(r)
        self.assertEqual(r.stats['processes'],3)
    def test_only_exact_read_commands(self):
        r=self.reader('raise AssertionError("must not run")')
        for args in (['delete','pods'],['get','secrets','-o','json'],['get','pods','-n','other','-o','json']):
            with self.assertRaises(KubernetesReadError):r(args)
        self.assertEqual(r.stats['processes'],0)
    def test_timeout_cleanup(self):
        marker=Path(self.tmp.name)/'pid'
        r=self.reader('import os,time\nopen('+repr(str(marker))+',"w").write(str(os.getpid()))\ntime.sleep(10)',timeout=.2)
        started=time.monotonic()
        with self.assertRaisesRegex(KubernetesReadError,'timeout'):self.call(r)
        self.assertLess(time.monotonic()-started,1)
        self.assertFalse(Path('/proc/'+marker.read_text()).exists())
    def test_stdout_and_stderr_combined_limit(self):
        for pipe in ('stdout','stderr'):
            r=self.reader('import sys\nsys.'+pipe+'.write("x"*100000)',max_output=1024)
            with self.assertRaisesRegex(KubernetesReadError,'output limit'):self.call(r)
    def test_malformed_and_nonzero_redacted(self):
        for code in ('print("not-json")','import sys\nprint("PRIVATE",file=sys.stderr)\nsys.exit(1)'):
            r=self.reader(code)
            with self.assertRaises(KubernetesReadError) as caught:self.call(r)
            self.assertNotIn('PRIVATE',str(caught.exception))
    def test_inherited_as_limit_fails_without_spawn_or_lifting(self):
        r=self.reader('print(\'{"items":[]}\')')
        with patch('assurance.kubernetes.resource.getrlimit',return_value=(512*1024**2,512*1024**2)):
            with self.assertRaisesRegex(KubernetesReadError,'RLIMIT_AS'):self.call(r)
        self.assertEqual(r.stats['processes'],0)
    def test_resident_memory_budget(self):
        r=self.reader('import time\nx=bytearray(32*1024*1024)\ntime.sleep(2)',max_rss=16*1024*1024)
        with self.assertRaisesRegex(KubernetesReadError,'RSS budget'):self.call(r)
    def test_go_virtual_reservation_compatible_offline(self):
        # Client-only: no Kubernetes request or live collection.
        import subprocess
        if resource.getrlimit(resource.RLIMIT_AS)[0]!=resource.RLIM_INFINITY:self.skipTest('host AS restricted')
        result=subprocess.run(['kubectl','version','--client','-o','json'],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0);self.assertIn('clientVersion',json.loads(result.stdout))

    def test_supervisor_one_use_and_no_retry(self):
        from assurance.bounded_once import main
        target=Path(self.tmp.name)/'evidence'
        with patch.object(sys,'argv',['bounded_once','--alertmanager-url','http://offline','--evidence-dir',str(target)]), patch('assurance.bounded_once.resource.setrlimit'), patch.object(KubernetesReader,'run_json',return_value={'observed_at':1}) as run:
            main()
            with self.assertRaises(FileExistsError):main()
            self.assertEqual(run.call_count,1)
            self.assertEqual(json.loads((target/'result.json').read_text())['status'],'COMPLETED')

    def test_descendant_timeout_cleanup(self):
        marker=Path(self.tmp.name)/'child';partial=str(marker)+'.tmp'
        code='import os,time\npid=os.fork()\nif pid==0:\n os.setsid()\n open('+repr(partial)+',"w").write(str(os.getpid()))\n os.replace('+repr(partial)+','+repr(str(marker))+')\n time.sleep(10)\nelse: time.sleep(10)'
        # On a loaded host the 0.25 s timeout can expire before the descendant exists;
        # that run proves nothing, so repeat (bounded) until one is conclusive.
        for _ in range(3):
            marker.unlink(missing_ok=True)
            r=self.reader(code,timeout=.25);started=time.monotonic()
            with self.assertRaises(KubernetesReadError):self.call(r)
            if marker.exists():break
        else:self.fail('descendant never started before the reader timeout')
        self.assertTrue(exited_by(int(marker.read_text()),started+5))  # sleeps 10 s unless killed

class KubernetesEvidenceTests(unittest.TestCase):
    def test_roles_restarts_and_labels(self):
        pods=[]
        for role in ('cu','du','fog','core'):
            pods.append({'metadata':{'name':'open5gs-amf' if role=='core' else role,'uid':role,'labels':{'component':role,'osm.etsi.org/ns-id':'protected'}},'spec':{'nodeName':'node'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{'name':'main','restartCount':2}]}})
        snap=Kubernetes(lambda _: {'items':pods},lambda:1000).snapshot('n')
        self.assertEqual(snap['observed_at'],1000);self.assertEqual(snap['provenance'],'LIVE')
        self.assertEqual(snap['value'][0]['container_restarts'],{'main':2})
        health=service_health(snap,active_ns='protected')
        self.assertTrue(all(health[r]['status']=='WORKLOAD_READY' for r in ('cu','du','fog','core')))
    def test_nodes_pressure_and_warning_freshness(self):
        node={'metadata':{'name':'node','uid':'uid'},'status':{'conditions':[{'type':key,'status':val} for key,val in [('Ready','True'),('MemoryPressure','False'),('DiskPressure','True'),('PIDPressure','False')]]}}
        result=Kubernetes(lambda _: {'items':[node]},lambda:200).infrastructure()
        self.assertEqual(result['value'][0]['conditions']['DiskPressure'],'True')
        warning={'metadata':{'uid':'event','namespace':'n'},'type':'Warning','lastTimestamp':'1970-01-01T00:00:01Z','reason':'BackOff','involvedObject':{'kind':'Pod','name':'du','uid':'du'}}
        result=Kubernetes(lambda _: {'items':[warning]},lambda:200).warnings('n')
        self.assertEqual(result['provenance'],'LIVE');self.assertEqual(result['value'][0]['provenance'],'STALE')
    def test_failures_never_mean_healthy(self):
        def failed(_):raise TimeoutError()
        k=Kubernetes(failed)
        for result in (k.snapshot('n'),k.infrastructure(),k.warnings('n')):self.assertEqual(result['provenance'],'UNAVAILABLE')
        self.assertEqual(service_health(k.snapshot('n'))['cu']['status'],'UNAVAILABLE')

    def test_missing_kubernetes_never_emits_workload_outage(self):
        from assurance.observe import collect
        from unittest.mock import Mock
        def failed(_):raise TimeoutError()
        state={'active':'fran-oai','context':{'namespace':'n','cluster_uid':'c'},'osm':{}}
        telemetry,events=collect({'PROMETHEUS_URL':'offline'},state,failed,Mock(),transport=Mock(side_effect=TimeoutError()))
        self.assertFalse(any(e['alarm_type'] in ('KubernetesNodeNotReady','CUUnavailable','DUUnavailable','FogApplicationUnavailable') for e in events))
        self.assertEqual(telemetry['kpm']['provenance'],'UNAVAILABLE')

    def test_truncated_inventory_does_not_assert_service_health(self):
        snapshot={'provenance':'LIVE','truncated':True,'value':[]}
        self.assertEqual(service_health(snapshot)['core']['status'],'UNAVAILABLE')

    def test_cli_retains_policy_classification_and_new_reader(self):
        from assurance.cli import observe
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            statefile=Path(tmp)/'state';statefile.write_text('{}')
            cfg={'ACTIVE_STATE_PATH':str(statefile),'RUNTIME_DIR':tmp}
            state={'context':{'namespace':'n'}}
            reader=Mock()
            with patch('runtime_config.load_config',return_value=cfg), patch('state_schema.validate_state',return_value=state), patch('scenario_registry.load_registry'), patch('assurance.kubernetes.KubernetesReader',return_value=reader), patch('assurance.observe.collect',return_value=({'observed_at':1},[])), patch('assurance.osm.ReadOnlyOSM'), patch.dict(sys.modules,{'osm_client':Mock()}):
                telemetry=observe(inspect_only=True)
            self.assertEqual(telemetry['policy_classifications'],{'SUPPORTED':8,'PARTIALLY_SUPPORTED':27,'UNAVAILABLE':9})



class ScopedSCTPReaderTests(unittest.TestCase):
    def fixtures(self):
        from types import SimpleNamespace
        pods=[]
        for role,ip in (('cu','192.0.2.1'),('du','192.0.2.2'),('amf','192.0.2.3')):
            pods.append({'metadata':{'name':'fran-oai-'+role,'namespace':'n','uid':role+'-uid',
                'labels':{'component':role,'app':'fran-oai-'+role,'osm.etsi.org/ns-id':'core-id' if role=='amf' else 'ran-id'}},
                'spec':{'containers':[{'name':role,'image':'open5gs:fixture' if role=='amf' else 'oai-gnb:fixture'}]},
                'status':{'phase':'Running','podIP':ip,'conditions':[{'type':'Ready','status':'True'}]}})
        case=SimpleNamespace(pods=pods,tables={'cu':'ST LPORT RPORT LADDRS <-> RADDRS\n'},
             state={'context':{'namespace':'n','namespace_uid':'namespace-id','cluster_uid':'cluster-id'}})
        snap=Kubernetes(lambda _:{'items':pods}).snapshot('n')
        return case,snap['value'][0],{'f1':snap['value'][1],'ngap':snap['value'][2]}
    def test_exact_commands_and_identity_readback(self):
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'n')
        with patch.object(reader,'run_json',side_effect=[{'items':case.pods},case.tables['cu'],{'items':case.pods}]) as run:
            result=reader.sctp(pod,peers,case.state['context'],'ran-id')
        self.assertEqual(run.call_count,3);self.assertEqual(reader.stats['protocol_request_attempts'],3)
        command=run.call_args_list[1].args[0]
        self.assertEqual(command[-3:],['--','cat','/proc/net/sctp/assocs'])
        self.assertIn('-c',command);self.assertEqual(result['pod_uid'],'cu-uid')
    def test_replacement_after_read_rejected(self):
        import copy
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'n')
        changed=copy.deepcopy(case.pods);changed[1]['metadata']['uid']='replacement'
        with patch.object(reader,'run_json',side_effect=[{'items':case.pods},case.tables['cu'],{'items':changed}]):
            with self.assertRaisesRegex(KubernetesReadError,'changed during'):reader.sctp(pod,peers,case.state['context'],'ran-id')
    def test_replacement_before_read_never_executes(self):
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'n')
        with patch.object(reader,'run_json',return_value={'items':[]}) as run:
            with self.assertRaises(KubernetesReadError):reader.sctp(pod,peers,case.state['context'],'ran-id')
        self.assertEqual(run.call_count,1)
    def test_wrong_namespace_denied_before_subprocess(self):
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'other')
        with patch.object(reader,'run_json') as run:
            with self.assertRaises(KubernetesReadError):reader.sctp(pod,peers,case.state['context'],'ran-id')
        run.assert_not_called()
    def test_protocol_budget_no_retry(self):
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'n')
        reader.stats['protocol_observations']=2
        with patch.object(reader,'run_json') as run:
            with self.assertRaisesRegex(KubernetesReadError,'budget'):reader.sctp(pod,peers,case.state['context'],'ran-id')
        run.assert_not_called()
    def test_sctp_output_limit(self):
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'n')
        with patch.object(reader,'run_json',side_effect=[{'items':case.pods},'x'*65537]):
            with self.assertRaisesRegex(KubernetesReadError,'output budget'):reader.sctp(pod,peers,case.state['context'],'ran-id')
    def test_text_mode_uses_existing_process_supervisor(self):
        reader=KubernetesReader({},'',timeout=1)
        self.assertEqual(reader.run_json([sys.executable,'-c','print("ST LPORT RPORT")'],_text=True),'ST LPORT RPORT\n')
        self.assertEqual(reader.stats['processes'],1)
    def test_container_restart_during_read_rejected(self):
        import copy
        case,pod,peers=self.fixtures();reader=KubernetesReader({'OSM_KUBECONFIG_PATH':'offline'},'n')
        changed=copy.deepcopy(case.pods);changed[0]['status']['containerStatuses']=[{'name':'cu','containerID':'replacement','restartCount':1}]
        with patch.object(reader,'run_json',side_effect=[{'items':case.pods},case.tables['cu'],{'items':changed}]):
            with self.assertRaisesRegex(KubernetesReadError,'changed during'):reader.sctp(pod,peers,case.state['context'],'ran-id')

if __name__=='__main__':unittest.main()
