"""Offline assessor. Reads projected wire evidence separately from snapshot records.
No production journal, protocol operations, clock control or recovery actions.
"""
import json,uuid
from timing import clock_uncertainty
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
SCHEMA=json.loads((Path(__file__).parent/'event.schema.json').read_text())

def check_setup(samples,packets,markers,uncertainty_ns=0):
    requests=[p for p in packets if p['event']=='NG_SETUP_REQUEST']
    responses=[p for p in packets if p['event']=='NG_SETUP_RESPONSE']
    if not requests or not responses:return 'INCONCLUSIVE','MISSING_DECODED_SETUP'
    if len({p['association'] for p in requests+responses})!=1:return 'INCONCLUSIVE','MULTIPLE_ASSOCIATIONS_REQUIRE_RECONNECTION_PROTOCOL'
    latest=responses[-1];prior=[p for p in requests if p['association']==latest['association'] and p['wall_ns']<=latest['wall_ns'] and p['src_port']==latest['dst_port']]
    if not prior:return 'INCONCLUSIVE','UNBOUND_RESPONSE'
    if latest['amf_name']!='l3-d0-amf-01':return 'FAIL','AMF_IDENTITY'
    if not any(m['source']=='amf' and m['marker']=='AMF_INITIALIZED' for m in markers):return 'INCONCLUSIVE','AMF_STARTUP_UNVERIFIED'
    if not any(m['source']=='cu' and m['marker']=='NG_SETUP_ACCEPTED' for m in markers):return 'INCONCLUSIVE','CU_ACCEPTANCE_UNVERIFIED'
    valid=[s for s in samples if s['phase']=='OBSERVE' and s['valid'] and s['complete'] and s['application_state']=='UP' and s['observed_amf_name']=='l3-d0-amf-01' and s['producer_wall_ns']>=latest['wall_ns']+uncertainty_ns and s.get('generation',0)>0 and s.get('sequence',0)>0 and s.get('amf_index')==0 and s.get('peer_id')=='l3-d0-amf-01@n2-loopback' and s.get('local_nf_id')=='l3-d0-cucp-01' and 0<=s['observer_monotonic_ns']-s['producer_monotonic_ns']<=30_000_000_000]
    valid=[sample for sample in valid if not any(p['event'] in ('SCTP_DISCONNECT','NG_SETUP_FAILURE') and p['association']==latest['association'] and latest['wall_ns']<=p['wall_ns']<=sample['producer_wall_ns'] for p in packets)]
    if not valid:return 'INCONCLUSIVE','NO_FRESH_CORRELATED_UP'
    if len({s['process_incarnation'] for s in samples})!=1:return 'FAIL','PROCESS_IDENTITY_CHANGED'
    return 'PASS',None


