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



class P1DetectionTests(unittest.TestCase):
    """Scoped synthetic fixtures; no kubectl, OSM or network access."""
    def setUp(self):
        import copy
        self.now=2000000000.
        self.state={'active':'fran-oai','context':{'namespace':'n','namespace_uid':'namespace-id','cluster_uid':'cluster-id'},
                    'osm':{'active_instance_id':'ran-id','core_instance_id':'core-id'}}
        def pod(role,ip,ns='ran-id'):
            return {'metadata':{'name':'fran-oai-'+role,'namespace':'n','uid':role+'-uid',
                'labels':{'component':role,'app':'fran-oai-'+role,'osm.etsi.org/ns-id':ns}},
                'spec':{'containers':[{'name':role,'image':'oai-gnb:fixture' if role!='amf' else 'open5gs:fixture'}]},
                'status':{'phase':'Running','podIP':ip,'conditions':[{'type':'Ready','status':'True'}]}}
        self.pods=[pod('cu','192.0.2.1'),pod('du','192.0.2.2'),pod('amf','192.0.2.3','core-id')]
        self.header='ST LPORT RPORT LADDRS <-> RADDRS\n'
        self.tables={'cu':self.header+'3 38472 40000 192.0.2.1 <-> 192.0.2.2\n3 40001 38412 192.0.2.1 <-> 192.0.2.3\n',
                     'du':self.header+'3 40000 38472 192.0.2.2 <-> 192.0.2.1\n'}
        self.change=lambda value:value
    def snapshot(self):
        from assurance.adapters import Kubernetes
        return Kubernetes(lambda _: {'items':self.pods},lambda:self.now).snapshot('n')
    def observer(self,pod,peers,context,ns):
        role=pod['labels']['component']
        value={**context,'ns_id':ns,'pod':pod['name'],'pod_uid':pod['uid'],'pod_ip':pod['pod_ip'],
               'container':role,'peers':{k:{x:p[x] for x in ('uid','pod_ip')} for k,p in peers.items()},
               'provenance':'LIVE','observed_at':self.now,'table':self.tables[role]}
        return self.change(value)
    def run_detection(self,observer=True,snapshot=None):
        from assurance.observe import ran_evidence
        return ran_evidence(snapshot if snapshot is not None else self.snapshot(),self.state,self.observer if observer else None,lambda:self.now)
    def alarm(self,events,kind):return [e for e in events if e['alarm_type']==kind]
    def test_healthy_owned_workloads_and_protocols(self):
        telemetry,events=self.run_detection()
        self.assertEqual([x['availability'] for x in telemetry['workloads']],['READY','READY'])
        self.assertTrue(events);self.assertTrue(all(e['status']=='resolved' for e in events))
        self.assertEqual(telemetry['associations']['f1']['value'],True)
        self.assertEqual(telemetry['associations']['ngap']['value'],True)
    def test_unhealthy_owned_workload_is_not_missing(self):
        self.pods[1]['status']['conditions'][0]['status']='False'
        telemetry,events=self.run_detection(False)
        self.assertEqual(telemetry['workloads'][1]['availability'],'NOT_READY')
        self.assertEqual(self.alarm(events,'DUUnavailable')[0]['status'],'firing')
    def test_unknown_condition_no_failure_or_clear(self):
        self.pods[1]['status']['conditions'][0]['status']='Unknown'
        telemetry,events=self.run_detection(False)
        self.assertEqual(telemetry['workloads'][1]['availability'],'UNKNOWN')
        self.assertFalse(self.alarm(events,'DUUnavailable'))
    def test_missing_condition_is_unknown(self):
        self.pods[1]['status']['conditions']=[]
        self.assertFalse(self.alarm(self.run_detection(False)[1],'DUUnavailable'))
    def test_missing_expected_workload_is_not_failure(self):
        self.pods.pop(1);telemetry,events=self.run_detection()
        self.assertEqual(telemetry['workloads'][1]['availability'],'MISSING')
        self.assertFalse(self.alarm(events,'DUUnavailable'));self.assertFalse(self.alarm(events,'F1AssociationLost'))
    def test_incomplete_inventory_suppresses_events(self):
        snap=self.snapshot();snap['truncated']=True
        self.assertFalse(self.run_detection(snapshot=snap)[1])
    def test_continued_inventory_is_incomplete(self):
        from assurance.adapters import Kubernetes
        snap=Kubernetes(lambda _: {'items':self.pods,'metadata':{'continue':'next'}},lambda:self.now).snapshot('n')
        self.assertTrue(snap['truncated']);self.assertFalse(self.run_detection(snapshot=snap)[1])
    def test_unrelated_ns_workload_not_selected(self):
        self.pods[1]['metadata']['labels']['osm.etsi.org/ns-id']='other'
        self.assertFalse(self.alarm(self.run_detection()[1],'DUUnavailable'))
    def test_unrelated_app_workload_not_selected(self):
        self.pods[1]['metadata']['labels']['app']='unrelated-du'
        self.assertFalse(self.alarm(self.run_detection()[1],'DUUnavailable'))
    def test_namespace_mismatch_is_unavailable(self):
        self.pods[0]['metadata']['namespace']='other'
        self.assertEqual(self.snapshot()['provenance'],'UNAVAILABLE')
        self.assertFalse(self.run_detection()[1])
    def test_unbound_namespace_identity_is_unknown(self):
        self.state['context'].pop('namespace_uid')
        self.assertFalse(self.run_detection()[1])
    def test_duplicate_candidates_are_unknown(self):
        import copy
        extra=copy.deepcopy(self.pods[1]);extra['metadata']['uid']='new';self.pods.append(extra)
        self.assertFalse(self.alarm(self.run_detection()[1],'DUUnavailable'))
    def test_replaced_uid_cannot_reuse_protocol_evidence(self):
        self.change=lambda x:dict(x,pod_uid='previous-uid')
        t,e=self.run_detection();self.assertIsNone(t['associations']['f1']['value'])
        self.assertFalse(self.alarm(e,'F1AssociationLost'))
    def test_wrong_peer_identity_is_unavailable(self):
        self.change=lambda x:dict(x,peers={})
        self.assertIsNone(self.run_detection()[0]['associations']['f1']['value'])
    def test_wrong_peer_address_never_establishes_health(self):
        self.tables['cu']=self.tables['cu'].replace('192.0.2.2','192.0.2.99')
        t,e=self.run_detection();self.assertIsNone(t['associations']['f1']['value'])
        self.assertFalse(self.alarm(e,'F1AssociationLost')) # contradictory ends
    def test_wrong_ports_never_establish_health(self):
        self.tables={k:v.replace('38472','9000') for k,v in self.tables.items()}
        t,e=self.run_detection();self.assertFalse(t['associations']['f1']['value'])
        self.assertTrue(all(x['status']=='firing' for x in self.alarm(e,'F1AssociationLost')))
    def test_cu_f1_requires_local_listener_port(self):
        self.tables['cu']=self.header+'3 40000 38472 192.0.2.1 <-> 192.0.2.2\n'
        self.assertIsNone(self.run_detection()[0]['associations']['f1']['value'])
    def test_ngap_requires_remote_port(self):
        self.tables['cu']=self.tables['cu'].replace('40001 38412','38412 40001')
        self.assertEqual(self.alarm(self.run_detection()[1],'NGAPAssociationLost')[0]['status'],'firing')
    def test_missing_associations_in_valid_tables_are_absent(self):
        self.tables={k:self.header for k in self.tables}
        t,e=self.run_detection();self.assertFalse(t['associations']['f1']['value']);self.assertFalse(t['associations']['ngap']['value'])
    def test_non_established_state_not_healthy(self):
        self.tables={k:v.replace('3 ','2 ') for k,v in self.tables.items()}
        self.assertFalse(self.run_detection()[0]['associations']['f1']['value'])
    def test_malformed_sctp_is_unavailable(self):
        for bad in ('', 'ST LPORT RPORT\n3 38472 40000\n',self.header+'broken\n',self.header+'3 38472 40000 bad <-> bad\n'):
            with self.subTest(bad=bad):
                self.tables={'cu':bad,'du':bad}
                self.assertFalse(self.alarm(self.run_detection()[1],'F1AssociationLost'))
    def test_stale_and_future_protocol_is_not_failure(self):
        for delta in (-121,1):
            self.change=lambda x:dict(x,observed_at=self.now+delta)
            t,e=self.run_detection();self.assertIsNone(t['associations']['f1']['value'])
            self.assertFalse(self.alarm(e,'NGAPAssociationLost'))
    def test_stale_inventory_is_not_failure(self):
        snap=self.snapshot();snap['observed_at']-=121
        self.assertFalse(self.run_detection(snapshot=snap)[1])
    def test_protocol_api_failure_is_not_failure(self):
        from assurance.observe import ran_evidence
        def failed(*args):raise TimeoutError('subscriber-secret')
        t,e=ran_evidence(self.snapshot(),self.state,failed,lambda:self.now)
        self.assertNotIn('subscriber-secret',str(t));self.assertFalse(self.alarm(e,'F1AssociationLost'))
    def test_inventory_api_failure_suppresses_events(self):
        from assurance.adapters import Kubernetes
        def failed(*args):raise TimeoutError()
        snap=Kubernetes(failed).snapshot('n');self.assertFalse(self.run_detection(snapshot=snap)[1])
    def test_default_has_no_live_protocol_reader(self):
        t,e=self.run_detection(False)
        self.assertEqual(t['associations']['f1']['status'],'UNKNOWN');self.assertFalse(self.alarm(e,'F1AssociationLost'))
    def test_simulated_protocol_cannot_become_live(self):
        self.change=lambda x:dict(x,provenance='SIMULATED')
        self.assertFalse(self.alarm(self.run_detection()[1],'F1AssociationLost'))
    def test_wrong_container_or_host_network_unavailable(self):
        self.change=lambda x:dict(x,container='sidecar')
        self.assertFalse(self.alarm(self.run_detection()[1],'F1AssociationLost'))
        self.change=lambda x:x;self.pods[0]['spec']['hostNetwork']=True
        self.assertFalse(self.alarm(self.run_detection()[1],'F1AssociationLost'))
    def test_protocol_does_not_prove_higher_states(self):
        t,_=self.run_detection()
        for protocol in t['protocols'].values():
            for key in ('f1_setup','ng_setup','ue_registered','pdu_established','user_plane_measured'):self.assertIsNone(protocol['value'][key])
    def test_policy_rejects_contradictory_workload_protocol(self):
        from assurance.model import normalize
        from assurance.catalog import load_catalog
        from assurance.policy import Policy
        self.pods[1]['status']['conditions'][0]['status']='False'
        e=self.alarm(self.run_detection()[1],'DUUnavailable')[0]
        self.assertEqual(Policy(load_catalog()).evaluate(normalize(e,self.now))['action'],'observe_notify')
    def test_existing_du_policy_accepts_corroborated_failure_only(self):
        from assurance.model import normalize
        from assurance.catalog import load_catalog
        from assurance.policy import Policy
        self.pods[1]['status']['conditions'][0]['status']='False';self.tables={k:self.header for k in self.tables}
        e=self.alarm(self.run_detection()[1],'DUUnavailable')[0]
        self.assertEqual(Policy(load_catalog()).evaluate(normalize(e,self.now))['action'],'propose_du_recovery')
    def test_existing_cu_policy_needs_both_protocol_failures(self):
        from assurance.model import normalize
        from assurance.catalog import load_catalog
        from assurance.policy import Policy
        self.pods[0]['status']['conditions'][0]['status']='False';self.tables={k:self.header for k in self.tables}
        e=self.alarm(self.run_detection()[1],'CUUnavailable')[0]
        self.assertEqual(Policy(load_catalog()).evaluate(normalize(e,self.now))['action'],'propose_cu_recovery')
    def test_ngap_does_not_invent_core_health(self):
        from assurance.model import normalize
        from assurance.catalog import load_catalog
        from assurance.policy import Policy
        self.tables['cu']=self.header
        e=self.alarm(self.run_detection()[1],'NGAPAssociationLost')[0]
        self.assertEqual(Policy(load_catalog()).evaluate(normalize(e,self.now))['action'],'observe_notify')
    def test_missing_amf_identity_suppresses_ngap(self):
        self.pods.pop();self.assertFalse(self.alarm(self.run_detection()[1],'NGAPAssociationLost'))
    def test_protocol_timestamp_preserved_not_receipt(self):
        self.change=lambda x:dict(x,observed_at=self.now-10)
        _,e=self.run_detection();self.assertEqual(e[0]['timestamp'],self.now-10)
    def test_replacement_does_not_reuse_alarm_key(self):
        from assurance.model import normalize
        before=self.alarm(self.run_detection(False)[1],'DUUnavailable')[0]
        self.pods[1]['metadata']['uid']='replacement'
        after=self.alarm(self.run_detection(False)[1],'DUUnavailable')[0]
        self.assertNotEqual(normalize(before,self.now)['key'],normalize(after,self.now)['key'])
    def test_incident_deduplication_and_production_disabled(self):
        import tempfile
        from pathlib import Path
        from assurance.store import Store
        from assurance.engine import Engine
        from assurance.catalog import load_catalog
        from assurance.policy import ProductionExecutor
        self.pods[1]['status']['conditions'][0]['status']='False';self.tables={k:self.header for k in self.tables}
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'journal.sqlite');engine=Engine(store,load_catalog(),lambda:self.now)
            for delta in (0,60,0):
                self.now+=delta
                for event in self.run_detection()[1]:engine.ingest(event)
            self.assertEqual(len(store.snapshot()['incident']),1);self.assertTrue(store.verify_audit())
        with self.assertRaises(PermissionError):ProductionExecutor().execute()


    def test_every_binding_field_must_match_current_context(self):
        for field in ('namespace','namespace_uid','cluster_uid','ns_id','pod','pod_uid','pod_ip'):
            with self.subTest(field=field):
                self.change=lambda x:dict(x,**{field:'wrong'})
                self.assertFalse(self.alarm(self.run_detection()[1],'F1AssociationLost'))
    def test_service_vip_without_endpoint_mapping_is_unknown(self):
        self.tables['cu']=self.tables['cu'].replace('192.0.2.3','192.0.2.99')
        self.assertFalse(self.alarm(self.run_detection()[1],'NGAPAssociationLost'))
    def test_provider_cannot_mutate_collector_inventory(self):
        from assurance.observe import ran_evidence
        snap=self.snapshot()
        def mutate(pod,peers,context,ns):
            pod['uid']='changed';context['namespace']='changed';return None
        ran_evidence(snap,self.state,mutate,lambda:self.now)
        self.assertEqual(snap['value'][0]['uid'],'cu-uid');self.assertEqual(self.state['context']['namespace'],'n')
    def test_full_collector_exposes_and_persists_scoped_events(self):
        import tempfile
        from pathlib import Path
        from assurance.observe import collect
        from assurance.cli import persist_collection
        from assurance.store import Store
        self.pods[1]['status']['conditions'][0]['status']='False'
        def read(args):return {'items':self.pods if args[1]=='pods' else []}
        def transport(*args):raise TimeoutError()
        telemetry,events=collect({'PROMETHEUS_URL':'offline'},self.state,read,None,transport=transport,
                                 clock=lambda:self.now,protocol_reader=self.observer)
        self.assertEqual(telemetry['services']['du']['status'],'DEGRADED')
        self.assertEqual(self.alarm(events,'DUUnavailable')[0]['status'],'firing')
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'journal.sqlite');persist_collection(store,telemetry,events,lambda:self.now)
            self.assertTrue(store.verify_audit());self.assertEqual(store.snapshot()['telemetry']['ran_evidence'],telemetry['ran_evidence'])
    def test_deleted_workload_is_unknown(self):
        self.pods[1]['metadata']['deletionTimestamp']='2026-10-23T00:00:00Z'
        t,e=self.run_detection(False);self.assertEqual(t['workloads'][1]['availability'],'UNKNOWN')
        self.assertFalse(self.alarm(e,'DUUnavailable'))
    def test_replayed_protocol_does_not_advance_persistence(self):
        from assurance.model import normalize
        self.change=lambda x:dict(x,observed_at=self.now-10)
        e=self.run_detection()[1][0];first=normalize(e,self.now)['delivery_id']
        self.now+=5;self.change=lambda x:dict(x,observed_at=self.now-15)
        again=self.run_detection()[1][0]
        self.assertEqual(normalize(again,self.now)['delivery_id'],first)
    def test_nonfinite_protocol_time_and_oversized_table_rejected(self):
        for changes in ({'observed_at':float('nan')},{'table':'x'*65537}):
            self.change=lambda x:dict(x,**changes)
            self.assertFalse(self.alarm(self.run_detection()[1],'F1AssociationLost'))


    def test_partial_unknown_transport_is_not_degraded_service(self):
        from assurance.adapters import service_health
        result=service_health(self.snapshot(),{'provenance':'LIVE','value':{'f1_association':None,'ngap_association':True}})
        self.assertEqual(result['ran_protocol']['status'],'UNAVAILABLE')
    def test_evidence_expiring_during_second_read_is_unknown(self):
        from assurance.observe import ran_evidence
        count=[0]
        def reader(*args):
            count[0]+=1
            if count[0]==2:self.now+=121
            return self.observer(*args)
        t,e=ran_evidence(self.snapshot(),self.state,reader,lambda:self.now)
        self.assertIsNone(t['associations']['f1']['value'])
        self.assertFalse(self.alarm(e,'F1AssociationLost'))


    def test_namespace_uid_normalization_is_bounded_identity(self):
        from assurance.model import normalize
        event=self.run_detection()[1][0]
        self.assertEqual(normalize(event,self.now)['observations']['namespace_uid'],'namespace-id')
        for bad in (False,1,'x'*201,'bad\nidentity'):
            event['observations']['namespace_uid']=bad
            with self.assertRaises(ValueError):normalize(event,self.now)
    def test_validated_protocol_binding_is_retained_without_table(self):
        t,_=self.run_detection();sample=t['protocols']['cu-uid']
        self.assertEqual(sample['binding']['namespace_uid'],'namespace-id')
        self.assertEqual(sample['binding']['peers']['f1']['uid'],'du-uid')
        self.assertNotIn('table',sample)



