import datetime as dt
import importlib.util
import json
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('performance_ran_exporter',ROOT/'monitoring/ran-exporter/ran_exporter.py')
exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
NOW=1792648800

def pod(uid='one',app='fran-oai-du',image='oaisoftwarealliance/oai-gnb:2026.w25',role='du'):
    return {'metadata':{'namespace':'test','uid':uid,'name':app+'-fixture','labels':{'app':app,'component':role,'ran-type':'fran'}},'spec':{'containers':[{'name':role,'image':image}]},'status':{'phase':'Running'}}

def line(body,at=NOW):return dt.datetime.fromtimestamp(at,dt.timezone.utc).isoformat().replace('+00:00','Z')+' '+body

class API:
    def __init__(self):self.pods=[pod()];self.text=line('UE a001: ulsch_rounds 100/4/2/1, ulsch_errors 0, BLER 0.04 SNR -1.25 dB');self.fail_discovery=False;self.fail_logs=False
    def discover(self,_):
        if self.fail_discovery:raise OSError('private token must not reach metrics')
        return self.pods
    def logs(self,_):
        if self.fail_logs:raise OSError('secret')
        return self.text

class ParserTests(unittest.TestCase):
    def test_ul_snr_and_independent_bler_order(self):
        for body in ('UE a001: ulsch_rounds 100/4/2/1, ulsch_errors 2 BLER 0.04 SNR -1.25 dB','UE a001: ulsch_rounds 100/4/2/1, ulsch_errors 2 SNR -1.25 BLER 0.04'):
            values=exporter.parse_oai(body)[0][2]
            self.assertEqual(values['oai_gnb_ue_ul_snr_db'],-1.25);self.assertEqual(values['oai_gnb_ue_ul_bler_ratio'],.04)
            self.assertEqual(values['oai_gnb_ue_ulsch_harq_round1_total'],4)
    def test_dl_snr_and_counters(self):
        values=exporter.parse_oai('UE 1234: dlsch_rounds 99/3/2/1, dlsch_errors 0, SNR 12.5 BLER 0.03')[0][2]
        self.assertEqual(values['oai_gnb_ue_dl_snr_db'],12.5);self.assertEqual(values['oai_gnb_ue_dlsch_harq_round3_total'],1)
    def test_rsrp_decimal_and_mac(self):
        self.assertEqual(exporter.parse_oai('UE RNTI a001 average RSRP -91.75 dBm')[0][2]['oai_gnb_ue_rsrp_dbm'],-91.75)
        self.assertEqual(exporter.parse_oai('UE a001: MAC: TX 120 RX 34 bytes')[0][2]['oai_gnb_ue_mac_rx_bytes_total'],34)
    def test_no_fabrication_or_composite_snr_truncation(self):
        self.assertEqual(exporter.parse_oai('F1 association up'),[])
        values=exporter.parse_oai('UE a001: dlsch_rounds 10/1/0/0 SNR 12.0+3.0 BLER 12.0')[0][2]
        self.assertNotIn('oai_gnb_ue_dl_snr_db',values);self.assertNotIn('oai_gnb_ue_dl_bler_ratio',values)
    def test_srs_json_units_and_no_oai_reuse(self):
        body=json.dumps({'cells':[{'ue_list':[{'rnti':40961,'pci':101,'pusch_snr_db':12,'dl_brate':100,'ul_brate':50,'pusch_rsrp_db':-20}]}]})
        sample=exporter.parse_srsran(body)[0]
        self.assertEqual(sample[0],'a001');self.assertEqual(sample[1],'101');self.assertEqual(sample[2]['srsran_ue_pusch_snr_db'],12)
        self.assertFalse(any('rsrp' in key or key.startswith('oai') for key in sample[2]))
        self.assertEqual(exporter.parse_srsran('UE a001: MAC: TX 10 RX 10 bytes'),[])
    def test_bad_json_and_nonfinite(self):
        with self.assertRaises(ValueError):exporter.parse_srsran('{bad')
        with self.assertRaises(ValueError):exporter.finite(float('inf'))
        self.assertEqual(exporter.parse_oai('x'*16385),[])
    def test_classification_split_mono_and_frandiscovery(self):
        for app,role in [('fran-oai-du','du'),('fran-oai-cu','cu'),('cloud-ran-oai-gnb','gnb')]:
            self.assertEqual(exporter.classify(pod(app=app,role=role))[0]['component'],role)
        self.assertEqual(exporter.classify(pod(image='python:3.12-slim')),[])
        self.assertEqual(exporter.classify(pod(image='srsran:25.04',app='cran-srsran-du'))[0]['implementation'],'srsran')

