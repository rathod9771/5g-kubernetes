"""Future one-shot radio collection: requires per-source health and source time.

Does not activate UEs, change logs, enable metrics or deploy the exporter.
Legacy values without trustworthy freshness/collection gates are unavailable.
"""
import argparse
import math
import time
from .passive import HTTP,Kubernetes,component,expected_apps
from ..schema import record,write

UNITS={'oai_gnb_ue_rsrp_dbm':'dBm','oai_gnb_ue_dl_snr_db':'dB','oai_gnb_ue_ul_snr_db':'dB','oai_gnb_ue_dl_bler_ratio':'ratio','oai_gnb_ue_ul_bler_ratio':'ratio','oai_gnb_ue_mac_tx_bytes_total':'bytes','oai_gnb_ue_mac_rx_bytes_total':'bytes','srsran_ue_pusch_snr_db':'dB','srsran_ue_mac_dl_bitrate_kbps':'kbps','srsran_ue_mac_ul_bitrate_kbps':'kbps'}
for channel in ('dlsch','ulsch'):
    UNITS['oai_gnb_ue_'+channel+'_errors_total']='count'
    for index in range(1,4):UNITS[f'oai_gnb_ue_{channel}_harq_round{index}_total']='count'

def collect(http,kube,trial,scenario='fran-oai',implementation='oai',clock=time.time):
    at=clock();failures=[];rows=[];inventory={}
    expected=expected_apps(scenario,implementation)
    try:inventory={p['metadata']['uid']:p for p in kube.pods()['items'] if p['metadata'].get('labels',{}).get('app') in expected}
    except Exception as error:failures.append(type(error).__name__)
    def query(expression):
        try:return http.query(expression,at)
        except Exception as error:failures.append(type(error).__name__);return []
    namespace=kube.namespace
    import json
    label='namespace='+json.dumps(namespace)
    data=query('{__name__=~"oai_gnb_ue_.*|srsran_ue_.*",'+label+'}')
    timestamps=query('ran_exporter_measurement_timestamp_seconds{'+label+'}')
    health=query('ran_exporter_source_collection_success{'+label+'}')
    key=lambda x:(x.get('pod_uid'),x.get('container'),x.get('rnti'),x.get('cell'))
    stamps={key(x['metric'])+(x['metric'].get('metric'),):float(x['value'][1]) for x in timestamps}
    healthy={(x['metric'].get('pod_uid'),x['metric'].get('container')):float(x['value'][1])==1 for x in health}
    for sample in data:
        labels=sample['metric'];metric=labels.get('__name__')
        if metric not in UNITS:continue
        pod=inventory.get(labels.get('pod_uid'));container=labels.get('container');stamp=stamps.get(key(labels)+(metric,))
        version=next((c['image'] for c in pod['spec']['containers'] if c['name']==container),'UNKNOWN') if pod else 'UNKNOWN'
        value=float(sample['value'][1]);fresh=stamp is not None and math.isfinite(stamp) and -5<=at-stamp<=120
        valid=bool(pod and component(pod) in ('cu','du','gnb') and healthy.get((labels.get('pod_uid'),container)) and fresh and math.isfinite(value))
        if UNITS[metric]=='ratio':valid=valid and 0<=value<=1
        if UNITS[metric] in ('count','bytes','kbps'):valid=valid and value>=0
        rows.append(record(metric=metric,unit=UNITS[metric],source='prometheus:health+per-metric-original-timestamp',value=value if valid else None,status='MEASURED' if valid else 'UNAVAILABLE',at=stamp if fresh else at,scenario=scenario,implementation=implementation,component=labels.get('component','radio'),experiment='radio-passive-observation',trial=trial,version=version,freshness='LIVE' if valid else 'STALE' if stamp and not fresh else 'UNAVAILABLE',interval=15,reason='' if valid else 'Missing health/ownership/source freshness; no success inference',labels={k:labels.get(k,'UNKNOWN') for k in ('pod','pod_uid','container','rnti','cell')}))
    if not rows:rows=[record(metric='radio_statistics',unit='not_available',source='prometheus:qualified-radio-series',status='REQUIRES_ACTIVE_UE',scenario=scenario,implementation=implementation,experiment='radio-passive-observation',trial=trial,reason='No qualified series; approved exporter/image-format and UE evidence also required')]
    return rows,{'http_requests':http.requests,'kubernetes_list_requests':kube.requests,'failures':failures,'lifecycle_calls':0,'ue_activations':0}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prometheus-url',required=True);p.add_argument('--kubeconfig',required=True);p.add_argument('--namespace',required=True);p.add_argument('--trial-id',required=True);p.add_argument('--scenario',default='fran-oai');p.add_argument('--implementation',default='oai');p.add_argument('--output',required=True);a=p.parse_args()
    rows,meta=collect(HTTP(a.prometheus_url),Kubernetes(a.kubeconfig,a.namespace),a.trial_id,a.scenario,a.implementation);write(a.output,rows,meta)
if __name__=='__main__':main()
