#!/usr/bin/env python3
"""Namespace-scoped, bounded radio log observer. Standard library only.

No pod exec, watch streams, lifecycle writes, remote-control subscription or raw
log persistence. Polls recover from discovery/read errors; original timestamps
bound freshness. Parser support is not evidence a given image emits the format.
"""
import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import signal
import ssl
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NUMBER=r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
UE=re.compile(r"\bUE (?:RNTI )?([0-9a-fA-F]{1,4})(?=:|\s)")
LABELS=('namespace','scenario','implementation','component','pod','pod_uid','container')


def finite(value):
    if isinstance(value,bool):raise ValueError('Boolean is not a measurement')
    result=float(value)
    if not math.isfinite(result):raise ValueError('Finite measurement required')
    return result


def parse_oai(line):
    """Explicit OAI textual fields; no SNR inference or composite-SNR truncation."""
    if len(line)>16384:return []
    match=UE.search(line)
    if not match:return []
    rnti=match[1].lower();values={}
    m=re.search(r"average RSRP ("+NUMBER+r")(?=\s|,|$)",line)
    if m:values['oai_gnb_ue_rsrp_dbm']=finite(m[1])
    for direction,channel in (('dl','dlsch'),('ul','ulsch')):
        rounds=re.search(channel+r"_rounds (\d+)/(\d+)/(\d+)/(\d+),?",line)
        if not rounds:continue
        for index in range(1,4):values[f'oai_gnb_ue_{channel}_harq_round{index}_total']=int(rounds[index+1])
        errors=re.search(channel+r"_errors (\d+)",line)
        if errors:values[f'oai_gnb_ue_{channel}_errors_total']=int(errors[1])
        snr=re.search(r"\bSNR ("+NUMBER+r")(?=dB(?:\s|,|$)|\s|,|$)",line)
        bler=re.search(r"\bBLER ("+NUMBER+r")(?=\s|,|$)",line)
        if snr:values[f'oai_gnb_ue_{direction}_snr_db']=finite(snr[1])
        if bler and 0<=finite(bler[1])<=1:values[f'oai_gnb_ue_{direction}_bler_ratio']=finite(bler[1])
    mac=re.search(r"MAC:\s+TX\s+(\d+) RX\s+(\d+) bytes",line)
    if mac:
        values['oai_gnb_ue_mac_tx_bytes_total']=int(mac[1]);values['oai_gnb_ue_mac_rx_bytes_total']=int(mac[2])
    return [(rnti,'unknown',values)] if values else []


def parse_srsran(line):
    """Documented scheduler JSON only, if already forwarded into pod stdout.

    Native pusch_snr_db and MAC bitrate fields retain their names/units. No CQI
    -> DL-SNR conversion, dB -> dBm conversion, or invented OAI statistics.
    See docs.srsran.com/.../user_manuals/source/outputs.html (scheduler metrics).
    Exact pinned-runtime schema and transport remain a lab verification gate.
    """
    if len(line)>16384 or not line.lstrip().startswith('{'):return []
    data=json.loads(line);result=[]
    if not isinstance(data,dict) or not isinstance(data.get('cells'),list):return []
    for cell in data['cells'][:16]:
        if not isinstance(cell,dict):continue
        for ue in cell.get('ue_list',[])[:256]:
            if not isinstance(ue,dict) or type(ue.get('rnti')) is not int or not 1<=ue['rnti']<=65535:continue
            values={}
            for field,metric in [('pusch_snr_db','srsran_ue_pusch_snr_db'),('dl_brate','srsran_ue_mac_dl_bitrate_kbps'),('ul_brate','srsran_ue_mac_ul_bitrate_kbps')]:
                if field in ue and ue[field] is not None:
                    value=finite(ue[field])
                    if field=='pusch_snr_db' or value>=0:values[metric]=value
            if 'pci' in ue and (type(ue['pci']) is not int or not 0<=ue['pci']<=1007):continue
            if values:result.append((format(ue['rnti'],'x'),str(ue.get('pci','unknown')),values))
    return result


def timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(?:Z|[+-][0-9]{2}:[0-9]{2})",value):
        raise ValueError('Timezone-qualified source timestamp required')
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def classify(pod):
    metadata=pod.get('metadata',{});labels=metadata.get('labels',{});name=metadata.get('name','')
    role=labels.get('component')
    if role not in ('cu','du','gnb'):
        role=next((r for r in ('cu','du','gnb') if labels.get('app','').endswith('-'+r)),None)
    result=[]
    for container in pod.get('spec',{}).get('containers',[]):
        image=container.get('image','').lower()
        impl='oai' if 'oai-gnb' in image else 'srsran' if 'srsran' in image else None
        if impl is None:continue
        actual_role=role or ('gnb' if impl=='oai' else None)
        if actual_role is None:continue
        result.append({'namespace':metadata.get('namespace',''),'scenario':labels.get('ran-type','unknown'),
            'implementation':impl,'component':actual_role,'pod':name,'pod_uid':metadata['uid'],
            'container':container['name'],'image':container.get('image','')})
    return result