class P2AMFTests(unittest.TestCase):
    def setUp(self):
        self.now=2000000000.
        self.state={'active':'fran-oai','context':{'namespace':'n','namespace_uid':'namespace-id','cluster_uid':'cluster-id'},
                    'osm':{'active_instance_id':'ran-id','core_instance_id':'core-id'}}
        self.pod={'metadata':{'name':'intended-amf','namespace':'n','uid':'amf-uid','labels':{
            'app.kubernetes.io/name':'open5gs-amf','app.kubernetes.io/instance':'core-release','osm.etsi.org/ns-id':'core-id'}},
            'spec':{},'status':{'conditions':[{'type':'Ready','status':'True'}]}}
    def snapshot(self):
        from assurance.adapters import Kubernetes
        return Kubernetes(lambda _:{'items':[self.pod]},lambda:self.now).snapshot('n')
    def detect(self,snapshot=None):
        from assurance.observe import amf_evidence
        return amf_evidence(self.snapshot() if snapshot is None else snapshot,self.state,lambda:self.now)
    def test_healthy_amf_is_workload_ready_only(self):
        t,e=self.detect();self.assertEqual(t['availability'],'READY');self.assertEqual(e[0]['status'],'resolved')
        self.assertEqual(t['ue_registration'],'UNAVAILABLE');self.assertIsNone(e[0]['observations']['service_degraded'])
    def test_unhealthy_amf_emits_existing_alarm(self):
        self.pod['status']['conditions'][0]['status']='False'
        t,e=self.detect();self.assertEqual(t['availability'],'NOT_READY');self.assertEqual(e[0]['alarm_type'],'AMFUnavailable')
        self.assertEqual(e[0]['status'],'firing');self.assertEqual(e[0]['service'],'core-id')
    def test_missing_amf_no_failure_or_clear(self):
        s=self.snapshot();s['value']=[];t,e=self.detect(s)
        self.assertEqual(t['availability'],'MISSING');self.assertEqual(e,[])
    def test_wrong_owner_or_application_rejected(self):
        for key in ('osm.etsi.org/ns-id','app.kubernetes.io/name'):
            with self.subTest(key=key):
                s=self.snapshot();s['value'][0]['labels'][key]='unrelated';self.assertEqual(self.detect(s)[1],[])
    def test_ambiguous_identity_no_event(self):
        import copy
        s=self.snapshot();s['value'].append(copy.deepcopy(s['value'][0]));self.assertEqual(self.detect(s)[1],[])
    def test_release_identity_required(self):
        self.pod['metadata']['labels'].pop('app.kubernetes.io/instance');self.assertEqual(self.detect()[1],[])
    def test_conflicting_role_rejected(self):
        self.pod['metadata']['labels']['component']='upf';self.assertEqual(self.detect()[1],[])
    def test_incomplete_inventory_no_event(self):
        for key,value in (('truncated',True),('provenance','UNAVAILABLE'),('namespace','other')):
            s=self.snapshot();s[key]=value;self.assertEqual(self.detect(s)[1],[])
    def test_api_failure_unknown(self):
        from assurance.adapters import Kubernetes
        def failed(*args):raise TimeoutError('private')
        t,e=self.detect(Kubernetes(failed).snapshot('n'));self.assertEqual(t['availability'],'UNKNOWN');self.assertFalse(e)
    def test_unknown_or_terminating_not_failure(self):
        for status in ('Unknown',None):
            self.pod['status']['conditions'][0]['status']=status;self.assertEqual(self.detect()[1],[])
        self.pod['status']['conditions'][0]['status']='True';self.pod['metadata']['deletionTimestamp']='now'
        self.assertEqual(self.detect()[1],[])
    def test_stale_future_and_simulated_inventory_rejected(self):
        for delta in (-121,1):
            s=self.snapshot();s['observed_at']+=delta;self.assertEqual(self.detect(s)[1],[])
        s=self.snapshot();s['provenance']='SIMULATED';self.assertEqual(self.detect(s)[1],[])
    def test_unbound_context_no_event(self):
        self.state['context'].pop('namespace_uid');self.assertEqual(self.detect()[1],[])
    def test_replacement_does_not_inherit_alarm_key(self):
        from assurance.model import normalize
        e=self.detect()[1][0];self.pod['metadata']['uid']='replacement';other=self.detect()[1][0]
        self.assertNotEqual(normalize(e,self.now)['key'],normalize(other,self.now)['key'])
    def test_incident_dedup_clear_and_missing_does_not_clear(self):
        from assurance.store import Store
        from assurance.engine import Engine
        from assurance.catalog import load_catalog
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite');engine=Engine(store,load_catalog(),lambda:self.now)
            self.pod['status']['conditions'][0]['status']='False'
            for delta in (0,60,0):
                self.now+=delta;engine.ingest(self.detect()[1][0])
            self.assertEqual(len(store.snapshot()['incident']),1)
            s=self.snapshot();s['value']=[];self.assertFalse(self.detect(s)[1])
            self.assertNotEqual(store.snapshot()['alarm'][0]['state'],'CLEARED')
            self.pod['status']['conditions'][0]['status']='True'
            for delta in (1,60):
                self.now+=delta;engine.ingest(self.detect()[1][0])
            self.assertEqual(store.snapshot()['incident'][0]['state'],'RESOLVED');self.assertTrue(store.verify_audit())
    def test_collector_exposes_amf_without_registration_claim(self):
        def read(args):return {'items':[self.pod] if args[1]=='pods' else []}
        telemetry,events=collect({'PROMETHEUS_URL':'offline'},self.state,read,None,
            transport=Mock(side_effect=TimeoutError()),clock=lambda:self.now)
        self.assertEqual(telemetry['services']['amf']['status'],'WORKLOAD_READY')
        self.assertEqual(telemetry['services']['ue_registration']['status'],'UNAVAILABLE')
        self.assertTrue(any(e['alarm_type']=='AMFUnavailable' for e in events))

if __name__=='__main__':unittest.main()
