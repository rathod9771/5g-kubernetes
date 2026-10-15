"""Offline regression for actual adapter failures and cached live-evidence contracts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.adapters import Prometheus,Alertmanager
from assurance.http import ReadOnlyHTTP
from assurance.observe import collect

class LiveAdapterTests(unittest.TestCase):
    def setUp(self):self.now=2000000000.
    def response(self,rows):return {'status':'success','data':{'resultType':'vector','result':rows}}
    def test_active_target_filter_avoids_dropped_discovery_payload(self):
        transport=Mock(return_value={'status':'success','data':{'activeTargets':[]}})
        Prometheus('https://p',transport).targets()
        self.assertIn('state=active',transport.call_args.args[0])
    def test_legacy_kpm_jobs_and_ue_names_cannot_degrade_cu_du(self):
        from assurance.adapters import service_health
        rows=[{'name':'fran-oai-cu-x','ready':True,'labels':{'component':'cu','osm.etsi.org/ns-id':'protected'}},
              {'name':'fran-oai-du-x','ready':True,'labels':{'component':'du','osm.etsi.org/ns-id':'protected'}},
              {'name':'flexric-cu-kpm-acceptance','ready':False,'labels':{}},
              {'name':'oai-nr-ue-fran-oai-du-x','ready':False,'labels':{}},
              {'name':'other-cu','ready':False,'labels':{'component':'cu','osm.etsi.org/ns-id':'different'}}]
        health=service_health({'provenance':'LIVE','value':rows},active_ns='protected')
        self.assertEqual(health['cu']['status'],'WORKLOAD_READY');self.assertEqual(health['cu']['count'],1)
        self.assertEqual(health['du']['status'],'WORKLOAD_READY')
    def test_assurance_routes_cannot_execute_lifecycle_and_stop_is_durable(self):
        from assurance.api import register
        from assurance.store import Store
        from flask import Flask
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'L3_OPERATOR_TOKEN':'x'*32}),patch('assurance.policy.ProductionExecutor.execute',side_effect=AssertionError('executor')):
            app=Flask(__name__);register(app,lambda:{'RUNTIME_DIR':tmp});client=app.test_client();headers={'Authorization':'Bearer '+'x'*32}
            for command in ('execute','terminate_ns','deploy','core_restart'):
                self.assertEqual(client.post('/api/assurance/operator',json={'command':command},headers=headers).status_code,400)
            self.assertEqual(client.post('/api/assurance/operator',json={'command':'control','mode':'LIVE_AUTO'},headers=headers).status_code,400)
            self.assertEqual(client.post('/api/assurance/operator',json={'command':'control','emergency_stop':True},headers=headers).status_code,200)
            self.assertTrue(Store(Path(tmp)/'assurance.sqlite').snapshot()['governance']['emergency_stop'])
            self.assertFalse(client.get('/api/assurance').json['live_auto_enabled'])
    def test_empty_target_inventory_is_unavailable(self):
        p=Prometheus('https://p',lambda _:{'status':'success','data':{'activeTargets':[]}})
        self.assertEqual(p.targets()['provenance'],'UNAVAILABLE')
    def test_alertmanager_loss_degrades_an_otherwise_accessible_pipeline(self):
        state={'active':'fran-oai','context':{'namespace':'n','cluster_uid':'c'},'osm':{}}
        with patch('assurance.observe.Prometheus') as prom,patch('assurance.observe.Alertmanager') as am:
            prom.return_value.health.return_value={'provenance':'LIVE'}
            prom.return_value.targets.return_value={'status':'AVAILABLE','value':[]}
            prom.return_value.measurement.return_value={'provenance':'LIVE'}
            am.return_value.snapshot.return_value={'provenance':'UNAVAILABLE'}
            am.return_value.groups.return_value={'provenance':'UNAVAILABLE'}
            telemetry,_=collect({'PROMETHEUS_URL':'https://p'},state,lambda _:{'items':[]},Mock(),alertmanager_url='https://a',transport=lambda _: {})
        self.assertEqual(telemetry['monitoring_status'],'DEGRADED')
    def test_source_timestamp_not_query_evaluation_time(self):
        query=self.response([{'metric':{'job':'node-exporter'},'value':[self.now,str(self.now-121)]}])
        p=Prometheus('https://p',lambda _:query,lambda:self.now)
        self.assertEqual(p.health()['provenance'],'STALE')
    def test_cpu_psi_source_freshness_labels_and_zero(self):
        responses=[self.response([{'metric':{'job':'node-exporter'},'value':[self.now,'0']}]),self.response([{'metric':{'job':'node-exporter'},'value':[self.now,str(self.now-30)]}])]
        p=Prometheus('https://p',Mock(side_effect=responses),lambda:self.now)
        result=p.measurement('rate(psi[5m])','psi')
        self.assertEqual(result['provenance'],'LIVE');self.assertEqual(result['value'][0]['value'],0)
        self.assertEqual(result['value'][0]['labels'],{'job':'node-exporter'})
    def test_missing_freshness_fail_closed(self):
        responses=[self.response([{'metric':{},'value':[self.now,'0.5']}]),self.response([])]
        p=Prometheus('https://p',Mock(side_effect=responses),lambda:self.now)
        self.assertEqual(p.measurement('rate(psi[5m])','psi')['provenance'],'UNAVAILABLE')
    def test_four_connection_refused_targets_are_degraded_not_healthy(self):
        from datetime import datetime,timezone
        at=datetime.fromtimestamp(self.now,tz=timezone.utc).isoformat()
        targets=[{'labels':{'job':job},'health':'down','lastScrape':at,'lastError':'connect: connection refused'} for job in ('kube-controller-manager','kube-etcd','kube-proxy','kube-scheduler')]
        p=Prometheus('https://p',lambda _:{'status':'success','data':{'activeTargets':targets}},lambda:self.now)
        result=p.targets();self.assertEqual(result['status'],'DEGRADED');self.assertEqual(len(result['value']),4)
        self.assertTrue(all(t['failure_class']=='connection_refused' for t in result['value']))
    def test_target_timeout_and_malformed_json_are_unavailable(self):
        for source in (Mock(side_effect=TimeoutError()),lambda _:None):
            self.assertEqual(Prometheus('https://p',source).targets()['provenance'],'UNAVAILABLE')
    def test_alert_grouping_and_duplicates_stable_no_secrets(self):
        payload=[{'labels':{'namespace':'kube-system'},'receiver':{'name':'default'},'alerts':[{'labels':{'alertname':'TargetDown','job':'kube-etcd','secret':'TOKEN'},'startsAt':'2026-10-01T00:00:00Z','annotations':{'token':'TOKEN'},'status':{'state':'suppressed'}}]}]
        am=Alertmanager('https://a',lambda _:payload)
        a,b=am.groups(),am.groups();self.assertEqual(a['value'],b['value']);self.assertNotIn('TOKEN',json.dumps(a))
        self.assertTrue(a['value'][0]['alerts'][0]['suppressed'])
    def test_alertmanager_auth_failure_and_limit(self):
        self.assertEqual(Alertmanager('https://a',Mock(side_effect=PermissionError())).snapshot()['provenance'],'UNAVAILABLE')
        self.assertEqual(Alertmanager('https://a',lambda _:[{}]*65).groups()['provenance'],'UNAVAILABLE')
    def test_get_only_cache_request_budget_and_timeout(self):
        opener=MagicMock();response=Mock();response.read.return_value=b'{"status":"success"}'
        opener.open.return_value.__enter__.return_value=response
        http=ReadOnlyHTTP(opener=opener,budget=1)
        http('https://p/api/v1/query?q=up');http('https://p/api/v1/query?q=up')
        self.assertEqual(http.stats['requests'],1);self.assertEqual(http.stats['cache_hits'],1)
        req=opener.open.call_args.args[0];self.assertEqual(req.get_method(),'GET');self.assertEqual(opener.open.call_args.kwargs['timeout'],4)
        with self.assertRaises(RuntimeError):http('https://p/api/v1/targets')
    def test_bearer_origin_https_and_no_redirect(self):
        opener=MagicMock();opener.open.return_value.__enter__.return_value.read.return_value=b'{}'
        http=ReadOnlyHTTP(token='PRIVATE',auth_origin='https://p',opener=opener)
        for url in ('http://p/api','https://other/api'):
            with self.assertRaises(ValueError):http(url)
        http('https://p/api');req=opener.open.call_args.args[0];self.assertEqual(req.get_header('Authorization'),'Bearer PRIVATE')
        self.assertNotIn('PRIVATE',json.dumps(http.stats))
        from assurance.http import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,None))
    def test_http_failure_redaction_and_oversized_response(self):
        opener=MagicMock();opener.open.side_effect=TimeoutError('TOKEN')
        with self.assertRaisesRegex(RuntimeError,'private details suppressed'):ReadOnlyHTTP(opener=opener)('https://p/api')
        opener=MagicMock();opener.open.return_value.__enter__.return_value.read.return_value=b' '* (2*1024*1024+1)
        with self.assertRaises(RuntimeError):ReadOnlyHTTP(opener=opener)('https://p/api')
    def test_collector_api_failures_do_not_infer_healthy_or_execute(self):
        state={'active':'fran-oai','context':{'namespace':'n','cluster_uid':'c'},'osm':{'active_instance_id':'ran','core_instance_id':'core'}}
        osm=Mock();osm.get_ns_instance.side_effect=TimeoutError()
        transport=Mock(side_effect=TimeoutError())
        with patch('assurance.policy.ProductionExecutor.execute',side_effect=AssertionError('executor')):
            telemetry,events=collect({'PROMETHEUS_URL':'https://p'},state,Mock(side_effect=TimeoutError()),osm,alertmanager_url='https://a',transport=transport)
        self.assertEqual(telemetry['monitoring_status'],'UNAVAILABLE');self.assertEqual(telemetry['services']['core']['status'],'UNAVAILABLE')
        self.assertTrue(any(e['alarm_type']=='MonitoringPipelineDegraded' for e in events));osm.terminate_ns.assert_not_called()
    def test_fresh_snapshot_with_failed_prometheus_is_unavailable(self):
        from assurance.api import register
        from assurance.store import Store
        from flask import Flask
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'assurance.sqlite')
            with store.transaction() as db:store.put(db,'telemetry','latest',{'observed_at':time.time(),'prometheus':{'provenance':'UNAVAILABLE'},'kubernetes':{'provenance':'LIVE'}})
            app=Flask(__name__);register(app,lambda:{'RUNTIME_DIR':tmp})
            self.assertEqual(app.test_client().get('/api/assurance').json['monitoring_confidence'],'UNAVAILABLE')
    def test_initial_healthy_observation_cannot_raise_active_alarm(self):
        from assurance.engine import Engine
        from assurance.catalog import load_catalog
        from assurance.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'journal.sqlite');engine=Engine(store,load_catalog(),lambda:self.now)
            alarm=engine.ingest({'alarm_type':'PrometheusTargetDown','resource':'healthy','timestamp':self.now,'status':'resolved'})
            self.assertEqual(alarm['state'],'CLEARED');self.assertTrue(alarm['resolved']);self.assertFalse(store.snapshot()['incident'])
    def test_native_webhook_redelivery_is_idempotent(self):
        from assurance.api import register
        from flask import Flask
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'L3_WEBHOOK_TOKEN':'x'*32}):
            app=Flask(__name__);register(app,lambda:{'RUNTIME_DIR':tmp});client=app.test_client()
            payload={'alerts':[{'status':'firing','labels':{'alertname':'DUUnavailable'},'startsAt':'2026-10-01T00:00:00Z'}]}
            for _ in range(2):self.assertEqual(client.post('/api/assurance/webhook',json=payload,headers={'Authorization':'Bearer '+'x'*32}).status_code,200)
            snap=client.get('/api/assurance').json
            self.assertEqual(len(snap['alarm']),1);self.assertEqual(len([a for a in snap['audit'] if a['kind']=='alarm_observed']),1)
            self.assertTrue(snap['alarm'][0]['stale']);self.assertFalse(snap['live_auto_enabled'])

if __name__=='__main__':unittest.main()