class API:
    def __init__(self,namespace):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',namespace):raise ValueError('Explicit namespace required')
        self.namespace=namespace
        self.base='https://'+os.environ['KUBERNETES_SERVICE_HOST']+':'+os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS','443')
        self.root='/var/run/secrets/kubernetes.io/serviceaccount/'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=self.root+'ca.crt')),NoRedirect())
    def get(self,suffix,query,limit):
        with open(self.root+'token') as stream:token=stream.read(16385).strip()
        if len(token)>16384:raise ValueError('Token budget')
        path='/api/v1/namespaces/'+self.namespace+'/pods'+suffix
        req=urllib.request.Request(self.base+path+'?'+urllib.parse.urlencode(query),headers={'Authorization':'Bearer '+token},method='GET')
        started=time.monotonic();chunks=[];total=0
        with self.opener.open(req,timeout=2) as response:
            while True:
                if time.monotonic()-started>6:raise TimeoutError('API wall-clock budget')
                chunk=response.read1(min(65536,limit+1-total))
                if not chunk:break
                chunks.append(chunk);total+=len(chunk)
                if total>limit:raise ValueError('Response budget')
        body=b''.join(chunks)
        if len(body)>limit:raise ValueError('Response budget')
        return body
    def discover(self,selector):
        data=json.loads(self.get('',{'labelSelector':selector,'limit':129},2*1024*1024))
        if data.get('metadata',{}).get('continue') or len(data.get('items',[]))>128:raise ValueError('Discovery inventory budget')
        return [p for p in data.get('items',[]) if p.get('status',{}).get('phase')=='Running' and not p.get('metadata',{}).get('deletionTimestamp')]
    def logs(self,source):
        return self.get('/'+urllib.parse.quote(source['pod'],safe='')+'/log',
            {'container':source['container'],'timestamps':'true','sinceSeconds':120,'tailLines':200,'limitBytes':65536},65536).decode(errors='replace')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None


