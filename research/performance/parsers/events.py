"""Convert independently recorded event pairs and iperf3 JSON to evidence.

Never initiates traffic, deployment, rollback, recovery or fault injection.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
from ..schema import record,write

PAIRS={
 'manual_deployment':('operator_start','service_verified_ready'),
 'automated_deployment':('approved_request','service_verified_ready'),
 'package_onboarding':('publication_start','readback_verified'),
 'ns_instantiation':('instantiate_submitted','osm_completed'),
 'ns_termination':('terminate_submitted','osm_completed'),
 'scenario_switch':('switch_approved','replacement_service_verified_ready'),
 'service_readiness':('instantiate_submitted','service_verified_ready'),
 'ue_registration':('registration_request_tx','registration_accept_rx'),
 'pdu_establishment':('pdu_request_tx','pdu_accept_rx'),
 'failure_detection':('verified_fault_onset','alarm_firing'),
 'degradation_detection':('verified_degradation_onset','alarm_firing'),
 'decision':('alarm_firing','proposal_recorded'),
 'recovery':('recovery_approved','service_verified_restored'),
 'service_downtime':('last_verified_service_loss','first_verified_service_restoration'),
 'ci_validation':('validation_start','validation_end'),
 'package_build':('build_start','package_verified'),
 'release_verification':('verification_start','verification_end'),
 'catalog_publication':('publication_start','readback_verified')}

def seconds(value):
    if isinstance(value,str):
        d=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError('Timezone-qualified event timestamp required')
        return d.timestamp()
    if type(value) in (int,float) and math.isfinite(value):return value
    raise ValueError('Timestamp required')

def durations(bundle):
    if bundle.get('schema_version')!=1 or bundle.get('provenance') not in ('RECORDED_LIVE','SYNTHETIC_TEST'):raise ValueError('Explicit event provenance required')
    clock=bundle.get('clock',{})
    if clock.get('same_clock') is not True and not (clock.get('synchronized') is True and type(clock.get('max_error_seconds')) in (int,float) and 0<=clock['max_error_seconds']<=.1):raise ValueError('Clock alignment evidence required')
    events=bundle.get('events',[])
    if not isinstance(events,list) or len(events)>512:raise ValueError('Event budget')
    by_name={}
    for event in events:
        name=event['name']
        if name in by_name:raise ValueError('Ambiguous duplicate event boundary')
        if not event.get('evidence_ref') or not event.get('operation_id'):raise ValueError('Boundary evidence and correlation required')
        by_name[name]=event
    rows=[]
    for metric in bundle.get('metrics',[]):
        if metric=='manual_operator_actions':
            actions=bundle.get('operator_actions');value=None
            if actions is not None:
                if not isinstance(actions,list) or len(actions)>256 or not bundle.get('operator_action_observer_ref'):raise ValueError('Bounded independently observed actions required')
                ids=set()
                for action in actions:
                    if not action.get('evidence_ref') or not action.get('operation_id') or not action.get('id') or action['id'] in ids:raise ValueError('Unique correlated action evidence required')
                    seconds(action['timestamp']);ids.add(action['id'])
                value=len(actions)
            rows.append(record(metric=metric,unit='count',source='independently observed operator action journal',value=value,status='MEASURED' if value is not None else 'REQUIRES_CONTROLLED_TEST',at=seconds(bundle['observed_at']),scenario=bundle['scenario'],implementation=bundle['ran_implementation'],component=bundle['component'],experiment=bundle['experiment_id'],trial=bundle['trial_id'],version=bundle['software_version'],provenance=bundle['provenance'],freshness='LIVE' if value is not None else 'UNAVAILABLE',reason='' if value is not None else 'No operator action observation'))
            continue
        if metric not in PAIRS:raise ValueError('Unknown metric')
        first,last=PAIRS[metric];a=by_name.get(first);b=by_name.get(last);value=None;reason='Missing verified event boundaries'
        if a and b:
            if a['operation_id']!=b['operation_id']:raise ValueError('Operation correlation mismatch')
            value=seconds(b['timestamp'])-seconds(a['timestamp'])
            if value<0:raise ValueError('Negative duration or unsynchronized clocks')
            reason=''
        rows.append(record(metric=metric+'_duration',unit='seconds',source='event-boundaries:'+first+'->'+last,value=value,status='MEASURED' if value is not None else 'REQUIRES_CONTROLLED_TEST',at=seconds(b['timestamp']) if b else None,scenario=bundle['scenario'],implementation=bundle['ran_implementation'],component=bundle['component'],experiment=bundle['experiment_id'],trial=bundle['trial_id'],version=bundle['software_version'],provenance=bundle['provenance'],freshness='LIVE' if value is not None else 'UNAVAILABLE',reason=reason,labels={'clock_max_error_seconds':clock.get('max_error_seconds',0)}))
    return rows

def iperf(data,metadata):
    # Server received data defines goodput; never substitute sender rate for it.
    if data.get('error'):raise ValueError('Failed iperf trial; do not report partial success')
    end=data.get('end',{});protocol=data.get('start',{}).get('test_start',{}).get('protocol')
    reverse=data.get('start',{}).get('test_start',{}).get('reverse')
    if reverse not in (0,1,False,True) or protocol not in ('TCP','UDP'):raise ValueError('iperf protocol/direction required')
    row=end.get('sum_received') if protocol=='TCP' else end.get('sum')
    if not isinstance(row,dict) or row.get('sender') is not False:raise ValueError('Receiver summary required')
    metrics={'dl_goodput' if reverse else 'ul_goodput':('bits_per_second',row.get('bits_per_second'))}
    if protocol=='UDP':
        if row.get('packets',0)<=0:raise ValueError('No received/test packets; loss undefined')
        metrics.update(jitter=('milliseconds',row.get('jitter_ms')),packet_loss=('ratio',row.get('lost_packets',0)/row['packets'] if 'lost_packets' in row else None))
    result=[]
    for metric,(unit,value) in metrics.items():
        if type(value) not in (int,float) or not math.isfinite(value) or value<0 or metric=='packet_loss' and value>1:raise ValueError('Invalid or missing receiver summary')
        result.append(record(metric=metric,unit=unit,value=value,status='MEASURED',source='iperf3 receiver JSON',freshness='LIVE',**metadata))
    return result

def ping(text,metadata):
    import re
    m=re.search(r'(\d+) packets transmitted, (\d+) (?:packets )?received',text)
    timing=re.search(r'(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms',text)
    if not m or int(m[1])<=0 or not 0<=int(m[2])<=int(m[1]):raise ValueError('Packet summary required')
    output=[record(metric='packet_loss',unit='ratio',value=(int(m[1])-int(m[2]))/int(m[1]),status='MEASURED',source='recorded ping receiver summary',freshness='LIVE',**metadata)]
    if timing and int(m[2])>0:
        for label,value in zip(('rtt_minimum','rtt_average','rtt_maximum','rtt_reported_mdev'),timing.groups()):
            output.append(record(metric=label,unit='milliseconds',value=float(value),status='MEASURED',source='recorded ping summary',freshness='LIVE',**metadata))
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('events');p.add_argument('--output',required=True);a=p.parse_args()
    path=Path(a.events)
    if path.stat().st_size>1024*1024:raise SystemExit('Event evidence budget')
    bundle=json.loads(path.read_text());rows=durations(bundle);write(a.output,rows,{'collection_kind':'RECORDED_EVENT_IMPORT','execution_calls':0})
if __name__=='__main__':main()
