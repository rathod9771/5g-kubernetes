"""Offline sustained-trial, correlation, source failures and safety regression."""
import json
import os
from pathlib import Path
import resource
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.periodic import Trial,collection_error
from assurance.locking import exclusive
from assurance.engine import Engine
from assurance.catalog import load_catalog
from assurance.store import Store
from assurance.observe import collect,monitoring_correlations
from assurance.osm import ReadOnlyOSM
from assurance.kubernetes import KubernetesReader,KubernetesReadError
from assurance.policy import ProductionExecutor


def healthy():
    value={k:{'provenance':'LIVE'} for k in ('kubernetes','nodes','kubernetes_warnings','prometheus','prometheus_targets','alertmanager','alert_groups')}
    value.update(osm={'ran':{'provenance':'LIVE'},'core':{'provenance':'LIVE'}},kpm={'provenance':'UNAVAILABLE'},monitoring_status='DEGRADED')
    return value

class TrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=0;self.starts=[]
        store=Store(self.root/'assurance.sqlite')
        with store.transaction() as db:store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
    def wait(self,delay):self.now+=delay
    def trial(self,execute=None,**kwargs):
        def good(remaining):self.starts.append(self.now);self.now+=12;return healthy()
        return Trial(self.root,self.root/'trial','http://offline',execute=execute or good,
                     clock=lambda:self.now,wall=lambda:1000+self.now,wait=self.wait,**kwargs)
    def test_repeated_cycles_one_at_time_and_durable_audit(self):
        result=self.trial(duration=180,max_cycles=3).run()
        self.assertEqual(self.starts,[0,60,120]);self.assertEqual(len(result['cycles']),3)
        self.assertTrue(Store(self.root/'assurance.sqlite').verify_audit())
        with self.assertRaises(FileExistsError):self.trial().run()
    def test_pause_interlock_prevents_new_starts_until_visibility_returns(self):
        trial=self.trial(duration=180,max_cycles=2)
        original=trial.execute
        def execute(remaining):
            result=original(remaining)
            if len(self.starts)==1:(self.root/'trial'/'PAUSE').touch()
            return result
        def wait(delay):
            self.now+=delay
            if self.now>=75 and (self.root/'trial'/'PAUSE').exists():(self.root/'trial'/'PAUSE').unlink()
        trial.execute=execute;trial.wait=wait;trial.run()
        self.assertEqual(self.starts,[0,75])

    def test_source_error_backoff_and_recovery(self):
        def source(_):
            self.starts.append(self.now);value=healthy()
            if len(self.starts)<3:value['osm']['ran']['provenance']='UNAVAILABLE'
            self.now+=12;return value
        result=self.trial(source,duration=600,max_cycles=4).run()
        self.assertEqual(self.starts,[0,120,360,420])
        self.assertEqual([r['status'] for r in result['cycles']],['SOURCE_UNAVAILABLE','SOURCE_UNAVAILABLE','LIVE','LIVE'])
    def test_no_catch_up_after_slow_cycle(self):
        def slow(_):self.starts.append(self.now);self.now+=80;return healthy()
        self.trial(slow,duration=180,max_cycles=2).run()
        self.assertEqual(self.starts,[0,81])
    def test_overlap_denied_without_launch(self):
        with exclusive(self.root/'.l3-periodic.lock'):
            with self.assertRaisesRegex(RuntimeError,'already active'):self.trial().run()
        self.assertFalse(self.starts)
    def test_collection_lock_across_processes(self):
        import subprocess
        with exclusive(self.root/'.l3-collection.lock'):
            code="from assurance.locking import exclusive; from pathlib import Path\nwith exclusive(Path("+repr(str(self.root/'.l3-collection.lock'))+")): pass"
            environment=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'scripts'))
            result=subprocess.run([sys.executable,'-c',code],env=environment,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0);self.assertIn(b'already active',result.stderr)
    def test_shutdown_during_wait(self):
        stop=threading.Event()
        def wait(delay):self.now+=delay;stop.set()
        trial=self.trial(stop=stop);trial.wait=wait
        result=trial.run();self.assertEqual(result['reason'],'shutdown');self.assertEqual(len(result['cycles']),1)
    def test_emergency_stop_prevents_collection(self):
        store=Store(self.root/'assurance.sqlite')
        with store.transaction() as db:store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':True})
        self.assertEqual(self.trial().run()['cycles'],[])
    def test_stop_file_prevents_next_cycle(self):
        def one(_):self.starts.append(self.now);(self.root/'trial'/'STOP').touch();return healthy()
        self.assertEqual(len(self.trial(one).run()['cycles']),1)
    def test_process_watchdog_failure_stops_without_retry(self):
        def failure(_):self.starts.append(self.now);raise KubernetesReadError('RSS limit')
        result=self.trial(failure).run()
        self.assertEqual(result['reason'],'process_failure');self.assertEqual(len(self.starts),1)
    def test_invalid_bounds_rejected(self):
        for values in ({'duration':601},{'max_cycles':11},{'interval':1}):
            with self.assertRaises(ValueError):self.trial(**values)
    def test_missing_and_stale_required_sources_backoff_kpm_does_not(self):
        self.assertFalse(collection_error(healthy()))
        for source in ('kubernetes','prometheus','nodes','alertmanager'):
            for provenance in ('UNAVAILABLE','STALE'):
                value=healthy();value[source]['provenance']=provenance;self.assertTrue(collection_error(value))
    def test_real_subprocess_shutdown_cleanup(self):
        stop=threading.Event();timer=threading.Timer(.1,stop.set);timer.start()
        try:
            with self.assertRaisesRegex(KubernetesReadError,'shutdown'):
                KubernetesReader({},'').run_json([sys.executable,'-c','import time;time.sleep(5)'],stop_event=stop)
        finally:timer.cancel()
    def test_cpu_watchdog_child_terminates(self):
        code='import resource\nresource.setrlimit(resource.RLIMIT_CPU,(1,1))\nwhile True: pass'
        with self.assertRaises(KubernetesReadError):KubernetesReader({},'',timeout=4).run_json([sys.executable,'-c',code])

class IncidentCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'journal');self.now=1000
        self.catalog=load_catalog();self.engine=Engine(self.store,self.catalog,lambda:self.now)
    def event(self,status='firing',at=None):return {'alarm_type':'PrometheusTargetDown','resource':'kube-etcd/host:2381','cluster':'c','namespace':'monitoring','timestamp':self.now if at is None else at,'status':status,'observations':{'metric_present':True}}
    def test_identity_across_cycles_and_restart(self):
        ids=[]
        for _ in range(5):
            self.engine.ingest(self.event());self.engine.ingest(self.event());self.now+=60
            self.engine=Engine(Store(self.store.path),self.catalog,lambda:self.now)
            ids.append({i['incident_id'] for i in self.store.snapshot()['incident']})
        self.assertTrue(all(i==ids[0] for i in ids));self.assertEqual(len(ids[0]),1)
        self.assertEqual(len(self.store.snapshot()['alarm']),1)
    def test_safe_clearing_requires_recovery_window(self):
        self.engine.ingest(self.event());self.now+=60;self.engine.ingest(self.event('resolved'))
        self.assertNotEqual(self.store.snapshot()['alarm'][0]['state'],'CLEARED')
        self.now+=60;self.engine.ingest(self.event('resolved'))
        self.assertEqual(self.store.snapshot()['alarm'][0]['state'],'CLEARED')
        self.assertEqual(self.store.snapshot()['incident'][0]['state'],'RESOLVED')
    def test_missing_and_stale_cannot_clear_alarm(self):
        self.engine.ingest(self.event());self.now+=180;self.engine.refresh()
        self.assertTrue(self.store.snapshot()['alarm'][0]['stale'])
        self.engine.ingest(self.event('resolved',at=1001))
        self.assertNotEqual(self.store.snapshot()['alarm'][0]['state'],'CLEARED')
    def test_large_gap_resets_recovery_persistence(self):
        self.engine.ingest(self.event());self.now+=60;self.engine.ingest(self.event('resolved'))
        self.now+=120;self.engine.ingest(self.event('resolved'))
        self.assertNotEqual(self.store.snapshot()['alarm'][0]['state'],'CLEARED')
    def test_endpoint_correlation_is_not_control_plane_outage(self):
        rows=[{'health':'down','fresh':True,'labels':{'job':job,'instance':'host:'+port},'failure_class':'connection_refused'} for job,port in [('kube-etcd','2381'),('kube-scheduler','10259'),('kube-controller-manager','10257'),('kube-proxy','10249')]]
        result=monitoring_correlations({'observed_at':1000,'prometheus_targets':{'value':rows}})
        self.assertEqual(len(result),1);self.assertEqual(len(result[0]['targets']),4)
        self.assertEqual(result[0]['classification'],'MONITORING_ENDPOINT_UNAVAILABLE')
        self.assertEqual(result[0]['control_plane_process_health'],'UNAVAILABLE')
        self.assertEqual(result[0]['kubernetes_service_degraded'],'UNAVAILABLE')
    def test_simulation_never_reaches_executor(self):
        simulation=Engine(self.store,self.catalog,lambda:self.now,simulation=True)
        with patch.object(ProductionExecutor,'execute',side_effect=AssertionError('production')):
            simulation.ingest(self.event());simulation.refresh()
        self.assertEqual(self.store.snapshot()['incident'][0]['provenance'],'SIMULATED')
        with self.assertRaises(PermissionError):ProductionExecutor().execute()
    def test_prometheus_recovery_generates_clear_evidence(self):
        state={'active':'fran-oai','context':{'namespace':'n','cluster_uid':'c'},'osm':{}}
        with patch('assurance.observe.Prometheus') as prom:
            prom.return_value.health.return_value={'provenance':'LIVE'}
            prom.return_value.targets.return_value={'provenance':'LIVE','status':'AVAILABLE','value':[]}
            prom.return_value.measurement.return_value={'provenance':'UNAVAILABLE'}
            telemetry,events=collect({'PROMETHEUS_URL':'offline'},state,lambda _:{'items':[]},Mock(),transport=Mock())
        recovery=next(e for e in events if e['alarm_type']=='MonitoringPipelineDegraded')
        self.assertEqual(recovery['status'],'resolved')
        self.assertEqual(telemetry['cpu_pressure']['provenance'],'UNAVAILABLE')

    def test_integer_audit_timestamps_survive_sqlite_real_storage(self):
        with self.store.transaction() as db:self.store.record(db,1000,'offline_test','clock',{})
        self.assertTrue(self.store.verify_audit())

    def test_all_api_failures_remain_unavailable_without_workload_outage(self):
        reader=Mock(side_effect=TimeoutError());osm=Mock();osm.get_ns_instance.side_effect=TimeoutError()
        state={'active':'fran-oai','context':{'namespace':'n','cluster_uid':'c'},'osm':{'active_instance_id':'ran','core_instance_id':'core'}}
        telemetry,events=collect({'PROMETHEUS_URL':'offline'},state,reader,osm,transport=Mock(side_effect=TimeoutError()))
        self.assertEqual(telemetry['services']['cu']['status'],'UNAVAILABLE');self.assertTrue(collection_error(telemetry))
        self.assertFalse(any(e['alarm_type'] in ('CUUnavailable','DUUnavailable','KubernetesNodeNotReady') for e in events))