class ObserverTests(unittest.TestCase):
    def setUp(self):self.api=API();self.now=NOW;self.observer=exporter.Observer(self.api,'test',clock=lambda:self.now)
    def test_genuine_zero_vs_missing(self):
        self.observer.cycle();text=self.observer.render().decode()
        self.assertIn('oai_gnb_ue_ulsch_errors_total',text);self.assertNotIn('oai_gnb_ue_dl_snr_db',text)
        self.assertIn('pod_uid="one"',text);self.assertIn('scenario="fran"',text)
    def test_discovery_failure_recovers_without_thread_death(self):
        self.api.fail_discovery=True;self.assertFalse(self.observer.cycle());self.assertEqual(self.observer.discovery_failures,1)
        self.api.fail_discovery=False;self.assertTrue(self.observer.cycle());self.assertTrue(self.observer.values)
    def test_source_health_not_radio_health(self):
        self.api.text=line('CU F1 setup diagnostic');self.observer.cycle();text=self.observer.render().decode()
        self.assertIn('ran_exporter_source_collection_success',text);self.assertIn('ran_exporter_source_radio_statistics_fresh',text)
        self.assertFalse(self.observer.values);self.assertEqual(self.observer.sources[('one','du')]['parsed'],0)
    def test_duplicates_do_not_count_or_refresh(self):
        self.observer.cycle();first=self.observer.sources[('one','du')]['last_parse'];self.now+=10;self.observer.cycle()
        self.assertEqual(self.observer.oai_parsed,1);self.assertEqual(self.observer.sources[('one','du')]['last_parse'],first)
    def test_stale_per_metric_eviction_even_on_api_failure(self):
        self.observer.cycle();self.now+=121;self.api.fail_discovery=True;self.observer.cycle()
        text=self.observer.render().decode();self.assertNotIn('oai_gnb_ue_ul_snr_db',text);self.assertFalse(self.observer.values)
    def test_pod_replacement_removes_old_uid_and_metrics(self):
        self.observer.cycle();self.api.pods=[pod(uid='two')];self.api.text=line('new pod no radio');self.observer.cycle()
        self.assertNotIn(('one','du'),self.observer.sources);self.assertFalse(self.observer.values);self.assertEqual(self.observer.oai_parsed,1)
    def test_ue_disconnect_expires_even_other_ue_active(self):
        self.observer.cycle();self.now+=121;self.api.text=line('UE a002: MAC: TX 1 RX 2 bytes',self.now);self.observer.cycle()
        self.assertTrue(all(k[2]=='a002' for k in self.observer.values))
    def test_submicrosecond_rounding_does_not_drop_fresh_values(self):
        self.now=1000.989999999999
        self.api.text=line('UE a001: MAC: TX 1 RX 2 bytes',1000.99)
        self.observer.cycle();self.observer.render();self.assertTrue(self.observer.values)
    def test_log_failures_sanitized(self):
        self.api.fail_logs=True;self.assertFalse(self.observer.cycle());text=self.observer.render().decode()
        self.assertNotIn('secret',text);self.assertEqual(self.observer.errors['log_read'],1)
    def test_unknown_timestamp_does_not_become_fresh(self):
        self.api.text='UE a001: MAC: TX 1 RX 2 bytes';self.observer.cycle();self.assertFalse(self.observer.values)
        self.assertEqual(self.observer.errors['timestamp'],1)
    def test_future_and_old_samples_rejected(self):
        for at in (NOW+60,NOW-121):
            self.api.text=line('UE a001: MAC: TX 1 RX 2 bytes',at);self.observer.cycle();self.assertFalse(self.observer.values)
    def test_multiple_sources_and_budget(self):
        self.api.pods=[pod(),pod(uid='two',app='hcran-oai-small')];self.observer.cycle();self.assertEqual(len(self.observer.sources),2)
        bounded=exporter.Observer(self.api,'test',max_sources=1,clock=lambda:self.now)
        self.assertFalse(bounded.cycle());self.assertFalse(bounded.values)
    def test_series_budget(self):
        self.observer.max_series=1;self.observer.cycle();self.assertEqual(len(self.observer.values),1);self.assertGreater(self.observer.errors['series_budget'],0)
    def test_normal_shutdown_wakes_backoff(self):
        self.api.fail_discovery=True;thread=threading.Thread(target=self.observer.run);thread.start();self.observer.stop.set();thread.join(timeout=1);self.assertFalse(thread.is_alive())
    def test_saturated_log_window_reported(self):
        self.api.text='\n'.join(line('unparsed '+str(i)) for i in range(200));self.observer.cycle();self.assertEqual(self.observer.sources[('one','du')]['saturated'],1)

if __name__=='__main__':unittest.main()
