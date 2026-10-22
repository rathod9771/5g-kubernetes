"""Public GitHub GET-only CI step timestamps; never dispatches a workflow."""
import argparse
import json
import re
import urllib.request
from ..schema import record,write
from ..parsers.events import seconds

def collect(repo,branch,sha):
    import urllib.parse
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not re.fullmatch(r'[0-9a-f]{40}',sha):raise ValueError('Repository/SHA identity required')
    requests=0
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        nonlocal requests
        if requests>=2:raise ValueError('GitHub request budget')
        requests+=1
        request=urllib.request.Request('https://api.github.com/repos/'+repo+path,headers={'User-Agent':'performance-read-only-audit','Accept':'application/vnd.github+json'})
        with opener.open(request,timeout=6) as response:body=response.read(1024*1024+1)
        if len(body)>1024*1024:raise ValueError('Response budget')
        return json.loads(body)
    data=get('/actions/runs?'+urllib.parse.urlencode({'branch':branch,'per_page':10}))
    candidates=[x for x in data.get('workflow_runs',[]) if x.get('head_sha')==sha and x.get('status')=='completed']
    if not candidates:return [],{'status':'UNAVAILABLE','reason':'No completed run for exact baseline SHA in bounded recent history','requests':requests}
    run=candidates[0];rows=[]
    jobs=get('/actions/runs/'+str(int(run['id']))+'/jobs?per_page=10')
    if jobs.get('total_count',0)>10:raise ValueError('Job inventory incomplete')
    for job in jobs.get('jobs',[]):
        for step in job.get('steps',[]):
            if step.get('conclusion')!='success' or not step.get('started_at') or not step.get('completed_at'):continue
            if 'Regression' not in step['name'] and 'regression' not in step['name'] and 'validation' not in step['name'].lower():continue
            start,end=seconds(step['started_at']),seconds(step['completed_at'])
            if end<start:raise ValueError('CI clock order')
            rows.append(record(metric='ci_validation_step_duration',unit='seconds',source=run['html_url']+' step '+step['name'],value=end-start,status='MEASURED',at=end,scenario='ci-offline',implementation='none',component='github-actions',experiment='H-CI-records',trial=str(run['id']),version=sha,provenance='RECORDED_LIVE',freshness='LIVE',labels={'runner':job.get('runner_name','UNKNOWN'),'timestamp_resolution_seconds':1}))
    return rows,{'requests':requests,'run_id':run['id'],'run_conclusion':run['conclusion'],'sha':sha,'scope':'CI step only, not source build/deployment duration','lifecycle_calls':0}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--branch',required=True);p.add_argument('--sha',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    try:rows,meta=collect(a.repo,a.branch,a.sha)
    except Exception as e:rows=[];meta={'status':'UNAVAILABLE','error_class':type(e).__name__,'details':'SUPPRESSED'}
    write(a.output,rows,meta);print(json.dumps({'records':len(rows),'metadata':meta},indent=2))
if __name__=='__main__':main()
