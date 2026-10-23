"""Explicit, bounded read-only collectors. Injected transports allow completely offline tests."""
import json
import math
import time
import urllib.parse
import urllib.request
from .model import timestamp


def unavailable(source, at, reason='adapter unavailable'):
    return {'source':source,'provenance':'UNAVAILABLE','observed_at':at,'reason':reason,'value':None}


def http_json(url):
    with urllib.request.urlopen(url,timeout=4) as response:
        content=response.read(2*1024*1024+1)
        if len(content)>2*1024*1024:raise ValueError('Response exceeds limit')
        return json.loads(content)

class Prometheus:
    def __init__(self, url, transport=http_json, clock=time.time, max_age=120):
        self.url,self.transport,self.clock,self.max_age=url.rstrip('/'),transport,clock,max_age
    def query(self, expression):
        at=self.clock()
        try:
            data=self.transport(self.url+'/api/v1/query?'+urllib.parse.urlencode({'query':expression}))
            if data.get('status')!='success' or data['data']['resultType']!='vector':raise ValueError('Query failed')
            rows=data['data']['result']
            if not rows:return unavailable('prometheus',at,'metric missing; not zero')
            if len(rows)>256:raise ValueError('Too many series')
            samples=[]
            for row in rows:
                sample_at=timestamp(row['value'][0]); value=float(row['value'][1])
                if not math.isfinite(value) or sample_at>at+30:raise ValueError('Invalid sample')
                labels={k:str(v) for k,v in row.get('metric',{}).items() if k in ('job','namespace','pod','container','node','instance','service','condition','status','reason','phase','mode')}
                samples.append({'at':sample_at,'value':value,'labels':labels})
            stale=any(at-s['at']>self.max_age for s in samples)
            return {'source':'prometheus','provenance':'STALE' if stale else 'LIVE',
                    'observed_at':at,'value':samples,'reason':None}
        except Exception:
            return unavailable('prometheus',at,'query failed; private details suppressed')
    def health(self):
        # timestamp(up) exposes underlying sample time, unlike aggregation evaluation timestamps.
        result=self.query('timestamp(up)')
        if result['provenance']!='UNAVAILABLE':
            for sample in result['value']:
                if sample['value']>self.clock()+30 or sample['value']<0:
                    return unavailable('prometheus',self.clock(),'Invalid source sample time')
                sample['at']=sample['value']
            result['provenance']='STALE' if any(self.clock()-s['at']>self.max_age for s in result['value']) else 'LIVE'
        return result

    def measurement(self, expression, source_selector):
        result=self.query(expression)
        if result['provenance']=='UNAVAILABLE':return result
        freshness=self.query('timestamp('+source_selector+')')
        if freshness['provenance']=='UNAVAILABLE':return unavailable('prometheus',self.clock(),'Source freshness missing')
        times={tuple(sorted(s['labels'].items())):s['value'] for s in freshness['value']}
        for sample in result['value']:
            at=times.get(tuple(sorted(sample['labels'].items())))
            if at is None or at>self.clock()+30 or at<0:
                return unavailable('prometheus',self.clock(),'Source freshness cannot be matched')
            sample['at']=at
        result['provenance']='STALE' if any(self.clock()-s['at']>self.max_age for s in result['value']) else 'LIVE'
        return result

    def targets(self):
        at=self.clock()
        try:
            data=self.transport(self.url+'/api/v1/targets?state=active')
            if data.get('status')!='success':raise ValueError('Target API failed')
            rows=data['data']['activeTargets']
            if not rows or len(rows)>256:raise ValueError('Targets absent or limit exceeded')
            values=[]
            for target in rows:
                last=timestamp(target['lastScrape'])
                labels={k:v for k,v in target.get('labels',{}).items() if k in ('job','namespace','instance','pod','service')}
                values.append({'labels':labels,'health':target['health'],'last_scrape':last,
                    'fresh':0<=at-last<=self.max_age,'scrape_duration':target.get('lastScrapeDuration'),
                    'failure_class':'connection_refused' if 'connection refused' in target.get('lastError','') else 'scrape_failed' if target.get('lastError') else None})
            return {'source':'prometheus targets','provenance':'LIVE','observed_at':at,'value':values,
                    'status':'DEGRADED' if any(t['health']!='up' or not t['fresh'] for t in values) else 'AVAILABLE'}
        except Exception:return unavailable('prometheus targets',at,'Target API failed')