class Observer:
    def __init__(self,api,namespace,selector='',ttl=120,max_sources=8,max_series=2048,clock=time.time):
        self.api,self.namespace,self.selector=api,namespace,selector
        if not 15<=ttl<=600 or not 1<=max_sources<=16 or not 16<=max_series<=8192:raise ValueError('Observer budgets')
        self.ttl,self.max_sources,self.max_series,self.clock=ttl,max_sources,max_series,clock
        self.sources={};self.values={};self.seen={};self.lock=threading.RLock()
        self.discovery_ok=0;self.discovery_at=0;self.discovery_failures=0;self.oai_parsed=0;self.errors=collections.Counter();self.stop=threading.Event()
    def cycle(self):
        now=self.clock()
        try:
            pods=self.api.discover(self.selector);found={}
            for pod in pods:
                for source in classify(pod):
                    if source['namespace']!=self.namespace:raise ValueError('Namespace mismatch')
                    found[(source['pod_uid'],source['container'])]=source
            if len(found)>self.max_sources:raise ValueError('Source budget exceeded')
        except Exception:
            with self.lock:self.discovery_ok=0;self.discovery_failures+=1
            return False
        with self.lock:
            self.discovery_ok=1;self.discovery_at=now
            for key in list(self.sources):
                if key not in found:
                    self.sources.pop(key);self.seen.pop(key,None)
                    self.values={k:v for k,v in self.values.items() if k[:2]!=key}
            for key,source in found.items():
                if key not in self.sources:self.sources[key]=dict(source,log_ok=0,last_log=0,last_parse=0,parsed=0,received=0,saturated=0)
                self.seen.setdefault(key,collections.OrderedDict())
        success=True
        for key in found:
            if self.stop.is_set():break
            try:
                text=self.api.logs(found[key])
                with self.lock:
                    source=self.sources[key];source['saturated']=int(len(text.encode())>=65536 or len(text.splitlines())>=200)
                    source['log_ok']=1
                    parser=parse_oai if source['implementation']=='oai' else parse_srsran
                    for line in text.splitlines()[:200]:
                        source['received']+=1
                        try:stamp,body=line.split(' ',1);at=timestamp(stamp)
                        except (ValueError,TypeError):self.errors['timestamp']+=1;continue
                        if not now-self.ttl<=at<=now+5:continue
                        digest=hashlib.sha256(line.encode()).hexdigest()
                        if digest in self.seen[key]:continue
                        self.seen[key][digest]=True
                        while len(self.seen[key])>512:self.seen[key].popitem(last=False)
                        source['last_log']=max(source['last_log'],at)
                        try:samples=parser(body)
                        except (ValueError,TypeError,KeyError,OverflowError):self.errors['parse']+=1;continue
                        for rnti,cell,measurements in samples:
                            source['parsed']+=1;source['last_parse']=max(source['last_parse'],at)
                            if source['implementation']=='oai':self.oai_parsed+=1
                            for metric,value in measurements.items():
                                identity=key+(rnti,cell,metric)
                                if identity not in self.values and len(self.values)>=self.max_series:
                                    self.errors['series_budget']+=1;continue
                                if identity not in self.values or at>=self.values[identity][1]:self.values[identity]=(value,at)
                    self.expire(now)
            except Exception:
                with self.lock:self.sources[key]['log_ok']=0;self.errors['log_read']+=1
                success=False
        return success
    def expire(self,now):
        self.values={k:v for k,v in self.values.items() if -5<=now-v[1]<=self.ttl}
    def run(self,interval=15):
        if not 5<=interval<=60:raise ValueError('Polling interval budget')
        backoff=interval
        while not self.stop.is_set():
            ok=self.cycle();backoff=interval if ok else min(60,backoff*2)
            self.stop.wait(backoff)
    def render(self):
        def sample(name,value,labels=None):
            labels=labels or {};escape=lambda v:str(v).replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
            suffix='{'+','.join(k+'="'+escape(v)+'"' for k,v in labels.items())+'}' if labels else ''
            return name+suffix+' '+str(value)+'\n'
        with self.lock:
            now=self.clock();self.expire(now)
            text=sample('ran_exporter_discovery_success',self.discovery_ok)+sample('ran_exporter_discovery_last_success_timestamp_seconds',self.discovery_at)
            text+=sample('ran_exporter_discovery_failures_total',self.discovery_failures)
            text+=sample('oai_gnb_exporter_lines_parsed_total',self.oai_parsed)
            for reason,count in sorted(self.errors.items()):text+=sample('ran_exporter_errors_total',count,{'reason':reason})
            for key,source in self.sources.items():
                labels={k:source[k] for k in LABELS}
                healthy=int(self.discovery_ok and source['log_ok'] and not source['saturated'] and now-self.discovery_at<=self.ttl)
                fresh=int(healthy and source['last_parse']>0 and -5<=now-source['last_parse']<=self.ttl)
                for name,value in [('collection_success',healthy),('radio_statistics_fresh',fresh),('last_log_timestamp_seconds',source['last_log']),('last_radio_timestamp_seconds',source['last_parse']),('log_window_saturated',source['saturated']),('lines_received_total',source['received']),('lines_parsed_total',source['parsed'])]:
                    text+=sample('ran_exporter_source_'+name,value,labels)
            for (uid,container,rnti,cell,metric),(value,at) in sorted(self.values.items()):
                source=self.sources[(uid,container)];labels={k:source[k] for k in LABELS};labels.update(rnti=rnti,cell=cell)
                text+=sample(metric,value,labels)
                text+=sample('ran_exporter_measurement_timestamp_seconds',at,dict(labels,metric=metric))
            return text.encode()


def main():
    namespace=os.environ.get('TARGET_NAMESPACE','');api=API(namespace)
    observer=Observer(api,namespace,os.environ.get('POD_LABEL_SELECTOR',''))
    worker=threading.Thread(target=observer.run,name='radio-reader',daemon=True)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/metrics':payload=observer.render();status=200;kind='text/plain; version=0.0.4'
            elif self.path=='/healthz':payload=b'worker-alive\n';status=200 if worker.is_alive() else 503;kind='text/plain'
            else:payload=b'not found\n';status=404;kind='text/plain'
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        slots=threading.BoundedSemaphore(4)
        def process_request(self,request,address):
            request.settimeout(2)
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request);return
            try:super().process_request(request,address)
            except BaseException:self.slots.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
    server=Server(('0.0.0.0',9091),Handler)
    worker.start();http=threading.Thread(target=server.serve_forever,daemon=True);http.start()
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,lambda *_:observer.stop.set())
    observer.stop.wait();server.shutdown();server.server_close();worker.join(timeout=10);http.join(timeout=2)
    if worker.is_alive():raise SystemExit('Bounded collector shutdown did not complete')
if __name__=='__main__':main()
