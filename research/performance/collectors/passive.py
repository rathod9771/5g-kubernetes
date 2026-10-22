"""One-shot, read-only resource collector. No deployment/exec/traffic primitives."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
import ssl
from ..schema import record,write,utc

NS='c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class HTTP:
    def __init__(self,base):
        u=urllib.parse.urlsplit(base)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('Credential-free monitoring URL required')
        self.base=base.rstrip('/');self.requests=0;self.deadline=time.monotonic()+40
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def get(self,path):
        if not path.startswith(('/api/v1/query?','/api/v1/targets?')):raise PermissionError('Monitoring GET allowlist')
        if self.requests>=8:raise ValueError('HTTP request budget')
        deadline=min(self.deadline,time.monotonic()+6)
        if time.monotonic()>=deadline:raise TimeoutError('Collection HTTP deadline')
        self.requests+=1;parts=[];size=0
        with self.opener.open(self.base+path,timeout=2) as response:
            while True:
                if time.monotonic()>=deadline:raise TimeoutError('HTTP wall-clock deadline')
                chunk=response.read1(min(65536,2*1024*1024+1-size))
                if not chunk:break
                parts.append(chunk);size+=len(chunk)
                if size>2*1024*1024:raise ValueError('HTTP response budget')
        body=b''.join(parts)
        if len(body)>2*1024*1024:raise ValueError('HTTP response budget')
        return json.loads(body)
    def query(self,expression,at):
        data=self.get('/api/v1/query?'+urllib.parse.urlencode({'query':expression,'time':at,'timeout':'3s'}))
        if data.get('status')!='success' or data.get('data',{}).get('resultType')!='vector':raise ValueError('Prometheus query failed/type unsupported')
        return data['data']['result']

class Kubernetes:
    def __init__(self,kubeconfig,namespace):self.config,self.namespace,self.requests=kubeconfig,namespace,0
    def pods(self):
        if self.requests:raise PermissionError('One inventory request only')
        self.requests+=1
        command=['kubectl','--kubeconfig',self.config,'--request-timeout=6s','get','pods','-n',self.namespace,'-o','json']
        # Reuse the existing tested bounded reader: Go-compatible memory policy,
        # output/time/RSS limits, PID-safe descendant cleanup; GET only.
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
        from assurance.kubernetes import KubernetesReader
        reader=KubernetesReader({'OSM_KUBECONFIG_PATH':self.config},self.namespace,timeout=8,max_output=2*1024*1024,budget=1)
        result=reader(['get','pods','-n',self.namespace,'-o','json'])
        self.stats=reader.stats
        return result

def component(pod):
    labels=pod['metadata'].get('labels',{});name=pod['metadata']['name']
    role=labels.get('component')
    if role in ('cu','du','gnb','fog'):return role
    if labels.get('osm.etsi.org/ns-id')=='1b1cb300-b571-43f2-a567-6540895d7d0f':return 'open5gs-'+labels.get('app.kubernetes.io/name','mongo')
    return None

def quantity(text,cpu=False):
    if text is None:return None
    text=str(text)
    units={'n':1e-9,'u':1e-6,'m':1e-3} if cpu else {'Ki':1024,'Mi':1024**2,'Gi':1024**3,'Ti':1024**4,'K':1e3,'M':1e6,'G':1e9,'T':1e12,'m':.001}
    for suffix,multiplier in sorted(units.items(),key=lambda x:-len(x[0])):
        if text.endswith(suffix):return float(text[:-len(suffix)])*multiplier
    return float(text)

def expected_apps(scenario,implementation):
    registry=json.loads((Path(__file__).resolve().parents[3]/'config/scenarios.json').read_text())
    entry=next((s for s in registry['scenarios'] if s['key']==scenario),None)
    if entry is None or entry.get('implementation','').lower()!=implementation.lower():raise ValueError('Known scenario/implementation pair required')
    expected={chart['release_name'] for chart in entry['charts']}
    return expected

def collect(kube,http,*,scenario='fran-oai',implementation='oai',experiment='B-resource-audit',trial='snapshot',clock=time.time):
    at=clock();rows=[];failures=[]
    try:inventory=kube.pods();pods=inventory['items']
    except Exception as error:pods=[];failures.append({'source':'kubernetes','class':type(error).__name__})
    expected=expected_apps(scenario,implementation)
    # Associate observations by actual app labels, not a caller's scenario name.
    # The explicitly identified reference Core is reported separately as shared.
    owned={p['metadata']['name']:p for p in pods if component(p) and
           (component(p).startswith('open5gs-') or p['metadata'].get('labels',{}).get('app') in expected)}
    def add(pod,container,metric,unit,value=None,source='kubernetes',stamp=at,**kwargs):
        c=component(pod);spec=next((x for x in pod['spec']['containers'] if x['name']==container),{})
        labels={'pod':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],'container':container}
        rows.append(record(metric=metric,unit=unit,value=value,status='MEASURED' if value is not None else 'UNAVAILABLE',source=source,at=stamp,scenario=scenario,implementation=implementation,component=c,experiment=experiment,trial=trial,version=spec.get('image','UNKNOWN'),freshness='LIVE' if value is not None else 'UNAVAILABLE',labels=labels,**kwargs))
    for p in owned.values():
        condition=next((x for x in p.get('status',{}).get('conditions',[]) if x.get('type')=='Ready'),None)
        ready=int(condition['status']=='True') if condition and condition.get('status') in ('True','False') else None
        add(p,p['spec']['containers'][0]['name'],'pod_ready','boolean',ready,source='kubernetes:status.conditions[Ready]')
        for c in p['spec']['containers']:
            name=c['name'];resources=c.get('resources',{})
            for kind in ('requests','limits'):
                for resource,unit in (('cpu','cores'),('memory','bytes')):
                    value=quantity(resources.get(kind,{}).get(resource),cpu=resource=='cpu')
                    add(p,name,resource+'_'+kind,unit,value,reason='' if value is not None else 'Not configured; not zero')
            status=next((s for s in p.get('status',{}).get('containerStatuses',[]) if s['name']==name),None)
            add(p,name,'container_restarts','count',status.get('restartCount') if status else None)
            add(p,name,'container_ready','boolean',int(status['ready']) if status and type(status.get('ready')) is bool else None)
    sel='namespace='+json.dumps(kube.namespace)+',container!="",container!="POD"'
    queries={
      'source_timestamp':('max by (pod,container) (timestamp(container_cpu_usage_seconds_total{'+sel+'}))','seconds',None),
      'cpu_usage':('max by (pod,container) (rate(container_cpu_usage_seconds_total{'+sel+'}[5m]))','cores',300),
      'memory_working_set':('max by (pod,container) (container_memory_working_set_bytes{'+sel+'})','bytes',None),
      'cpu_throttled_period_fraction':('max by (pod,container) (rate(container_cpu_cfs_throttled_periods_total{'+sel+'}[5m]) / rate(container_cpu_cfs_periods_total{'+sel+'}[5m]))','ratio',300)}
    data={}
    for metric,(query,unit,window) in queries.items():
        try:
            result=http.query(query,at)
            data[metric]={(v['metric'].get('pod'),v['metric'].get('container')):float(v['value'][1]) for v in result}
        except Exception as error:data[metric]={};failures.append({'source':'prometheus','metric':metric,'class':type(error).__name__})
    for p in owned.values():
        for c in p['spec']['containers']:
            key=(p['metadata']['name'],c['name']);stamp=data['source_timestamp'].get(key)
            for metric,(_,unit,window) in queries.items():
                if metric=='source_timestamp':continue
                value=data[metric].get(key)
                fresh=stamp is not None and math.isfinite(stamp) and 0<=at-stamp<=120
                valid=value is not None and math.isfinite(value) and value>=0
                if metric.endswith('fraction') and valid:valid=value<=1
                add(p,c['name'],metric,unit,value if fresh and valid else None,source='prometheus:'+queries[metric][0],stamp=stamp if fresh else at,interval=30,window=window,reason='' if fresh and valid else 'Missing/nonfinite series or source older than120s')
                if stamp is not None and not fresh:rows[-1]['freshness']='STALE'
    exporter_interval=None
    try:
        data=http.get('/api/v1/targets?state=active')['data']['activeTargets']
        intervals=set()
        def duration(text):
            for suffix,multiplier in [('ms',.001),('s',1),('m',60)]:
                if text.endswith(suffix):return float(text[:-len(suffix)])*multiplier
            return None
        for target in data:
            path=urllib.parse.urlsplit(target.get('scrapeUrl','')).path
            if path=='/metrics/cadvisor':intervals.add(duration(target.get('scrapeInterval','')))
            if 'ran-exporter' in target.get('labels',{}).get('job',''):exporter_interval=duration(target.get('scrapeInterval',''))
        resource_interval=next(iter(intervals)) if len(intervals)==1 else None
        for row in rows:
            if row['source'].startswith('prometheus:'):row['sampling_interval_seconds']=resource_interval
        for target in data:
            labels=target.get('labels',{});rows.append(record(metric='scrape_target_up',unit='boolean',source='prometheus:/api/v1/targets',value=int(target['health']=='up'),status='MEASURED',at=at,scenario=scenario,implementation=implementation,component='monitoring',experiment=experiment,trial=trial,version='Prometheus API v1',freshness='LIVE',interval=duration(target.get('scrapeInterval','')),labels={'job':labels.get('job','UNKNOWN'),'instance':labels.get('instance','UNKNOWN')}))
    except Exception as error:failures.append({'source':'prometheus-targets','class':type(error).__name__})
    try:
        series=http.query('oai_gnb_exporter_lines_parsed_total',at)
        if not series:raise ValueError('No exporter sample')
        for value in series:
            rows.append(record(metric='exporter_lines_parsed',unit='count',source='prometheus:oai_gnb_exporter_lines_parsed_total',value=float(value['value'][1]),status='MEASURED',at=float(value['value'][0]),scenario=scenario,implementation=implementation,component='ran-exporter',experiment=experiment,trial=trial,version='live legacy exporter',freshness='LIVE',interval=exporter_interval))
    except Exception as error:failures.append({'source':'ran-exporter','class':type(error).__name__})
    if not owned:rows.append(record(metric='resource_inventory',unit='count',source='kubernetes',reason='No trustworthy owned pod inventory; no resource success claimed',scenario=scenario,implementation=implementation,experiment=experiment,trial=trial))
    return rows,{'collected_at':utc(at),'scenario':scenario,'collection_kind':'PASSIVE_SNAPSHOT_NOT_A_BENCHMARK','http_requests':http.requests,'kubernetes_list_requests':kube.requests,'failures':failures,'duplicate_scrapes':'max per pod/container; sidecars separate','freshness_limit_seconds':120,'cpu_window_seconds':300,'lifecycle_calls':0,'expected_ran_app_labels':sorted(expected),'kubernetes_reader':getattr(kube,'stats',{})}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',required=True);parser.add_argument('--namespace',default=NS);parser.add_argument('--prometheus-url',required=True);parser.add_argument('--output',required=True);parser.add_argument('--scenario',default='fran-oai');parser.add_argument('--implementation',default='oai');parser.add_argument('--trial-id',required=True)
    args=parser.parse_args();started=time.monotonic()
    import resource
    before=resource.getrusage(resource.RUSAGE_SELF);children=resource.getrusage(resource.RUSAGE_CHILDREN)
    rows,meta=collect(Kubernetes(args.kubeconfig,args.namespace),HTTP(args.prometheus_url),scenario=args.scenario,implementation=args.implementation,trial=args.trial_id)
    usage=resource.getrusage(resource.RUSAGE_SELF);child=resource.getrusage(resource.RUSAGE_CHILDREN)
    meta.update(wall_seconds=time.monotonic()-started,cpu_seconds=(usage.ru_utime+usage.ru_stime-before.ru_utime-before.ru_stime)+(child.ru_utime+child.ru_stime-children.ru_utime-children.ru_stime),parent_peak_rss_kib=usage.ru_maxrss,child_lifetime_peak_rss_kib=child.ru_maxrss)
    write(args.output,rows,meta);print(json.dumps({'records':len(rows),'metadata':meta},indent=2))
if __name__=='__main__':main()
