import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from research.performance.schema import record,validate,write,load
from research.performance.benchmarks.summarize import summarize,percentile
from research.performance.benchmarks.plot import candidates
from research.performance.benchmarks.reliability import confusion,success_rate
from research.performance.collectors.passive import collect,quantity
from research.performance.parsers.events import durations,iperf,ping

class SchemaTests(unittest.TestCase):
    def sample(self,value=1,**kwargs):return record(metric='test',unit='seconds',source='fixture',value=value,status='MEASURED',freshness='LIVE',**kwargs)
    def test_json_csv_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'trial';rows=[self.sample(),record(metric='unknown',unit='seconds',source='fixture')];write(path,rows,{})
            for suffix in ('json','csv'):self.assertEqual(len(load(path/('measurements.'+suffix))),2)
            with self.assertRaises(FileExistsError):write(path,rows,{})
    def test_result_digest_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'trial';write(path,[self.sample()],{})
            target=path/'measurements.json';target.write_text(target.read_text().replace('"value": 1','"value": 2'))
            with self.assertRaises(ValueError):load(target)
    def test_null_not_zero_and_nonfinite(self):
        with self.assertRaises(ValueError):record(metric='missing',unit='seconds',source='fixture',value=0)
        for v in (True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.sample(v)
    def test_synthetic_and_unavailable_excluded_from_research_summary(self):
        self.assertEqual(summarize([self.sample(provenance='SYNTHETIC_TEST')]),[])
        self.assertEqual(candidates([self.sample()], 'test'),[])
    def test_independent_trials_not_samples_define_n(self):
        rows=[self.sample(i,at=100+i,trial='same') for i in range(10)]
        result=summarize(rows)[0];self.assertEqual(result['sample_count'],1);self.assertIsNone(result['standard_deviation']);self.assertIsNone(result['confidence_interval_95'])
    def test_statistics_five_real_trials_and_dedup(self):
        rows=[self.sample(i,trial=str(i),at=100+i) for i in range(1,6)];result=summarize(rows+rows)[0]
        self.assertEqual(result['sample_count'],5);self.assertEqual(result['mean'],3);self.assertEqual(result['median'],3);self.assertAlmostEqual(result['standard_deviation'],1.5811388300841898);self.assertAlmostEqual(result['p95'],4.8);self.assertIsNotNone(result['confidence_interval_95'])
    def test_target_dimensions_not_silently_pooled(self):
        a=self.sample(labels={'job':'scheduler'});b=self.sample(labels={'job':'kubelet'})
        self.assertEqual(len(summarize([a,b])),2)
    def test_source_versions_and_units_not_mixed(self):
        rows=[self.sample(version='a'),self.sample(version='b')];self.assertEqual(len(summarize(rows)),2)
    def test_resource_units(self):
        self.assertEqual(quantity('500m',True),.5);self.assertEqual(quantity('1Gi'),1073741824);self.assertIsNone(quantity(None))

class EventTests(unittest.TestCase):
    def bundle(self):return {'schema_version':1,'scenario':'fixture','ran_implementation':'oai','component':'cu','experiment_id':'A','trial_id':'1','software_version':'fixture','provenance':'SYNTHETIC_TEST','clock':{'same_clock':True},'metrics':['manual_deployment'],'events':[{'name':'operator_start','timestamp':100,'operation_id':'one','evidence_ref':'test-fixture'},{'name':'service_verified_ready','timestamp':112,'operation_id':'one','evidence_ref':'test-fixture'}]}
    def test_recorded_boundaries(self):self.assertEqual(durations(self.bundle())[0]['value'],12)
    def test_missing_end_is_not_zero(self):
        b=self.bundle();b['events'].pop();r=durations(b)[0];self.assertIsNone(r['value']);self.assertEqual(r['measurement_status'],'REQUIRES_CONTROLLED_TEST')
    def test_duplicate_negative_mismatched_clock_and_operation(self):
        for change in ('duplicate','negative','clock','operation'):
            b=self.bundle()
            if change=='duplicate':b['events'].append(b['events'][0])
            if change=='negative':b['events'][1]['timestamp']=99
            if change=='clock':b['clock']={}
            if change=='operation':b['events'][1]['operation_id']='different'
            with self.assertRaises(ValueError):durations(b)
    def test_traffic_parsers_never_initiate_processes(self):
        meta={'provenance':'SYNTHETIC_TEST','trial':'fixture'}
        payload={'start':{'test_start':{'protocol':'TCP','reverse':1}},'end':{'sum_received':{'sender':False,'bits_per_second':1000}}}
        with patch('subprocess.Popen',side_effect=AssertionError('traffic execution forbidden')):
            self.assertEqual(iperf(payload,meta)[0]['metric'],'dl_goodput')
            result=ping('10 packets transmitted, 9 received, 10% packet loss\nrtt min/avg/max/mdev = 1/2/3/0.5 ms',meta)
            self.assertEqual(result[0]['value'],.1);self.assertEqual(result[2]['value'],2)
    def test_udp_receiver_and_failed_trial(self):
        p={'start':{'test_start':{'protocol':'UDP','reverse':0}},'end':{'sum':{'sender':False,'bits_per_second':100,'packets':10,'lost_packets':2,'jitter_ms':1.2}}}
        result=iperf(p,{'provenance':'SYNTHETIC_TEST'});self.assertEqual(result[-1]['value'],.2)
        p['error']='failure'
        with self.assertRaises(ValueError):iperf(p,{'provenance':'SYNTHETIC_TEST'})
    def test_unknown_outcome_not_counted_as_success_or_failure(self):
        rows=[{'trial_id':'one','provenance':'RECORDED_LIVE','evidence_ref':'verified-ref','outcome':'UNKNOWN'}]
        r=success_rate(rows);self.assertIsNone(r['success_rate']);self.assertEqual(r['UNKNOWN'],1)
        self.assertEqual(r['success_fraction_lower_bound'],0);self.assertEqual(r['success_fraction_upper_bound'],1)
    def test_manual_actions_require_observation_and_unique_events(self):
        b=self.bundle();b['metrics']=['manual_operator_actions'];b['observed_at']=112
        self.assertIsNone(durations(b)[0]['value'])
        b['operator_actions']=[]
        with self.assertRaises(ValueError):durations(b)
        b['operator_action_observer_ref']='fixture-ref'
        self.assertEqual(durations(b)[0]['value'],0)
    def test_false_positive_denominator_and_missing_ground_truth(self):
        self.assertIsNone(confusion([])['false_positive_rate'])
        rows=[{'provenance':'RECORDED_LIVE','ground_truth_ref':'fixture-ref','experiment_id':'F','trial_id':'one','condition_present':False,'alarm_fired':True}]
        self.assertEqual(confusion(rows)['false_positive_rate'],1)
        with self.assertRaises(ValueError):confusion(rows+rows)
        rows[0]['provenance']='SYNTHETIC_TEST'
        with self.assertRaises(ValueError):confusion(rows)

class CollectionTests(unittest.TestCase):
    def test_prometheus_and_kubernetes_outage_fail_explicitly(self):
        class K:
            namespace='test';requests=0
            def pods(self):self.requests+=1;raise OSError('private')
        class H:
            requests=0
            def query(self,*a):self.requests+=1;raise OSError('private')
            def get(self,*a):self.requests+=1;raise OSError('private')
        rows,meta=collect(K(),H(),clock=lambda:100)
        self.assertTrue(meta['failures']);self.assertFalse(any(r['measurement_status']=='MEASURED' for r in rows));self.assertNotIn('private',json.dumps(meta))
    def test_missing_stale_and_duplicate_sidecar_data(self):
        class K:
            namespace='test';requests=0
            def pods(self):
                self.requests+=1
                return {'items':[{'metadata':{'name':'cu-pod','uid':'uid','labels':{'component':'cu','app':'fran-oai-cu'}},'spec':{'containers':[{'name':'cu','image':'oai-gnb:fixture'}]},'status':{'containerStatuses':[{'name':'cu','ready':True,'restartCount':0}]}}]}
        class H:
            requests=0
            def query(self,q,at):
                self.requests+=1
                return [{'metric':{'pod':'cu-pod','container':'cu'},'value':[at,'1' if 'timestamp(' in q else 'nan']}]
            def get(self,*a):self.requests+=1;return {'data':{'activeTargets':[]}}
        rows,meta=collect(K(),H(),clock=lambda:1000)
        cpu=next(r for r in rows if r['metric']=='cpu_usage');self.assertIsNone(cpu['value']);self.assertEqual(cpu['freshness'],'STALE')
        ready=next(r for r in rows if r['metric']=='container_ready');self.assertEqual(ready['value'],1)
        pod_ready=next(r for r in rows if r['metric']=='pod_ready');self.assertIsNone(pod_ready['value']) # Pod Ready is not inferred from container Ready.

if __name__=='__main__':unittest.main()

class RadioTests(unittest.TestCase):
    def test_legacy_samples_without_health_or_timestamp_are_unavailable(self):
        from research.performance.collectors.radio import collect as radio
        class K:
            namespace='test';requests=0
            def pods(self):self.requests+=1;return {'items':[]}
        class H:
            requests=0
            def query(self,q,at):
                self.requests+=1
                if q.startswith('{'):return [{'metric':{'__name__':'oai_gnb_ue_ul_snr_db','pod_uid':'uid','container':'du'},'value':[at,'12']}]
                return []
        rows,_=radio(H(),K(),'fixture',clock=lambda:100)
        self.assertEqual(rows[0]['measurement_status'],'UNAVAILABLE');self.assertIsNone(rows[0]['value'])

class QualifiedRadioTests(unittest.TestCase):
    def test_fresh_owned_sample_requires_health_and_source_timestamp(self):
        from research.performance.collectors.radio import collect as radio
        class K:
            namespace='test';requests=0
            def pods(self):
                self.requests+=1
                return {'items':[{'metadata':{'name':'fran-oai-du-fixture','uid':'uid','labels':{'app':'fran-oai-du','component':'du'}},'spec':{'containers':[{'name':'du','image':'oai-gnb:fixture'}]}}]}
        class H:
            requests=0
            healthy=1
            at=99
            def query(self,q,at):
                self.requests+=1
                labels={'pod_uid':'uid','container':'du','rnti':'a001','cell':'unknown','component':'du'}
                if q.startswith('{'):return [{'metric':dict(labels,__name__='oai_gnb_ue_ul_snr_db'),'value':[at,'12.5']}]
                if 'timestamp' in q:return [{'metric':dict(labels,metric='oai_gnb_ue_ul_snr_db'),'value':[at,str(self.at)]}]
                return [{'metric':labels,'value':[at,str(self.healthy)]}]
        h=H();rows,_=radio(h,K(),'fixture',clock=lambda:100)
        self.assertEqual(rows[0]['measurement_status'],'MEASURED');self.assertEqual(rows[0]['value'],12.5)
        h=H();h.healthy=0;rows,_=radio(h,K(),'fixture',clock=lambda:100)
        self.assertIsNone(rows[0]['value'])
        h=H();h.at=1;rows,_=radio(h,K(),'fixture',clock=lambda:1000)
        self.assertIsNone(rows[0]['value']);self.assertEqual(rows[0]['freshness'],'STALE')
