"""Offline assurance and safety contracts: never contacts or mutates live resources."""
import concurrent.futures
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.model import normalize, PROTECTED_NS
from assurance.catalog import load_catalog
from assurance.store import Store
from assurance.engine import Engine
from assurance.policy import ProductionExecutor, SafetyValidator, Verification
from assurance.adapters import Prometheus, Kubernetes, OSM, ProtocolEvidence, service_health, Alertmanager
from assurance.simulation import run_all
from assurance.governance import LifecycleGate, transition_matrix

class AssuranceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'journal.sqlite';self.now=2000000000.
        self.store=Store(self.path);self.catalog=load_catalog();self.engine=Engine(self.store,self.catalog,lambda:self.now)
        self.event={'alarm_type':'DUUnavailable','timestamp':self.now,'cluster':'c','namespace':'n',
            'resource':'du','service':'ns','node':'node','observations':{'pod_ready':False,'f1_association':False}}
    def ingest(self,delta=0,**changes):
        self.now+=delta;return self.engine.ingest({**self.event,'timestamp':self.now,**changes})
    def raised(self):self.ingest();return self.ingest(60)
    def test_normalization_redacts_untrusted_annotations(self):
        raw={**self.event,'annotations':{'secret':'credential'},'observations':{'token':'secret','imsi':'subscriber','pod_ready':False},'fingerprint':'untrusted'}
        normalized=normalize(raw,self.now)
        self.assertNotIn('secret',json.dumps(normalized));self.assertNotIn('subscriber',json.dumps(normalized))
        self.assertEqual(normalized['provenance'],'LIVE')
    def test_timestamp_and_nan_rejected(self):
        for changes in ({'timestamp':self.now+60},{'timestamp':'2026-01-01T00:00:00'},{'observations':{'host_pressure':float('nan')}}):
            with self.assertRaises(ValueError):normalize({**self.event,**changes},self.now)
    def test_duplicate_delivery_and_source_deduplication(self):
        a=self.raised();self.ingest(source='rancher');self.ingest(source='rancher')
        out=self.store.snapshot();self.assertEqual(len(out['alarm']),1);self.assertEqual(len(out['incident']),1);self.assertEqual(len(out['action']),1)
    def test_persistence_and_clear_hysteresis(self):
        self.ingest();self.assertFalse(self.store.snapshot()['incident'])
        self.ingest(59);self.assertFalse(self.store.snapshot()['incident'])
        self.ingest(1);self.assertEqual(len(self.store.snapshot()['incident']),1)
        self.ingest(1,status='resolved');self.assertNotEqual(self.store.snapshot()['alarm'][0]['state'],'CLEARED')
        self.ingest(60,status='resolved');self.assertEqual(self.store.snapshot()['incident'][0]['state'],'RESOLVED')
        self.assertEqual(self.store.snapshot()['action'][0]['status'],'CANCELLED')
    def test_flapping_suppression(self):
        for i in range(5):self.ingest(10,status='firing' if i%2==0 else 'resolved')
        self.ingest(60);self.assertTrue(self.store.snapshot()['alarm'][0]['flapping']);self.assertFalse(self.store.snapshot()['incident'])
    def test_stale_and_gap_reset(self):
        self.ingest();self.ingest(100);self.assertFalse(self.store.snapshot()['incident'])
        self.now+=121;self.engine.refresh();self.assertTrue(self.store.snapshot()['alarm'][0]['stale'])
        self.assertFalse(self.store.snapshot()['incident'])
    def test_out_of_order_cannot_clear(self):
        self.raised();old=self.engine.ingest({**self.event,'status':'resolved'})
        self.assertEqual(old['status'],'firing')
    def test_node_inhibition_scope_and_correlation(self):
        self.ingest(alarm_type='KubernetesNodeNotReady',resource='node',observations={'node_ready':False})
        self.ingest(60,alarm_type='KubernetesNodeNotReady',resource='node',observations={'node_ready':False})
        alarm=self.raised();self.assertTrue(alarm['inhibited_by'])
        other=self.ingest(resource='other',node='different');self.assertFalse(other['inhibited_by'])
    def test_root_alarm_added_after_child_inhibits_existing_alarm(self):
        self.raised();self.ingest(alarm_type='CUUnavailable',resource='cu',observations={'pod_ready':False,'f1_association':False,'ngap_association':False})
        self.ingest(60,alarm_type='CUUnavailable',resource='cu',observations={'pod_ready':False,'f1_association':False,'ngap_association':False})
        self.ingest(alarm_type='F1AssociationLost',resource='f1',observations={'pod_ready':False,'f1_association':False})
        self.ingest(60,alarm_type='F1AssociationLost',resource='f1',observations={'pod_ready':False,'f1_association':False})
        alarms=self.store.snapshot()['alarm'];self.assertTrue(next(a for a in alarms if a['alarm_type']=='F1AssociationLost')['inhibited_by'])
        self.assertEqual(len(self.store.snapshot()['incident']),1)
    def test_protected_service_and_unknown_state_fail_closed(self):
        for ns in PROTECTED_NS:
            a=normalize({**self.event,'service':ns},self.now);self.assertTrue(self.engine.policy.evaluate(a)['protected'])
        self.assertTrue(self.engine.policy.evaluate(normalize(self.event,self.now))['protected'])
    def test_maintenance_acknowledgement_and_history(self):
        self.engine.maintenance('ns',self.now,self.now+600,'operator')
        a=self.raised();self.assertFalse(self.store.snapshot()['incident'])
        self.engine.acknowledge(a['key'],'operator');self.assertTrue(self.store.snapshot()['alarm'][0]['acknowledged'])
        self.assertTrue(self.store.verify_audit())
    def test_approval_cancel_idempotency_and_emergency_stop(self):
        self.raised();action=self.store.snapshot()['action'][0]
        self.engine.decide(action['action_id'],'APPROVED','operator');self.engine.decide(action['action_id'],'APPROVED','operator')
        self.engine.control('APPROVAL_REQUIRED',True)
        reasons=self.engine.validate_action(action['action_id'],{})
        self.assertIn('emergency stop',reasons);self.assertIn('cooldown active or unknown',reasons)
        self.engine.decide(action['action_id'],'CANCELLED','operator')
        with self.assertRaises(ValueError):self.engine.decide(action['action_id'],'APPROVED','operator')
        with self.assertRaises(ValueError):self.engine.control('LIVE_AUTO')
    def test_severity_and_rollback(self):
        a=self.ingest(alarm_type='AMFUnavailable',severity='INFO');self.assertEqual(a['severity'],'CRITICAL')
        p=self.engine.policy.evaluate(a);self.assertEqual(p['action'],'observe_notify');self.assertIn('Preserve',p['rollback'])
    def test_cpu_cannot_switch(self):
        p=self.engine.policy.evaluate(normalize({**self.event,'alarm_type':'HostCPUContentionHigh','observations':{'host_pressure':.9}},self.now))
        self.assertEqual(p['action'],'observe_notify');self.assertFalse(p['automation_eligible']);self.assertTrue(p['missing_evidence'])
    def test_audit_append_only_and_restart_durability(self):
        self.raised();other=Store(self.path);self.assertTrue(other.verify_audit());self.assertEqual(len(other.snapshot()['incident']),1)
        with self.assertRaises(sqlite3.IntegrityError):
            with other.transaction() as db:db.execute('DELETE FROM audit')
    def test_concurrent_delivery_serialization(self):
        raw=copy.deepcopy(self.event)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(self.engine.ingest,[raw]*8))
        self.assertEqual(len(self.store.snapshot()['alarm']),1)
    def test_missing_monitoring_never_zero(self):
        for response in ({'status':'error'},{'status':'success','data':{'resultType':'vector','result':[]}}):
            x=Prometheus('https://metrics',lambda _:response,lambda:self.now).query('up')
            self.assertEqual(x['provenance'],'UNAVAILABLE');self.assertIsNone(x['value'])
    def test_stale_prometheus_nan_and_api_failure(self):
        response={'status':'success','data':{'resultType':'vector','result':[{'value':[self.now-121,'1']}]}}
        self.assertEqual(Prometheus('https://metrics',lambda _:response,lambda:self.now).query('up')['provenance'],'STALE')
        response['data']['result'][0]['value'][1]='NaN'
        self.assertEqual(Prometheus('https://metrics',lambda _:response,lambda:self.now).query('up')['provenance'],'UNAVAILABLE')
    def test_kubernetes_osm_alertmanager_failures(self):
        def broken(*args):raise TimeoutError('sensitive details')
        self.assertEqual(Kubernetes(broken).snapshot('n')['provenance'],'UNAVAILABLE')
        self.assertEqual(Kubernetes(broken).infrastructure()['provenance'],'UNAVAILABLE')
        client=Mock();client.get_ns_instance.side_effect=broken
        self.assertEqual(OSM(client).snapshot('id')['provenance'],'UNAVAILABLE')
        self.assertEqual(Alertmanager('https://am',broken).snapshot()['provenance'],'UNAVAILABLE')
    def test_osm_completed_not_protocol_healthy(self):
        client=Mock();client.get_ns_instance.return_value={'_id':'id','nsState':'READY'};client.get_operation.return_value={'nsInstanceId':'id','operationState':'COMPLETED'}
        x=OSM(client).snapshot('id','op');self.assertEqual(x['protocol_health'],'UNAVAILABLE')
        client.get_operation.return_value={'nsInstanceId':'different'};self.assertEqual(OSM(client).snapshot('id','op')['provenance'],'UNAVAILABLE')
    def test_transport_distinct_from_service(self):
        table='ST LPORT RPORT\n3 38472 38472\n3 1234 38412\n'
        x=ProtocolEvidence.from_sctp(table)
        self.assertTrue(x['value']['f1_association']);self.assertIsNone(x['value']['f1_setup']);self.assertIsNone(x['value']['ue_registered'])
        self.assertEqual(service_health({'provenance':'UNAVAILABLE'})['core']['status'],'UNAVAILABLE')
    def test_simulation_all_16_and_no_production_executor_calls(self):
        with patch('assurance.policy.ProductionExecutor.execute',side_effect=AssertionError('production invoked')),patch('subprocess.run',side_effect=AssertionError('subprocess invoked')),patch('urllib.request.urlopen',side_effect=AssertionError('network invoked')):
            results=run_all()
        self.assertEqual(len(results),16)
        for result in results:
            self.assertEqual(result['provenance'],'SIMULATED');self.assertTrue(result['audit_valid']);self.assertTrue(result['verification']['passed']);self.assertEqual(result['production_calls'],0)
        self.assertFalse(self.store.snapshot()['incident'])
    def test_production_executor_forbidden_even_after_approval(self):
        with self.assertRaises(PermissionError):ProductionExecutor().execute({'status':'APPROVED'})
        action={'action':'core_restart','class':'D','provenance':'SIMULATED','service':next(iter(PROTECTED_NS)),'status':'APPROVED'}
        reasons=SafetyValidator().validate(action,{},mode='APPROVAL_REQUIRED');self.assertIn('forbidden action',reasons);self.assertIn('simulation cannot invoke production executor',reasons)
    def test_incident_transition_verification_failure(self):
        self.raised();i=self.store.snapshot()['incident'][0]
        with self.store.transaction() as db:
            with self.assertRaises(ValueError):self.engine.transition(db,i,'RESOLVED',self.now+1) if i['state']=='DETECTED' else self.engine.transition(db,i,'TRIAGED',self.now+1)
        self.assertFalse(Verification.assess({'core_healthy':False},['core_healthy'])['passed'])
    def test_transition_matrix_blocks_all_unvalidated_transitions(self):
        from scenario_registry import load_registry
        matrix=transition_matrix(load_registry());self.assertTrue(matrix);self.assertTrue(all(not m['auto_allowed'] and m['proposal_only'] for m in matrix))
    def test_lifecycle_gate_reuses_lock_and_blocks_pending(self):
        path=Path(self.tmp.name)/'active-ran.yaml';path.write_text('active: test\nosm:\n  pending_instance: {}\n')
        gate=LifecycleGate(path,{},{});
        with patch('assurance.governance.validate_state',return_value={'osm':{'pending_instance':{'id':'pending'}}}),patch('assurance.governance.exclusive_lock') as lock:
            with self.assertRaises(PermissionError):
                with gate.locked_state():pass
            lock.assert_called_once_with(path.parent/'.lifecycle.lock')
    def test_durable_idempotency_cooldown_and_ambiguous_timeout(self):
        from assurance.journal import ActionJournal
        journal=ActionJournal(self.store)
        action={'action_id':'a','service':'s','action':'sandbox_recovery','provenance':'SIMULATED','cooldown':600}
        _,created=journal.claim_simulated(action,self.now);self.assertTrue(created)
        restarted=ActionJournal(Store(self.path))
        _,created=restarted.claim_simulated(action,self.now+10);self.assertFalse(created)
        restarted.finish_simulated('a','TIMEOUT_AMBIGUOUS',self.now+10)
        entry,created=restarted.claim_simulated(action,self.now+700)
        self.assertFalse(created);self.assertEqual(entry['status'],'TIMEOUT_AMBIGUOUS')
        with self.assertRaises(PermissionError):journal.claim_simulated({**action,'action_id':'b'},self.now+20)
        with self.assertRaises(PermissionError):journal.claim_simulated({**action,'provenance':'LIVE'},self.now+700)
        _,created=journal.claim_simulated({**action,'action_id':'b'},self.now+700);self.assertTrue(created)
    def test_optional_log_adapter_freshness_and_distinct_protocol_states(self):
        from datetime import datetime, timezone
        at=datetime.fromtimestamp(self.now,tz=timezone.utc).isoformat()
        text=at+' F1SetupResponse imsi=PRIVATE\n'+at+' NGSetupResponse\n'+at+' configuration parser error\n'
        result=ProtocolEvidence.from_timestamped_logs(text,lambda:self.now)
        self.assertTrue(result['value']['f1_setup']);self.assertTrue(result['value']['ng_setup'])
        self.assertTrue(result['value']['configuration_failure']);self.assertIsNone(result['value']['ue_registered'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        stale=ProtocolEvidence.from_timestamped_logs(text,lambda:self.now+121)
        self.assertEqual(stale['provenance'],'STALE');self.assertIsNone(stale['value']['f1_setup'])
        self.assertEqual(ProtocolEvidence.from_sctp('unavailable')['provenance'],'UNAVAILABLE')
    def test_contradictory_evidence_cannot_propose_targeted_recovery(self):
        alarm=normalize({**self.event,'observations':{'pod_ready':True,'f1_association':True}},self.now)
        decision=self.engine.policy.evaluate(alarm)
        self.assertFalse(decision['corroborated']);self.assertEqual(decision['action'],'observe_notify')
    def test_direct_engine_rejects_synthetic_input_and_one_sample_persistence(self):
        with self.assertRaises(ValueError):self.engine.ingest({**self.event,'provenance':'SIMULATED'})
        self.ingest();self.now+=70;self.engine.refresh();self.assertFalse(self.store.snapshot()['incident'])
    def test_native_webhook_missing_evidence_remains_non_autonomous(self):
        from flask import Flask
        from assurance.api import register
        app=Flask(__name__);register(app,lambda:{'RUNTIME_DIR':self.tmp.name});client=app.test_client()
        payload={'alerts':[{'labels':{'alertname':'DUUnavailable'},'status':'firing','startsAt':'2026-01-01T00:00:00Z'}]}
        with patch.dict(os.environ,{'L3_WEBHOOK_TOKEN':'b'*32}):
            for _ in range(2):self.assertEqual(client.post('/api/assurance/webhook',json=payload,headers={'Authorization':'Bearer '+'b'*32}).status_code,200)
        snapshot=client.get('/api/assurance').json
        self.assertEqual(len(snapshot['alarm']),1);self.assertFalse(snapshot['alarm'][0]['automation_eligible'])
        self.assertFalse(snapshot['incident'])
    def test_api_auth_simulation_and_duplicate_webhook(self):
        from flask import Flask
        from assurance.api import register
        app=Flask(__name__);register(app,lambda:{'RUNTIME_DIR':self.tmp.name});client=app.test_client()
        self.assertEqual(client.post('/api/assurance/operator',json={'command':'control','mode':'LIVE_AUTO'}).status_code,403)
        with patch.dict(os.environ,{'L3_OPERATOR_TOKEN':'a'*32,'L3_WEBHOOK_TOKEN':'b'*32}):
            self.assertEqual(client.post('/api/assurance/operator',json={'command':'control','mode':'LIVE_AUTO'},headers={'Authorization':'Bearer '+'a'*32}).status_code,400)
            headers={'Authorization':'Bearer '+'b'*32}
            raw={**self.event,'timestamp':__import__('time').time()}
            for _ in range(2):self.assertEqual(client.post('/api/assurance/webhook',json={'alerts':[raw]},headers=headers).status_code,200)
            self.assertEqual(len(client.get('/api/assurance').json['alarm']),1)
            self.assertEqual(client.post('/api/assurance/webhook',json={'alerts':[{**raw,'provenance':'SIMULATED'}]},headers=headers).status_code,400)
        self.assertFalse(client.get('/api/assurance').json['live_auto_enabled'])

if __name__=='__main__':unittest.main()