class Kubernetes:
    def __init__(self, read, clock=time.time):
        self.read,self.clock=read,clock
    def snapshot(self, namespace):
        at=self.clock()
        try:
            data=self.read(['get','pods','-n',namespace,'-o','json'])
            if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise ValueError('Invalid API response')
            resources=[]
            for pod in data['items'][:256]:
                status=pod.get('status',{}); meta=pod['metadata']; containers=status.get('containerStatuses',[])
                if meta.get('namespace',namespace)!=namespace:raise ValueError('Namespace mismatch')
                condition=next((c.get('status') for c in status.get('conditions',[]) if c.get('type')=='Ready'),None)
                ready={'True':True,'False':False}.get(condition)
                if meta.get('deletionTimestamp'):ready=None
                resources.append({'name':meta['name'],'uid':meta['uid'],
                    'labels':{k:v for k,v in meta.get('labels',{}).items() if k in ('component','app','app.kubernetes.io/name','app.kubernetes.io/instance','osm.etsi.org/ns-id')},
                    'ready':ready,'namespace':namespace,'pod_ip':status.get('podIP'),
                    'host_network':pod.get('spec',{}).get('hostNetwork',False),
                    'containers':[{'name':c['name'],'image':c.get('image','')} for c in pod.get('spec',{}).get('containers',[])],
                    'phase':status.get('phase','Unknown'),'restarts':sum(c.get('restartCount',0) for c in containers),
                    'container_restarts':{c['name']:c.get('restartCount',0) for c in containers},
                    'container_identities':[{k:c.get(k) for k in ('name','containerID','imageID','restartCount')} for c in containers],
                    'node':pod.get('spec',{}).get('nodeName'),
                    'crash_loop':any(c.get('state',{}).get('waiting',{}).get('reason')=='CrashLoopBackOff' for c in containers),
                    'oom_killed':any(c.get('lastState',{}).get('terminated',{}).get('reason')=='OOMKilled' for c in containers)})
            return {'source':'kubernetes','provenance':'LIVE','observed_at':at,'value':resources,
                    'truncated':len(data['items'])>256 or bool(data.get('metadata',{}).get('continue')),'namespace':namespace}
        except Exception:return unavailable('kubernetes',at,'Kubernetes API unavailable')
    def infrastructure(self):
        at=self.clock()
        try:
            data=self.read(['get','nodes','-o','json'])
            values=[{'name':n['metadata']['name'],'uid':n['metadata']['uid'],
                'labels':{k:v for k,v in n['metadata'].get('labels',{}).items() if k in ('kubernetes.io/hostname','node-role.kubernetes.io/control-plane')},
                'conditions':{c['type']:c['status'] for c in n['status'].get('conditions',[])},
                'allocatable':{k:v for k,v in n['status'].get('allocatable',{}).items() if k in ('cpu','memory','pods')}} for n in data['items'][:32]]
            return {'source':'kubernetes','provenance':'LIVE','observed_at':at,'value':values}
        except Exception:return unavailable('kubernetes',at,'Node API unavailable')

    def warnings(self, namespace):
        at=self.clock()
        try:
            data=self.read(['get','events','-n',namespace,'--field-selector=type=Warning','-o','json'])
            values=[]
            for event in data['items'][:256]:
                meta=event['metadata']; obj=event.get('involvedObject',{})
                last=event.get('lastTimestamp') or event.get('eventTime') or meta.get('creationTimestamp')
                sample_at=timestamp(last)
                if sample_at>at+30:raise ValueError('Future event')
                values.append({'uid':meta['uid'],'namespace':meta.get('namespace',namespace),
                    'labels':{k:v for k,v in meta.get('labels',{}).items() if k in ('component','app','osm.etsi.org/ns-id')},
                    'reason':event.get('reason'),'count':event.get('count',1),
                    'resource':{k:obj.get(k) for k in ('kind','name','uid')},
                    'at':sample_at,'provenance':'LIVE' if at-sample_at<=120 else 'STALE'})
            return {'source':'kubernetes warning events','provenance':'LIVE','observed_at':at,
                    'value':values,'truncated':len(data['items'])>256}
        except Exception:return unavailable('kubernetes warning events',at,'Warning event API unavailable')

