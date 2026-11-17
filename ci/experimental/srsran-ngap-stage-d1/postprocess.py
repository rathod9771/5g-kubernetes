"""Offline-only genuine-run assessment, requiring separately captured independent evidence."""
import sys,json,hashlib,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from runtime_launcher import private_file
from assess import verdict,normalize,SCHEMA
from jsonschema import Draft202012Validator,FormatChecker
P=Path(__file__).parent

def process(folder):
    uid=os.getuid();raw=private_file(folder/'evidence-sha256.json',uid)
    hashes=json.loads(raw)
    required={'execution.json','snapshots.jsonl','protocol-events.json','log-markers.json','preflight.json','approval-consumed.json','n2.pcap'}
    if set(hashes)!=required:raise ValueError('EVIDENCE_INVENTORY')
    for name,h in hashes.items():
        if hashlib.sha256(private_file(folder/name,uid,8*1024**2)).hexdigest()!=h:raise ValueError('EVIDENCE_HASH_MISMATCH')
    def load(name):return json.loads(private_file(folder/name,uid,8*1024**2))
    execution=load('execution.json');provenance=execution['provenance']
    if execution.get('synthetic') is not False:raise ValueError('NOT_GENUINE_RUNTIME_EVIDENCE')
    if provenance['package_sha256']!=hashlib.sha256((P/'SHA256SUMS').read_bytes()).hexdigest() or provenance['source_manifest_sha256']!=hashlib.sha256((P/'source-manifest.json').read_bytes()).hexdigest():raise ValueError('PACKAGE_PROVENANCE_MISMATCH')
    if provenance['srsran_revision']!='11c9bbabb69873752500d676f55e0034f6caa5c5' or provenance['open5gs_revision']!='7dfd9a39649700c24c22f1978ed7a35541a72cca':raise ValueError('SOURCE_PROVENANCE_MISMATCH')
    samples=[json.loads(l) for l in private_file(folder/'snapshots.jsonl',uid,8*1024**2).splitlines()]
    packets=load('protocol-events.json');logs=load('log-markers.json')
    if not samples or len(samples)>5000 or len(packets)>2048 or len(logs)>5000:raise ValueError('EVIDENCE_COUNT')
    test=execution['test_id'];result,limitation=verdict(test,samples,packets,logs,False,execution)
    source=json.loads((P/'source-manifest.json').read_text());config='amf-reject.yaml' if test=='D0-T3' else 'amf.yaml'
    config_hash=hashlib.sha256((P/config).read_bytes()+(P/'nrf.yaml').read_bytes()+(P/'runtime_driver.cpp').read_bytes()).hexdigest()
    context={'synthetic':False,'experiment_id':execution['experiment_id'],'test_id':test,'provenance':{'srsran_revision':provenance['srsran_revision'],'instrumentation_patch_sha256':source['original_instrumentation_patch_sha256'],'experiment_patch_sha256':hashlib.sha256((P/'activation.patch').read_bytes()).hexdigest(),'cu_binary_sha256':provenance['binaries']['cu']['sha256'],'amf_binary_sha256':provenance['binaries']['amf']['sha256'],'amf_source_revision':provenance['open5gs_revision'],'config_sha256':config_hash,'network_namespace_inode':str(execution['runtime_netns']),'boot_id':provenance['boot_id']},'timing_uncertainty_ns':execution['timing']['uncertainty_ns'],'uncertainty_reason':'Empirical same-kernel consistency only; absolute synchronization unverified','expected_states':['UP'] if test=='D0-T2' else ['DOWN','UNKNOWN'] if test=='D0-T3' else ['UNKNOWN'],'association_epoch':None}
    # Normalize every producer record as NOT_ASSESSED; aggregate PASS is never a per-record health claim.
    normalized=[normalize(s,context) for s in samples]
    refs=['protocol-events.json#frame='+str(p['frame']) for p in packets if p['event'].startswith('NG_SETUP_')]
    if not refs:refs=['approval-consumed.json','execution.json']
    selected=next((x for x in reversed(normalized) if x['actual_state']=='UP' and x['snapshot']['valid']),normalized[-1]) if test=='D0-T2' else normalized[-1]
    final=dict(selected,kind='assessment',classification=result,failure_or_limitation=limitation,independent_evidence_refs=refs[:16])
    associations={p['association'] for p in packets if p['event'].startswith('NG_SETUP_')}
    if len(associations)==1:final['association_epoch']=execution['experiment_id']+':'+next(iter(associations))
    if result=='PASS':
        final['protocol_coverage']='APPLICATION_CONFIRMED' if test in ('D0-T2','D0-T3') else 'TRANSPORT_ONLY'
        final['independent_protocol_event']='NG_SETUP_RESPONSE' if test=='D0-T2' else 'NG_SETUP_FAILURE' if test=='D0-T3' else 'SOURCE_UNAVAILABLE'
    Draft202012Validator(SCHEMA,format_checker=FormatChecker()).validate(final)
    return {'classification':result,'limitation':limitation,'producer_records':len(samples),'wire_records':len(packets),'record_count':len(normalized),'assessment':final}

def main():
    if len(sys.argv)!=3 or sys.argv[1]!='--assess-genuine-private-run':raise SystemExit('Offline assessment requires an explicit private genuine-run directory')
    # Standard output is a sanitized summary only; write detailed exports separately after review.
    result=process(Path(sys.argv[2]));print(json.dumps(result,indent=2))
if __name__=='__main__':
    try:main()
    except Exception as e:raise SystemExit('ASSESSMENT_BLOCKED: '+type(e).__name__)