def verdict(test_id,samples,packets,markers,synthetic=True,execution=None):
    if synthetic:return 'NOT_ASSESSED','SYNTHETIC_FIXTURE_NOT_RUNTIME_RESULT'
    if not samples:return 'INCONCLUSIVE','NO_SNAPSHOT_RECORDS'
    if execution is None or execution.get('status')!='DRIVER_COMPLETED_NOT_PROTOCOL_PASS' or execution.get('test_id')!=test_id or execution.get('timing',{}).get('uncertainty_ns') is None:return 'INCONCLUSIVE','EXECUTION_OR_CLOCK_EVIDENCE_UNVERIFIED'
    if test_id=='D0-T6':return 'INCONCLUSIVE','STALE_COMPLETION_HOOK_UNIMPLEMENTED'
    if test_id=='D0-T2':return check_setup(samples,packets,markers,execution['timing']['uncertainty_ns'])
    if test_id=='D0-T3':
        if any(s['application_state']=='UP' for s in samples):return 'FAIL','FALSE_UP_DURING_REJECTION'
        failed=[p for p in packets if p['event']=='NG_SETUP_FAILURE']
        if not failed or not any(p['event']=='NG_SETUP_REQUEST' and p['association']==failed[-1]['association'] and p['wall_ns']<=failed[-1]['wall_ns'] for p in packets):return 'INCONCLUSIVE','NO_CORRELATED_EXPLICIT_REJECTION'
        last=failed[-1]
        if not any(s['phase']=='OBSERVE' and s['valid'] and s['complete'] and s['application_state'] in ('DOWN','UNKNOWN') and s['producer_wall_ns']>=last['wall_ns']+execution['timing']['uncertainty_ns'] and 0<=s['observer_monotonic_ns']-s['producer_monotonic_ns']<=30_000_000_000 for s in samples):return 'INCONCLUSIVE','NO_VALID_REJECTION_OBSERVATION'
        return 'PASS',None
    if test_id=='D0-T1':
        if any(s['application_state']=='UP' for s in samples):return 'FAIL','FALSE_UP_WITHOUT_AMF'
        if not any(s['phase']=='OBSERVE' and s['valid'] and s['complete'] and s['application_state']=='UNKNOWN' for s in samples):return 'INCONCLUSIVE','NO_VALID_STARTUP_OBSERVATION'
        if execution.get('amf_started') is not False or not packets or any(p['event'].startswith('NG_SETUP_') for p in packets):return 'INCONCLUSIVE','AMF_ABSENCE_NOT_ESTABLISHED'
        return 'PASS',None
    if test_id=='D0-T7':
        after=[s for s in samples if s['phase']=='AFTER_STOP']
        if not after:return 'INCONCLUSIVE','NO_POST_STOP_RECORD'
        if any(s['valid'] or s['application_state']!='UNKNOWN' for s in after):return 'FAIL','SHUTDOWN_INVALIDATION'
        before=[s for s in samples if s['phase']=='OBSERVE' and s['valid'] and s['complete']]
        if not before or before[-1]['sequence']!=after[-1]['sequence'] or before[-1]['process_incarnation']!=after[-1]['process_incarnation']:return 'INCONCLUSIVE','NO_MATCHED_VALID_RETAINED_TICKET'
        if not any(m['source']=='cu' and m['marker']=='CU_STOPPED' for m in markers):return 'INCONCLUSIVE','MISSING_STOP_RECEIPT'
        return 'PASS',None
    # Loss/reconnect require separately authorized actions, association epochs and local-loss ordering.
    return 'INCONCLUSIVE','CONTROLLED_LIFECYCLE_ACTION_RECEIPTS_REQUIRED'

def normalize(sample,context,refs=()):
    """Normalize one bounded producer record into the reviewed generic evidence schema."""
    actual=sample['application_state'] if sample['valid'] and sample['complete'] else 'UNKNOWN'
    event={'schema_version':'l3-ngap-runtime-evidence-v1','synthetic':context['synthetic'],'experiment_id':context['experiment_id'],'test_id':context['test_id'],'event_id':str(uuid.uuid4()),'kind':'snapshot','provenance':context['provenance'],'observed_wall_ns':sample['observer_wall_ns'],'observed_monotonic_ns':sample['observer_monotonic_ns'],'timing_uncertainty_ns':context['timing_uncertainty_ns'],'uncertainty_reason':context['uncertainty_reason'],'process_incarnation':sample['process_incarnation'],'local_nf_id':sample['local_nf_id'],'amf_index':sample['amf_index'],'peer_id':sample['peer_id'],'expected_amf_name':sample['expected_amf_name'],'observed_amf_name':sample['observed_amf_name'] or None,'association_epoch':context.get('association_epoch'),'snapshot':{'valid':sample['valid'],'complete':sample['complete'],'application_state':actual,'reason':sample['reason'],'generation':sample['generation'],'sequence':sample['sequence'],'revision':sample['revision'],'producer_wall_ns':sample['producer_wall_ns'],'producer_monotonic_ns':sample['producer_monotonic_ns'],'transition_known':sample['transition_known'],'transition_wall_ns':sample['transition_wall_ns'],'transition_monotonic_ns':sample['transition_monotonic_ns']},'independent_protocol_event':None,'independent_evidence_refs':list(refs),'expected_states':context['expected_states'],'actual_state':actual,'classification':'NOT_ASSESSED','failure_or_limitation':'PROTOCOL_ASSESSMENT_PENDING','protocol_coverage':'MISSING'}
    Draft202012Validator(SCHEMA,format_checker=FormatChecker()).validate(event)
    return event