class OSM:
    def __init__(self, client, clock=time.time):self.client,self.clock=client,clock
    def snapshot(self, ns_id, operation_id=None):
        at=self.clock()
        try:
            ns=self.client.get_ns_instance(ns_id)
            if ns.get('_id',ns.get('id'))!=ns_id:raise ValueError('Identity mismatch')
            value={k:ns.get(k) for k in ('nsState','operational-status','config-status','currentOperation','currentOperationID')}
            # KDU/VNF fields vary by profile; absence remains unknown.
            value['vnf_state_available']=isinstance(ns.get('vnfr'),list)
            if operation_id:
                op=self.client.get_operation(operation_id)
                if op.get('nsInstanceId')!=ns_id:raise ValueError('Operation ownership mismatch')
                value['operation']={k:op.get(k) for k in ('operationState','lcmOperationType','startTime','statusEnteredTime')}
                value['operation_id']=operation_id
                entered=value['operation'].get('statusEnteredTime')
                value['stalled_candidate']=bool(type(entered) in (int,float) and at-entered>900 and op.get('operationState') in ('PROCESSING','STARTING'))
            return {'source':'osm','provenance':'LIVE','observed_at':at,'value':value,
                'protocol_health':'UNAVAILABLE','healing':'UNVALIDATED','scaling':'UNVALIDATED'}
        except Exception:return unavailable('osm',at,'OSM API unavailable or ownership unverified')

