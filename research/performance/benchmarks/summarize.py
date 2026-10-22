"""Summarize genuine evidence; independent trials, not scrapes, define sample N."""
import argparse
import collections
import json
import math
import random
import statistics
from ..schema import load

def percentile(values,p):
    data=sorted(values);index=(len(data)-1)*p;lo=int(index);hi=math.ceil(index)
    return data[lo]+(data[hi]-data[lo])*(index-lo)

def summarize(rows):
    groups=collections.defaultdict(lambda:collections.defaultdict(list));seen=set()
    for r in rows:
        if r['measurement_status']!='MEASURED' or r['freshness']!='LIVE' or r['provenance'] not in ('LIVE','RECORDED_LIVE'):continue
        identity=(r['experiment_id'],r['trial_id'],r['timestamp'],r['source'],json.dumps(r['labels'],sort_keys=True),r['metric'])
        if identity in seen:continue
        seen.add(identity)
        # Containers remain distinct: proxy/container/core metrics must not mix.
        dimensions={k:v for k,v in r['labels'].items() if k not in ('pod','pod_uid','clock_max_error_seconds','runner','timestamp_resolution_seconds','evidence_ref','operation_id')}
        key=tuple(r[k] for k in ('experiment_id','scenario','ran_implementation','component','metric','unit','software_version'))+(r['labels'].get('container',''),json.dumps(dimensions,sort_keys=True))
        groups[key][r['trial_id']].append(r['value'])
    result=[]
    for key,trials in sorted(groups.items()):
        if len(trials)>1000:raise ValueError('Trial budget')
        values=[statistics.mean(v) for v in trials.values()];n=len(values);ci=None
        if n>=5:
            rng=random.Random(2026);means=[statistics.mean(rng.choices(values,k=n)) for _ in range(1000)]
            ci=[percentile(means,.025),percentile(means,.975)]
        result.append(dict(zip(('experiment_id','scenario','implementation','component','metric','unit','software_version','container','dimensions_json'),key),sample_count=n,observed_sample_count=sum(map(len,trials.values())),mean=statistics.mean(values),median=statistics.median(values),standard_deviation=statistics.stdev(values) if n>1 else None,minimum=min(values),maximum=max(values),p95=percentile(values,.95),confidence_interval_95=ci,interval_method='exploratory percentile bootstrap of trial means;1000 resamples;seed2026' if ci else 'insufficient independent trials',aggregation='mean within trial, then equal-weight trials; p95 is of trial means',minimum_trial_count_met=n>=5))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',nargs='+');p.add_argument('--output',required=True);args=p.parse_args()
    rows=[]
    for path in args.input:rows.extend(load(path))
    result=summarize(rows)
    with open(args.output,'x') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print('Summary groups:',len(result))
if __name__=='__main__':main()
