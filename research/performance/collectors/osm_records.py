"""Existing allowlisted OSM reader only. No lifecycle mutations or catalog writes."""
import argparse
import json
from pathlib import Path
import sys
from ..schema import record,write
from ..parsers.events import seconds

def collect(snapshot,root):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
    from runtime_config import load_config,private_text
    from assurance.osm import ReadOnlyOSM
    import yaml
    cfg=load_config(root=Path(root),environ={'P0_RUNTIME_CONFIG':str(snapshot),'HOME':str(Path.home())},require=('osm',))
    text,_=private_text(cfg['ACTIVE_STATE_PATH'],required=True,strict=True);state=yaml.safe_load(text)
    reader=ReadOnlyOSM(cfg,state);rows=[];states={}
    for key in ('active_instance_id','core_instance_id'):
        ident=state.get('osm',{}).get(key)
        if ident:
            obj=reader.get_ns_instance(ident)
            states[key]={k:obj.get(k) for k in ('nsState','operational-status','config-status','currentOperation')}
    operation=state.get('osm',{}).get('active_operation_id')
    if operation:
        obj=reader.get_operation(operation);kind=obj.get('lcmOperationType');status=obj.get('operationState')
        if kind in ('INSTANTIATE','TERMINATE') and status in ('COMPLETED','FAILED') and 'startTime' in obj and 'statusEnteredTime' in obj:
            start,end=seconds(obj['startTime']),seconds(obj['statusEnteredTime'])
            if end<start:raise ValueError('OSM clock/order mismatch')
            rows.append(record(metric='osm_'+kind.lower()+'_operation_duration',unit='seconds',source='OSM NBI startTime->terminal statusEnteredTime',value=end-start,status='MEASURED',at=end,scenario=state.get('active','UNKNOWN'),component='osm',experiment='C-historical-operation',trial=operation,version='OSM v19; configured installation',provenance='RECORDED_LIVE',freshness='LIVE',labels={'operation_id':operation,'operation_state':status}))
    return rows,{'ns_states':states,'reader_requests':reader.stats,'lifecycle_calls':0,'scope':'bound active/core NS and recorded active operation only; no history completeness claim'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',required=True);p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    try:rows,meta=collect(a.snapshot,a.root)
    except Exception as e:
        code='CONFIGURATION_OR_READER_UNAVAILABLE'
        for text,name in [('configuration snapshot contract','SNAPSHOT_SCHEMA_MISMATCH'),('Default','DEFAULT_CREDENTIALS_REJECTED'),('default','DEFAULT_CREDENTIALS_REJECTED')]:
            if text in str(e):code=name;break
        rows=[];meta={'status':'UNAVAILABLE','error_class':type(e).__name__,'error_code':code,'details':'SUPPRESSED','lifecycle_calls':0}
    write(a.output,rows,meta);print(json.dumps(meta,indent=2))
if __name__=='__main__':main()
