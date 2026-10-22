"""Strict evidence records shared by collectors, importers and summaries."""
import hashlib
import csv
import datetime as dt
import json
import math
from pathlib import Path

STATUSES=('MEASURED','AVAILABLE_BUT_NOT_COLLECTED','REQUIRES_ACTIVE_UE','REQUIRES_CONTROLLED_TEST','UNAVAILABLE')
FIELDS=('timestamp','scenario','ran_implementation','component','metric','value','unit','source','experiment_id','trial_id','software_version','measurement_status','provenance','freshness','sampling_interval_seconds','collection_window_seconds','reason','labels')

def utc(value=None):
    return dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat().replace('+00:00','Z') if value is not None else dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')

def record(*,metric,unit,source,value=None,status='UNAVAILABLE',at=None,scenario='fran-oai',implementation='oai',component='platform',experiment='audit',trial='snapshot',version='UNKNOWN',provenance='LIVE',freshness='UNAVAILABLE',interval=None,window=None,reason='',labels=None):
    result=dict(zip(FIELDS,(utc(at),scenario,implementation,component,metric,value,unit,source,experiment,trial,version,status,provenance,freshness,interval,window,reason,labels or {})))
    validate(result);return result

def validate(row):
    if set(row)!=set(FIELDS):raise ValueError('Exact version-1 record fields required')
    if row['measurement_status'] not in STATUSES or row['provenance'] not in ('LIVE','RECORDED_LIVE','SYNTHETIC_TEST') or row['freshness'] not in ('LIVE','STALE','UNAVAILABLE'):raise ValueError('Evidence classification')
    stamp=dt.datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('UTC/offset timestamp required')
    value=row['value']
    if row['measurement_status']=='MEASURED':
        if type(value) not in (int,float) or not math.isfinite(value) or row['freshness']!='LIVE':raise ValueError('Measured requires fresh finite numeric value')
    elif value is not None:raise ValueError('Unavailable/uncollected is null, never numeric zero')
    for key in ('sampling_interval_seconds','collection_window_seconds'):
        v=row[key]
        if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<=0):raise ValueError('Positive bounded time metadata required')
    for key in ('scenario','ran_implementation','component','metric','unit','source','experiment_id','trial_id','software_version','reason'):
        if not isinstance(row[key],str) or len(row[key])>1024 or any(ord(c)<32 for c in row[key]):raise ValueError('Bounded metadata strings required')
    if not isinstance(row['labels'],dict) or len(row['labels'])>20 or len(json.dumps(row['labels']))>4096:raise ValueError('Bounded label metadata required')
    return row

def write(directory,rows,metadata):
    path=Path(directory);path.mkdir(parents=True,mode=0o700,exist_ok=False)
    for row in rows:validate(row)
    # Exclusive fresh result directory: never overwrite earlier evidence.
    (path/'measurements.json').write_text(json.dumps({'schema_version':1,'metadata':metadata,'records':rows},indent=2,allow_nan=False)+'\n')
    with (path/'measurements.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader()
        for row in rows:writer.writerow(dict(row,labels=json.dumps(row['labels'],sort_keys=True)))
    hashes={name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in ('measurements.json','measurements.csv')}
    (path/'manifest.json').write_text(json.dumps({'schema_version':1,'sha256':hashes},indent=2)+'\n')

def load(path):
    path=Path(path)
    if path.stat().st_size>16*1024*1024:raise ValueError('Input evidence budget')
    manifest=path.parent/'manifest.json'
    if manifest.exists():
        expected=json.loads(manifest.read_text()).get('sha256',{}).get(path.name)
        if expected!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Evidence digest mismatch')
    if path.suffix=='.json':
        obj=json.loads(path.read_text())
        if obj.get('schema_version')!=1:raise ValueError('Unknown result schema')
        rows=obj['records']
    else:
        with path.open(newline='') as stream:rows=list(csv.DictReader(stream))
        for row in rows:
            for key in ('value','sampling_interval_seconds','collection_window_seconds'):row[key]=float(row[key]) if row[key] else None
            row['labels']=json.loads(row['labels'])
    if len(rows)>50000:raise ValueError('Record count budget')
    return [validate(row) for row in rows]