class ProtocolEvidence:
    @staticmethod
    def from_sctp(table, clock=time.time):
        from ran_health import established
        if not isinstance(table,str) or not table.splitlines() or not {'ST','LPORT','RPORT'}<=set(table.splitlines()[0].split()):
            return unavailable('kernel SCTP',clock(),'Missing or malformed association table')
        # Association is transport evidence only; all higher states remain unknown.
        return {'source':'kernel SCTP','provenance':'LIVE','observed_at':clock(),
            'value':{'f1_association':established(table,38472),
                'ngap_association':established(table,38412,remote_only=True),
                'f1_setup':None,'ng_setup':None,'cell_active':None,
                'ue_registered':None,'pdu_established':None,'user_plane_measured':None}}

    @staticmethod
    def scoped(observation, pod, peers, context, ns_id, clock=time.time, max_age=120):
        """Trusted provider contract; reject stale/replaced/mis-scoped assertions.

        peers maps f1/ngap to current owned peer pods. Only pod-IP paths are
        supported; VIP/NAT requires independently verified endpoint mapping.
        No protocol setup, registration or user-plane success is inferred.
        """
        import ipaddress
        from ran_health import scoped_association
        at=clock()
        unknown={'f1_association':None,'ngap_association':None,'f1_setup':None,
                 'ng_setup':None,'ue_registered':None,'pdu_established':None,'user_plane_measured':None}
        result={'source':'scoped kernel SCTP','provenance':'UNAVAILABLE','observed_at':at,
                'value':unknown,'reason':'Missing or invalid scoped protocol evidence'}
        try:
            if not isinstance(observation,dict) or observation.get('provenance')!='LIVE':return result
            stamp=timestamp(observation['observed_at'])
            if not 0<=at-stamp<=max_age:
                result['provenance']='STALE';return result
            binding={'namespace':context['namespace'],'namespace_uid':context['namespace_uid'],
                     'cluster_uid':context['cluster_uid'],'ns_id':ns_id,'pod':pod['name'],
                     'pod_uid':pod['uid'],'pod_ip':pod['pod_ip']}
            if not all(isinstance(v,str) and v and observation.get(k)==v for k,v in binding.items()):return result
            if pod.get('host_network') is not False:return result
            ipaddress.ip_address(pod['pod_ip'])
            container=observation.get('container')
            if container not in [c['name'] for c in pod['containers']]:return result
            # A sidecar cannot attest to the intended RAN process namespace.
            image=next(c['image'].lower() for c in pod['containers'] if c['name']==container)
            if 'oai-gnb' not in image and 'srsran' not in image:return result
            table=observation['table']
            if not isinstance(table,str) or len(table.encode())>65536:return result
            available=False
            for protocol,peer in peers.items():
                if protocol not in ('f1','ngap') or not peer or peer.get('host_network') is not False:continue
                declared=observation.get('peers',{}).get(protocol,{})
                expected={k:peer[k] for k in ('uid','pod_ip')}
                if declared!=expected:continue
                ipaddress.ip_address(peer['pod_ip'])
                value=scoped_association(table,38472 if protocol=='f1' else 38412,
                    pod['pod_ip'],peer['pod_ip'],remote_only=protocol=='ngap' or pod['labels'].get('component')=='du',
                    local_only=protocol=='f1' and pod['labels'].get('component')=='cu')
                unknown[protocol+'_association']=value
                available=available or value is not None
            result['binding']={**binding,'container':container,'peers':{k:{x:p[x] for x in ('uid','pod_ip')} for k,p in peers.items()}}
            result.update(provenance='LIVE' if available else 'UNAVAILABLE',observed_at=stamp,
                          reason=None if available else result['reason'])
            return result
        except (KeyError,ValueError,TypeError,AttributeError,StopIteration):return result

    @staticmethod
    def from_timestamped_logs(text, clock=time.time, max_age=120):
        """Optional OAI/srsRAN evidence parser: never re-label retained startup logs live."""
        import re
        at=clock()
        if not isinstance(text,str) or len(text)>262144:
            return unavailable('RAN application logs',at,'Bounded timestamped logs required')
        patterns={
            'f1_setup':(r'F1.?SetupResponse|F1.*Setup.*(?:complete|success|finalized)',r'F1.?SetupFailure|F1.*Setup.*fail'),
            'ng_setup':(r'NG.?SetupResponse|NG Setup.*(?:complete|success)',r'NG.?SetupFailure|NG Setup.*fail'),
            'cell_active':(r'cell.*activat(?:ed|ion successful)',r'cell.*deactivat|cell.*activation.*fail'),
            'rrc_established':(r'RRC.*SetupComplete',r'RRC.*(?:reject|setup.*fail)'),
            'ue_registered':(r'Registration complete|Received Registration Accept',r'Registration Reject'),
            'pdu_established':(r'PDU Session Establishment Accept|PDU Session establishment is successful',r'PDU Session Establishment Reject'),
            'configuration_failure':(r'(?:configuration|parser|config).*?(?:error|invalid|fail)|fatal.*config',r'configuration validated successfully')}
        values={key:None for key in patterns};times={};saw_timestamp=False
        for line in text.splitlines()[-1000:]:
            match=re.match(r'^(\d{4}-\d{2}-\d{2}T[^ ]+)\s+(.*)$',line)
            if not match:continue
            try:sample_at=timestamp(match.group(1))
            except ValueError:continue
            saw_timestamp=True
            if sample_at>at+30 or at-sample_at>max_age:continue
            for key,(positive,negative) in patterns.items():
                value=False if re.search(negative,match.group(2),re.I) else True if re.search(positive,match.group(2),re.I) else None
                if value is not None and sample_at>=times.get(key,0):values[key]=value;times[key]=sample_at
        values['user_plane_measured']=None
        return {'source':'RAN application logs','provenance':'LIVE' if times else 'STALE' if saw_timestamp else 'UNAVAILABLE',
            'observed_at':at,'value':values,'evidence_times':times,
            'note':'Timestamped application assertions; not end-to-end protocol conformance or persistent session evidence'}

