"""Offline resource evidence; reserved addresses, temporary journals, no API calls."""
import copy
import tempfile
from pathlib import Path
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from unittest.mock import Mock
from assurance.adapters import Kubernetes,resource_quantity,pod_requests
from assurance.observe import cpu_contention_evidence,capacity_evidence,collect

class ResourceFixtures(unittest.TestCase):
    def setUp(self):
        self.now=2000000000.
        self.state={'active':'fran-oai','context':{'namespace':'n','namespace_uid':'namespace-id','cluster_uid':'cluster-id'},
                    'osm':{'active_instance_id':'ran-id','core_instance_id':'core-id'}}
        self.pod={'metadata':{'name':'du','namespace':'n','uid':'du-uid','labels':{'app':'fran-oai-du','component':'du','osm.etsi.org/ns-id':'ran-id'}},
            'spec':{'nodeName':'node','containers':[{'name':'du','resources':{'requests':{'cpu':'500m','memory':'256Mi'}}}]},
            'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}}
        self.node={'metadata':{'name':'node','uid':'node-uid','labels':{'zone':'a'}},'spec':{},
            'status':{'allocatable':{'cpu':'2','memory':'2Gi'},'addresses':[{'type':'InternalIP','address':'192.0.2.1'}],
                'conditions':[{'type':k,'status':v} for k,v in [('Ready','True'),('MemoryPressure','False'),('DiskPressure','False'),('PIDPressure','False')]]}}
        self.warning={'metadata':{'uid':'event-uid','namespace':'n'},'reason':'FailedScheduling','message':'0/1 nodes: Insufficient cpu',
            'reportingController':'default-scheduler','eventTime':'2033-05-18T03:33:20Z','involvedObject':{'kind':'Pod','name':'du','uid':'du-uid'}}
        self.labels={'job':'node-exporter','instance':'192.0.2.1:9100'}
    def telemetry(self):
        api=Kubernetes(lambda args:{'items':[self.pod] if args[1]=='pods' else [self.node] if args[1]=='nodes' else [self.warning]},lambda:self.now)
        return {'kubernetes':api.snapshot('n'),'nodes':api.infrastructure(),'kubernetes_warnings':api.warnings('n'),
            'cpu_pressure':{'provenance':'LIVE','observed_at':self.now,'value':[{'at':self.now,'value':.3,'labels':dict(self.labels)}]},
            'prometheus_targets':{'provenance':'LIVE','observed_at':self.now,'value':[{'labels':dict(self.labels),'health':'up','fresh':True,'last_scrape':self.now}]}}
    def pending(self):
        self.pod['spec'].pop('nodeName',None);self.pod['status']['phase']='Pending'
        self.pod['spec']['containers'][0]['resources']['requests']['cpu']='3'
    def cpu(self,t=None):return cpu_contention_evidence(t or self.telemetry(),self.state,lambda:self.now)
    def capacity(self,t=None):return capacity_evidence(t or self.telemetry(),self.state,lambda:self.now)

class CPUTests(ResourceFixtures):
    def test_valid_pressure_and_units(self):
        t,e=self.cpu();self.assertEqual(t['unit'],'ratio');self.assertEqual(t['window_seconds'],300)
        self.assertEqual(e[0]['status'],'firing');self.assertIsNone(e[0]['observations']['service_degraded'])
    def test_high_utilization_without_pressure_not_contention(self):
        t=self.telemetry();t['cpu_utilization']=1.;t['cpu_pressure']['value'][0]['value']=.01
        self.assertEqual(self.cpu(t)[1][0]['status'],'resolved')
    def test_throttling_without_pressure_not_contention(self):
        t=self.telemetry();t['cpu_throttling']=1.;t['cpu_pressure']['provenance']='UNAVAILABLE'
        self.assertFalse(self.cpu(t)[1])
    def test_stale_or_missing_pressure_no_event(self):
        for provenance in ('STALE','UNAVAILABLE'):
            t=self.telemetry();t['cpu_pressure']['provenance']=provenance;self.assertFalse(self.cpu(t)[1])
        t=self.telemetry();t['cpu_pressure']['value']=[];self.assertFalse(self.cpu(t)[1])
    def test_source_time_not_receipt_time(self):
        for at in (self.now-121,self.now+1):
            t=self.telemetry();t['cpu_pressure']['value'][0]['at']=at;self.assertFalse(self.cpu(t)[1])
    def test_bad_labels_rejected(self):
        for labels in ({},{'job':'other','instance':'192.0.2.1:9100'},dict(self.labels,node='wrong'),dict(self.labels,instance='192.0.2.99:9100'),dict(self.labels,namespace='other'),dict(self.labels,cluster='other-cluster')):
            t=self.telemetry();t['cpu_pressure']['value'][0]['labels']=labels;self.assertFalse(self.cpu(t)[1])
    def test_wrong_or_stale_target_rejected(self):
        for changes in ({'health':'down'},{'fresh':False},{'last_scrape':self.now-121}):
            t=self.telemetry();t['prometheus_targets']['value'][0].update(changes);self.assertFalse(self.cpu(t)[1])
    def test_metric_value_bounds(self):
        for value in (True,float('nan'),float('inf'),-1,1.1):
            t=self.telemetry();t['cpu_pressure']['value'][0]['value']=value;self.assertFalse(self.cpu(t)[1])
    def test_wrong_workload_owner_or_namespace(self):
        self.pod['metadata']['labels']['osm.etsi.org/ns-id']='other';self.assertFalse(self.cpu()[1])
        self.pod['metadata']['labels'].update({'osm.etsi.org/ns-id':'ran-id','component':'wrong'});self.assertFalse(self.cpu()[1])
    def test_ambiguous_source_or_node(self):
        for field in ('cpu_pressure','nodes','kubernetes'):
            t=self.telemetry();t[field]['value']*=2;self.assertFalse(self.cpu(t)[1])
    def test_transient_and_sustained_pressure_and_clearing(self):
        from assurance.engine import Engine
        from assurance.store import Store
        from assurance.catalog import load_catalog
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');engine=Engine(store,load_catalog(),lambda:self.now)
            engine.ingest(self.cpu()[1][0]);self.assertFalse(store.snapshot()['incident'])
            self.now+=60;engine.ingest(self.cpu()[1][0]);self.assertEqual(len(store.snapshot()['incident']),1)
            for delta in (1,60):
                self.now+=delta;t=self.telemetry();t['cpu_pressure']['value'][0]['value']=0.;engine.ingest(self.cpu(t)[1][0])
            self.assertEqual(store.snapshot()['incident'][0]['state'],'RESOLVED')
    def test_no_disruptive_proposal(self):
        from assurance.model import normalize
        from assurance.policy import Policy,ProductionExecutor
        from assurance.catalog import load_catalog
        action=Policy(load_catalog()).evaluate(normalize(self.cpu()[1][0],self.now))
        self.assertEqual(action['action'],'observe_notify');self.assertFalse(action['automation_eligible'])
        with self.assertRaises(PermissionError):ProductionExecutor().execute()
    def test_duplicate_prometheus_source_timestamps_rejected(self):
        from assurance.adapters import Prometheus
        def response(rows):return {'status':'success','data':{'resultType':'vector','result':rows}}
        sample={'metric':self.labels,'value':[self.now,'0.3']};stamp={'metric':self.labels,'value':[self.now,str(self.now)]}
        p=Prometheus('offline',Mock(side_effect=[response([sample]),response([stamp,stamp])]),lambda:self.now)
        self.assertEqual(p.measurement('rate(psi[5m])','psi')['provenance'],'UNAVAILABLE')

    def test_isolated_pressure_spike_does_not_create_incident(self):
        from assurance.engine import Engine
        from assurance.store import Store
        from assurance.catalog import load_catalog
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');engine=Engine(store,load_catalog(),lambda:self.now)
            engine.ingest(self.cpu()[1][0]);self.now+=60
            t=self.telemetry();t['cpu_pressure']['value'][0]['value']=0.;engine.ingest(self.cpu(t)[1][0])
            self.assertFalse(store.snapshot()['incident'])
    def test_incomplete_or_unbound_workload_inventory_no_cpu_event(self):
        t=self.telemetry();t['kubernetes']['truncated']=True;self.assertFalse(self.cpu(t)[1])
        self.state['context'].pop('namespace_uid');self.assertFalse(self.cpu()[1])
    def test_missing_source_node_address_no_guessing(self):
        t=self.telemetry();t['nodes']['value'][0]['addresses']=[];self.assertFalse(self.cpu(t)[1])

    def test_full_collector_cpu_uses_existing_query_budget(self):
        import datetime
        from urllib.parse import urlsplit,parse_qs
        requests=[]
        def transport(url):
            requests.append(url)
            if '/targets?' in url:
                stamp=datetime.datetime.fromtimestamp(self.now,datetime.timezone.utc).isoformat()
                return {'status':'success','data':{'activeTargets':[{'labels':self.labels,'health':'up','lastScrape':stamp}]}}
            expression=parse_qs(urlsplit(url).query)['query'][0]
            value=str(self.now) if expression.startswith('timestamp(') else '0.3'
            return {'status':'success','data':{'resultType':'vector','result':[{'metric':self.labels,'value':[self.now,value]}]}}
        def read(args):return {'items':[self.pod] if args[1]=='pods' else [self.node] if args[1]=='nodes' else []}
        telemetry,events=collect({'PROMETHEUS_URL':'http://offline.invalid'},self.state,read,None,transport=transport,clock=lambda:self.now)
        self.assertEqual(len(requests),4);self.assertEqual(telemetry['cpu_contention']['provenance'],'LIVE')
        self.assertTrue(any(e['alarm_type']=='HostCPUContentionHigh' for e in events))

class QuantityTests(unittest.TestCase):
    def test_cpu_units_and_rounding(self):
        for text,value in [('500m',.5),('2',2.),('1000000u',1.),('1000000000n',1.),('1e-3',.001),('.0001',.001)]:
            self.assertEqual(resource_quantity(text,cpu=True),value)
    def test_memory_units(self):
        for text,value in [('1Ki',1024),('1Mi',1048576),('2Gi',2147483648),('1G',1000000000),('1e3',1000),('1m',1)]:
            self.assertEqual(resource_quantity(text),value)
    def test_invalid_units_and_values(self):
        for value in (None,True,-1,'-1','nan','1XB','1e99',''):
            with self.assertRaises(ValueError):resource_quantity(value)
        with self.assertRaises(ValueError):resource_quantity('1Mi',cpu=True)
    def test_regular_requests_not_usage(self):
        spec={'containers':[{'resources':{'requests':{'cpu':'500m','memory':'1Mi'}}},{'resources':{}}]}
        self.assertEqual(pod_requests(spec),{'cpu':.5,'memory':1048576})
        spec['containers'][1]['resources']={'limits':{'cpu':'1'}};self.assertEqual(pod_requests(spec)['cpu'],1.5)
        spec={'containers':[{'resources':{'requests':{'cpu':v}}} for v in ('100m','200m')]}
        self.assertEqual(pod_requests(spec)['cpu'],.3)
    def test_unsupported_accounting_is_unknown(self):
        for extra in ({'initContainers':[{}]},{'overhead':{'cpu':'1'}},{'resources':{'requests':{}}}):
            self.assertIsNone(pod_requests({'containers':[{}],**extra}))
        self.assertIsNone(pod_requests({'containers':[{'resources':{'requests':{'gpu':'1'}}}]}))

class CapacityTests(ResourceFixtures):
    def test_shortage_requires_bounded_scheduler_evidence(self):
        self.pending();t,e=self.capacity();self.assertEqual(t['value'][0]['status'],'PROVEN_SHORTAGE')
        self.assertEqual(e[0]['status'],'firing')
    def test_memory_shortage(self):
        self.pending();r=self.pod['spec']['containers'][0]['resources']['requests'];r.update(cpu='1',memory='3Gi')
        self.warning['message']='Insufficient memory';self.assertTrue(self.capacity()[1])
    def test_known_namespace_allocations_are_lower_bound(self):
        self.pending();t=self.telemetry();other=copy.deepcopy(t['kubernetes']['value'][0]);other.update(uid='other',name='other',node='node',phase='Running',requests={'cpu':1.,'memory':0})
        t['kubernetes']['value'].append(other);t['kubernetes']['value'][0]['requests']['cpu']=1.5
        row=self.capacity(t)[0]['value'][0];self.assertEqual(row['eligible_nodes'][0]['headroom_upper_bound']['cpu'],1.)
        self.assertEqual(row['status'],'PROVEN_SHORTAGE')
        t['kubernetes']['value'][0]['requests']={'cpu':0.,'memory':0}
        t['kubernetes']['value'][1]['requests']['cpu']=10.
        self.assertFalse(self.capacity(t)[1])
    def test_fit_does_not_mean_schedulable_or_healthy(self):
        self.pending();self.pod['spec']['containers'][0]['resources']['requests']['cpu']='1'
        t,e=self.capacity();self.assertEqual(t['value'][0]['status'],'FIT_NOT_PROVEN');self.assertFalse(e)
        t=self.telemetry();t['nodes']['value'][0]['allocatable']['cpu']='300m'
        t['kubernetes']['value'][0]['requests']['cpu']=.1
        other=copy.deepcopy(t['kubernetes']['value'][0]);other.update(uid='allocated',name='allocated',node='node',phase='Running',requests={'cpu':.2,'memory':0})
        t['kubernetes']['value'].append(other)
        self.assertFalse(self.capacity(t)[1])
    def test_node_selector_constraints(self):
        self.pending();self.pod['spec']['nodeSelector']={'zone':'b'}
        t,e=self.capacity();self.assertEqual(t['value'][0]['status'],'CONSTRAINTS_UNSATISFIED');self.assertFalse(e)
    def test_taints_not_resource_shortage(self):
        self.pending();self.node['spec']['taints']=[{'key':'dedicated','value':'other','effect':'NoSchedule'}]
        self.assertFalse(self.capacity()[1])
        self.pod['spec']['tolerations']=[{'key':'dedicated','operator':'Exists','effect':''}]
        self.assertTrue(self.capacity()[1])
    def test_unknown_affinity_no_capacity_claim(self):
        self.pending();self.pod['spec']['affinity']={'nodeAffinity':{}};self.assertFalse(self.capacity()[1])
    def test_unrelated_pending_causes_not_shortage(self):
        self.pending()
        for message in ('untolerated taint','unbound PersistentVolumeClaims','node affinity mismatch'):
            self.warning['message']=message;self.assertFalse(self.capacity()[1])
    def test_warning_must_match_uid_and_scheduler(self):
        self.pending()
        for field,value in (('uid','different'),('kind','Deployment')):
            t=self.telemetry();t['kubernetes_warnings']['value'][0]['resource'][field]=value;self.assertFalse(self.capacity(t)[1])
        self.warning['reportingController']='other';self.assertFalse(self.capacity()[1])
    def test_stale_warning_not_failure(self):
        self.pending();t=self.telemetry();t['kubernetes_warnings']['value'][0]['at']-=121
        self.assertFalse(self.capacity(t)[1])
    def test_incomplete_inventory_never_claims_shortage(self):
        self.pending()
        for field in ('kubernetes','nodes','kubernetes_warnings'):
            t=self.telemetry();t[field]['truncated']=True;self.assertFalse(self.capacity(t)[1])
    def test_unknown_node_or_pressure(self):
        self.pending()
        for key,value in (('Ready','Unknown'),('MemoryPressure','True'),('PIDPressure','Unknown')):
            t=self.telemetry();t['nodes']['value'][0]['conditions'][key]=value;self.assertFalse(self.capacity(t)[1])
    def test_invalid_allocatable_and_unknown_requests(self):
        self.pending();t=self.telemetry();t['nodes']['value'][0]['allocatable']['cpu']='bad';self.assertFalse(self.capacity(t)[1])
        t=self.telemetry();t['kubernetes']['value'][0]['requests']=None;self.assertFalse(self.capacity(t)[1])
    def test_other_eligible_node_can_prevent_shortage(self):
        self.pending();t=self.telemetry();other=copy.deepcopy(t['nodes']['value'][0]);other.update(name='second',uid='second-uid',allocatable={'cpu':'4','memory':'4Gi'})
        t['nodes']['value'].append(other);self.assertFalse(self.capacity(t)[1])
    def test_duplicate_node_identity_unknown(self):
        self.pending();t=self.telemetry();t['nodes']['value']*=2;self.assertFalse(self.capacity(t)[1])
    def test_missing_or_replaced_pending_pod_no_clear(self):
        self.pending();t=self.telemetry();t['kubernetes']['value'][0]['uid']='new';self.assertFalse(self.capacity(t)[1])
        t['kubernetes']['value']=[];self.assertFalse(self.capacity(t)[1])
    def test_scheduling_confirmation_clears_with_existing_window(self):
        from assurance.engine import Engine
        from assurance.store import Store
        from assurance.catalog import load_catalog
        self.pending()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');engine=Engine(store,load_catalog(),lambda:self.now)
            e=self.capacity()[1][0];engine.ingest(e);engine.ingest(e);self.assertEqual(len(store.snapshot()['incident']),1)
            self.pod['spec']['nodeName']='node';self.pod['status']['conditions'].append({'type':'PodScheduled','status':'True'})
            for delta in (1,60):
                self.now+=delta;engine.ingest(self.capacity()[1][0])
            self.assertEqual(store.snapshot()['incident'][0]['state'],'RESOLVED');self.assertTrue(store.verify_audit())
    def test_warning_messages_are_not_retained(self):
        self.warning['message']+=' private subscriber data';t=self.telemetry()
        self.assertNotIn('private subscriber',str(t['kubernetes_warnings']))


    def test_no_capacity_recovery_recommendation(self):
        from assurance.policy import Policy
        from assurance.catalog import load_catalog
        from assurance.model import normalize
        self.pending();action=Policy(load_catalog()).evaluate(normalize(self.capacity()[1][0],self.now))
        self.assertEqual(action['action'],'observe_notify');self.assertEqual(action['class'],'A')
    def test_supported_constraints_exclude_only_ineligible_nodes(self):
        self.pending();t=self.telemetry();other=copy.deepcopy(t['nodes']['value'][0])
        other.update(name='other',uid='other-uid',allocatable={'cpu':'4','memory':'4Gi'},scheduling_labels={'zone':'b'})
        t['nodes']['value'].append(other);t['kubernetes']['value'][0]['scheduling']['nodeSelector']={'zone':'a'}
        self.assertTrue(self.capacity(t)[1])
    def test_missing_warning_source_and_pressure_not_shortage(self):
        self.pending();t=self.telemetry();t['kubernetes_warnings']['provenance']='UNAVAILABLE';self.assertFalse(self.capacity(t)[1])
        self.node['status']['conditions'][1]['status']='True';self.assertFalse(self.capacity()[1])
    def test_full_collector_uses_only_existing_three_kubernetes_reads(self):
        self.pending();commands=[]
        def read(args):
            commands.append(args)
            return {'items':[self.pod] if args[1]=='pods' else [self.node] if args[1]=='nodes' else [self.warning]}
        telemetry,events=collect({'PROMETHEUS_URL':'offline'},self.state,read,None,
            transport=Mock(side_effect=TimeoutError()),clock=lambda:self.now)
        self.assertEqual(len(commands),3);self.assertTrue(all(c[0]=='get' for c in commands))
        self.assertEqual(telemetry['capacity']['value'][0]['status'],'PROVEN_SHORTAGE')
        self.assertTrue(any(e['alarm_type']=='InsufficientClusterCapacity' for e in events))
    def test_absent_scheduling_condition_does_not_clear(self):
        t=self.telemetry();self.assertFalse(self.capacity(t)[1])
    def test_incomplete_adapters_retain_continuation(self):
        api=Kubernetes(lambda args: {'items':[self.warning] if args[1]=='events' else [self.node],'metadata':{'continue':'next'}},lambda:self.now)
        self.assertTrue(api.infrastructure()['truncated'])
        self.assertTrue(api.warnings('n')['truncated'])

if __name__=='__main__':unittest.main()