class ReadOnlyOSMTests(unittest.TestCase):
    def reader(self,payloads):
        opener=Mock();responses=[]
        for payload in payloads:
            response=Mock();response.read.return_value=json.dumps(payload).encode();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);responses.append(response)
        opener.open.side_effect=responses
        cfg={'OSM_HOST':'https://osm','OSM_CA_CERT_PATH':'unused','OSM_USER':'test','OSM_PASSWORD':'private','OSM_PROJECT_ID':'','OSM_PROJECT':'test'}
        return ReadOnlyOSM(cfg,{'osm':{'active_instance_id':'ran','core_instance_id':'core','active_operation_id':'op'}},opener),opener
    def test_bounded_auth_and_three_reads(self):
        reader,opener=self.reader([{'id':'token'},{'_id':'ran'},{'_id':'core'},{'nsInstanceId':'ran'}])
        reader.get_ns_instance('ran');reader.get_ns_instance('core');reader.get_operation('op')
        self.assertEqual(reader.stats,{'gets':3,'authentication':1,'failures':0})
        self.assertTrue(all(call.kwargs['timeout']==4 for call in opener.open.call_args_list))
        with self.assertRaises(PermissionError):reader.get_ns_instance('ran')
        with self.assertRaises(PermissionError):reader._request('POST','/nslcm/v1/ns_instances/ran/terminate')
    def test_auth_failure_no_retry(self):
        reader,opener=self.reader([]);opener.open.side_effect=TimeoutError('secret')
        for _ in range(2):
            with self.assertRaises(Exception):reader.get_ns_instance('ran')
        self.assertEqual(opener.open.call_count,1)
    def test_oversized_response_redacted(self):
        reader,opener=self.reader([{'id':'token'},{'private':'x'*(2*1024*1024)}])
        with self.assertRaisesRegex(Exception,'private details suppressed'):reader.get_ns_instance('ran')
        self.assertEqual(reader.stats['failures'],1)
    def test_unbound_object_denied_before_authentication(self):
        reader,opener=self.reader([])
        with self.assertRaises(PermissionError):reader.get_ns_instance('other')
        opener.open.assert_not_called()

if __name__=='__main__':unittest.main()