class Alertmanager:
    def __init__(self,url,transport=http_json,clock=time.time):self.url,self.transport,self.clock=url.rstrip('/'),transport,clock
    def snapshot(self):
        at=self.clock()
        try:
            data=self.transport(self.url+'/api/v2/status')
            if not isinstance(data,dict) or 'versionInfo' not in data:raise ValueError('Invalid status')
            # Do not retain config.original (may contain receiver secrets).
            return {'source':'alertmanager','provenance':'LIVE','observed_at':at,'value':{'version':data['versionInfo'].get('version')}}
        except Exception:return unavailable('alertmanager',at)

    def groups(self):
        from .model import digest
        at=self.clock()
        try:
            data=self.transport(self.url+'/api/v2/alerts/groups')
            if not isinstance(data,list) or len(data)>64:raise ValueError('Invalid alert groups')
            groups=[];count=0
            for group in data:
                labels={k:v for k,v in group.get('labels',{}).items() if k in ('cluster','namespace','alertname','ns_id','job')}
                receiver=group.get('receiver',{}).get('name','unknown')
                ident=digest([labels,receiver]);alerts=[]
                for alert in group.get('alerts',[]):
                    count+=1
                    if count>256:raise ValueError('Alert limit exceeded')
                    safe={k:v for k,v in alert.get('labels',{}).items() if k in ('alertname','severity','cluster','namespace','pod','node','instance','job','ns_id','simulation')}
                    state=alert.get('status',{}).get('state','unknown')
                    starts=timestamp(alert['startsAt'])
                    alerts.append({'labels':safe,'starts_at':starts,'state':state,
                        'suppressed':state=='suppressed','group_id':ident,
                        'key':digest([safe,starts]),'provenance':'SIMULATED' if safe.get('simulation')=='true' else 'LIVE'})
                groups.append({'group_id':ident,'labels':labels,'receiver':receiver,'alerts':alerts})
            return {'source':'alertmanager','provenance':'LIVE','observed_at':at,'value':groups}
        except Exception:return unavailable('alertmanager',at,'Alert grouping API unavailable')


def service_health(snapshot, protocols=None, active_ns=None):
    """Pod readiness remains workload evidence, not end-to-end service health."""
    result={k:{'status':'UNAVAILABLE','provenance':'UNAVAILABLE'} for k in ('core','cu','du','fog','ran_protocol','ue_registration','pdu_sessions','user_plane')}
    if snapshot.get('provenance')!='LIVE' or snapshot.get('truncated'):return result
    pods=snapshot.get('value',[])
    for role in ('core','cu','du','fog'):
        matches=[]
        for pod in pods:
            labels=pod['labels']; component=labels.get('component'); name=pod['name']
            if role=='core' and name.startswith('open5gs'):matches.append(pod)
            elif role in ('cu','du','fog') and component==role and (active_ns is None or labels.get('osm.etsi.org/ns-id')==active_ns):matches.append(pod)
        if matches:result[role]={'status':'UNAVAILABLE' if any(p['ready'] is None for p in matches) else 'WORKLOAD_READY' if all(p['ready'] for p in matches) else 'DEGRADED','provenance':'LIVE','count':len(matches)}
    if protocols and protocols.get('provenance')=='LIVE':
        v=protocols['value']
        known=all(type(v.get(k)) is bool for k in ('f1_association','ngap_association'))
        result['ran_protocol']={'status':'UNAVAILABLE' if not known else 'TRANSPORT_ESTABLISHED' if v['f1_association'] and v['ngap_association'] else 'DEGRADED',
                                'provenance':'LIVE' if known else 'UNAVAILABLE'}
    return result
