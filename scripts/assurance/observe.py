"""One-shot observe-only collection and evidence-derived alarms; no lifecycle client methods."""
import time
from .adapters import Prometheus, Alertmanager, Kubernetes, OSM, ProtocolEvidence, service_health, unavailable
from .http import ReadOnlyHTTP


def collect(cfg, state, read_kubernetes, osm_reader, *, alertmanager_url=None,
            transport=None, clock=time.time, protocol_reader=None):
    at=clock();transport=transport or ReadOnlyHTTP(budget=8)
    namespace=state['context']['namespace']
    kube=Kubernetes(read_kubernetes,clock);prom=Prometheus(cfg['PROMETHEUS_URL'],transport,clock)
    telemetry={'observed_at':at,'active_scenario':state['active'],'cluster':state['context']['cluster_uid'],
        'kubernetes':kube.snapshot(namespace),'nodes':kube.infrastructure(),'kubernetes_warnings':kube.warnings(namespace),
        'prometheus':prom.health(),'prometheus_targets':prom.targets(),
        'cpu_pressure':prom.measurement('rate(node_pressure_cpu_waiting_seconds_total[5m])','node_pressure_cpu_waiting_seconds_total')}
    telemetry['services']=service_health(telemetry['kubernetes'],active_ns=state['osm'].get('active_instance_id'))
    am=Alertmanager(alertmanager_url,transport,clock) if alertmanager_url else None
    telemetry['alertmanager']=am.snapshot() if am else unavailable('alertmanager',at,'Explicit reviewed endpoint required')
    telemetry['alert_groups']=am.groups() if am else unavailable('alertmanager',at,'Explicit reviewed endpoint required')
    telemetry['osm']={}
    for role,field in (('ran','active_instance_id'),('core','core_instance_id')):
        ns=state.get('osm',{}).get(field)
        if ns:telemetry['osm'][role]=OSM(osm_reader,clock).snapshot(ns,state['osm'].get('active_operation_id') if role=='ran' else None,
            expected_project=state['context'].get('project_id'))
    telemetry['kpm']=unavailable('KPM',at,'No live KPM adapter; availability not inferred from workload readiness')
    telemetry['rancher']=unavailable('rancher',at,'Authoritative Kubernetes state used; Rancher API unconfigured')
    telemetry['monitoring_status']='UNAVAILABLE' if telemetry['prometheus']['provenance']!='LIVE' else telemetry['prometheus_targets'].get('status','UNAVAILABLE')
    if am and telemetry['alertmanager']['provenance']!='LIVE' and telemetry['monitoring_status']!='UNAVAILABLE':
        telemetry['monitoring_status']='DEGRADED'
    stats=getattr(transport,'stats',{})
    telemetry['kubernetes_request_stats']=dict(getattr(read_kubernetes,'stats',{})) if isinstance(getattr(read_kubernetes,'stats',{}),dict) else {}
    telemetry['osm_request_stats']=dict(getattr(osm_reader,'stats',{})) if isinstance(getattr(osm_reader,'stats',{}),dict) else {}
    telemetry['monitoring_correlations']=monitoring_correlations(telemetry)
    telemetry['request_stats']=dict(stats) if isinstance(stats,dict) else {}
    events=[]
    common={'cluster':state['context']['cluster_uid'],'namespace':namespace,'timestamp':at}
    for target in telemetry['prometheus_targets'].get('value') or []:
        if not target['fresh']:continue
        labels=target['labels']
        events.append({**common,'alarm_type':'PrometheusTargetDown','source':'prometheus',
            'resource':labels.get('job','unknown')+'/'+labels.get('instance','unknown'),
            'status':'resolved' if target['health']=='up' else 'firing',
            'observations':{'metric_present':True}})
    events.append({**common,'alarm_type':'MonitoringPipelineDegraded','resource':'prometheus',
                   'status':'resolved' if telemetry['prometheus']['provenance']=='LIVE' else 'firing',
                   'observations':{'metric_present':telemetry['prometheus']['provenance']=='LIVE'}})
    for node in telemetry['nodes'].get('value') or []:
        ready=node['conditions'].get('Ready')
        if ready in ('True','False'):
            events.append({**common,'alarm_type':'KubernetesNodeNotReady','resource':node['name'],'node':node['name'],
                'status':'resolved' if ready=='True' else 'firing','observations':{'node_ready':ready=='True','metric_present':True,'resource_uid':node['uid']}})
    ran,ran_events=ran_evidence(telemetry['kubernetes'],state,protocol_reader,clock)
    telemetry['ran_evidence']=ran
    telemetry['kubernetes_request_stats']=dict(getattr(read_kubernetes,'stats',{})) if isinstance(getattr(read_kubernetes,'stats',{}),dict) else {}
    for role in ('cu','du'):
        members=[v for v in ran['workloads'] if v['role']==role]
        values=[m['availability'] for m in members]
        status='UNAVAILABLE' if not values or any(v not in ('READY','NOT_READY') for v in values) else 'DEGRADED' if 'NOT_READY' in values else 'WORKLOAD_READY'
        telemetry['services'][role]={'status':status,'provenance':'LIVE' if status!='UNAVAILABLE' else 'UNAVAILABLE','count':len(members)}
    events.extend(ran_events)
    amf,amf_events=amf_evidence(telemetry['kubernetes'],state,clock)
    telemetry['amf_evidence']=amf
    availability=amf['availability']
    telemetry['services']['amf']={'status':{'READY':'WORKLOAD_READY','NOT_READY':'DEGRADED'}.get(availability,'UNAVAILABLE'),
        'provenance':amf['provenance'],'availability':availability}
    events.extend(amf_events)
    events.extend(osm_failure_events(telemetry['osm'],state,clock))
    cpu,cpu_events=cpu_contention_evidence(telemetry,state,clock)
    capacity,capacity_events=capacity_evidence(telemetry,state,clock)
    telemetry['cpu_contention']=cpu;telemetry['capacity']=capacity
    events.extend(cpu_events);events.extend(capacity_events)
    # Fog behavior is retained, but unknown readiness is never a failure assertion.
    for pod in telemetry['kubernetes'].get('value') or []:
        role=pod['labels'].get('component')
        if role!='fog' or pod['ready'] is None or telemetry['kubernetes'].get('truncated') or pod['labels'].get('osm.etsi.org/ns-id')!=state['osm'].get('active_instance_id'):continue
        kind={'cu':'CUUnavailable','du':'DUUnavailable','fog':'FogApplicationUnavailable'}[role]
        events.append({**common,'alarm_type':kind,'resource':pod['name'],'node':pod['node'] or 'unknown',
            'service':state['osm'].get('active_instance_id','unknown'),
            'status':'resolved' if pod['ready'] else 'firing','observations':{'pod_ready':pod['ready'],'resource_uid':pod['uid']}})
    return telemetry,events



def ran_evidence(snapshot, state, protocol_reader=None, clock=time.time):
    """Owned workload and optional scoped transport evidence; no live exec here.

    The injected trusted observer returns the ProtocolEvidence.scoped contract.
    Default is UNAVAILABLE, never port-only/legacy evidence. Expected apps come
    from the existing scenario registry, not a second deployment-state file.
    """
    from scenario_registry import load_registry
    at=clock();context=state.get('context',{});ns=state.get('osm',{}).get('active_instance_id')
    result={'provenance':'UNAVAILABLE','observed_at':at,'workloads':[],
            'protocols':{},'note':'SCTP transport only; setup, registration and sessions remain unknown'}
    try:
        entry=next(s for s in load_registry()['scenarios'] if s['key']==state['active'])
        expected=[(c['release_name'],'cu' if c['kdu']=='cu' else 'du') for c in entry['charts'] if c['kdu'] in ('cu','du','macro','small')]
    except (ValueError,KeyError,StopIteration):expected=[]
    complete=(snapshot.get('provenance')=='LIVE' and not snapshot.get('truncated') and
              snapshot.get('namespace')==context.get('namespace') and
              all(isinstance(context.get(k),str) and context[k] for k in ('namespace','namespace_uid','cluster_uid')) and
              isinstance(ns,str) and ns and ns!='unknown' and ns!=state.get('osm',{}).get('core_instance_id') and
              0<=at-snapshot.get('observed_at',0)<=120)
    pods=snapshot.get('value') or [];owned=[]
    for app,role in expected:
        matches=[p for p in pods if p.get('namespace')==context.get('namespace') and
                 p['labels'].get('app')==app and p['labels'].get('component')==role and
                 p['labels'].get('osm.etsi.org/ns-id')==ns and p.get('uid')]
        pod=matches[0] if complete and len(matches)==1 else None
        availability=('READY' if pod['ready'] is True else 'NOT_READY' if pod['ready'] is False else 'UNKNOWN') if pod else 'MISSING' if complete and not matches else 'UNKNOWN'
        item={'app':app,'role':role,'availability':availability,'provenance':'LIVE' if complete else 'UNAVAILABLE',
              'pod_uid':pod['uid'] if pod else None}
        result['workloads'].append(item)
        if pod:owned.append(pod)
    result['provenance']='LIVE' if complete else 'UNAVAILABLE'
    # First-phase topology contract: one CU and one DU. Ambiguity is unknown.
    cus=[p for p in owned if p['labels'].get('component')=='cu']
    dus=[p for p in owned if p['labels'].get('component')=='du']
    amfs=[p for p in pods if p['labels'].get('osm.etsi.org/ns-id')==state.get('osm',{}).get('core_instance_id') and
          p.get('namespace')==context.get('namespace') and
          (p['labels'].get('component')=='amf' or p['labels'].get('app.kubernetes.io/name') in ('amf','open5gs-amf'))]
    if complete and len(cus)==len(dus)==1:
        for pod,peer in ((cus[0],dus[0]),(dus[0],cus[0])):
            peers={'f1':peer}
            if pod is cus[0] and len(amfs)==1:peers['ngap']=amfs[0]
            observation=None
            if protocol_reader is not None:
                try:
                    from copy import deepcopy
                    observation=protocol_reader(deepcopy(pod),deepcopy(peers),deepcopy(context),ns)
                except Exception:pass  # Never persist adapter errors or raw SCTP data.
            result['protocols'][pod['uid']]=ProtocolEvidence.scoped(observation,pod,peers,context,ns,clock)
    def assoc(pod,key):
        sample=result['protocols'].get(pod['uid'],{})
        if sample.get('provenance')!='LIVE' or not 0<=clock()-sample.get('observed_at',0)<=120:return None
        return sample.get('value',{}).get(key)
    f1=None
    if len(cus)==len(dus)==1:
        sides=[assoc(p,'f1_association') for p in (cus[0],dus[0])]
        if sides[0] is sides[1] and type(sides[0]) is bool:f1=sides[0]
    ngap=assoc(cus[0],'ngap_association') if len(cus)==1 else None
    result['associations']={'f1':{'value':f1,'status':'ESTABLISHED' if f1 is True else 'ABSENT' if f1 is False else 'UNKNOWN'},
                            'ngap':{'value':ngap,'status':'ESTABLISHED' if ngap is True else 'ABSENT' if ngap is False else 'UNKNOWN'}}
    if complete and not 0<=clock()-snapshot['observed_at']<=120:
        result['provenance']='STALE'
        for item in result['workloads']:item.update(availability='UNKNOWN',provenance='STALE')
        return result,[]
    events=[]
    for pod in owned:
        role=pod['labels']['component'];ready=pod['ready']
        # Evidence timestamps, not receipt time, drive persistence and deduplication.
        stamps=[snapshot['observed_at']]
        for sample in result['protocols'].values():
            if sample['provenance']=='LIVE':stamps.append(sample['observed_at'])
        common={'cluster':context['cluster_uid'],'namespace':context['namespace'],'timestamp':min(stamps),
                'service':ns,'node':pod.get('node') or 'unknown','source':'scoped_ran_observer',
                'observations':{'pod_ready':ready,'resource_uid':pod['uid'],'namespace_uid':context['namespace_uid'],'ownership_verified':True,
                                'f1_association':f1,'ngap_association':ngap if role=='cu' else None}}
        identity=pod['name']+'/'+pod['uid']
        if type(ready) is bool:
            events.append({**common,'alarm_type':'CUUnavailable' if role=='cu' else 'DUUnavailable',
                           'resource':identity,'status':'resolved' if ready else 'firing'})
        if type(f1) is bool:
            events.append({**common,'alarm_type':'F1AssociationLost','resource':identity+'/f1',
                           'status':'resolved' if f1 else 'firing'})
        if role=='cu' and type(ngap) is bool:
            # Core service health is deliberately unknown: AMF workload readiness
            # cannot satisfy the NGAP policy's independent core-health requirement.
            events.append({**common,'alarm_type':'NGAPAssociationLost','resource':identity+'/ngap',
                           'status':'resolved' if ngap else 'firing'})
    return result,events



def amf_evidence(snapshot, state, clock=time.time):
    """Reference AMF metadata contract; workload health is not NAS acceptance."""
    context=state.get('context',{});osm=state.get('osm',{});core=osm.get('core_instance_id')
    at=clock();result={'source':'kubernetes AMF workload','provenance':'UNAVAILABLE',
        'observed_at':snapshot.get('observed_at',at),'availability':'UNKNOWN',
        'application_health':'UNAVAILABLE','ue_registration':'UNAVAILABLE'}
    if (snapshot.get('provenance')!='LIVE' or snapshot.get('truncated') or
        snapshot.get('namespace')!=context.get('namespace') or
        not all(isinstance(context.get(k),str) and context[k] for k in ('namespace','namespace_uid','cluster_uid')) or
        not isinstance(core,str) or not core or core=='unknown' or core==osm.get('active_instance_id') or
        not 0<=at-snapshot.get('observed_at',0)<=120):return result,[]
    candidates=[p for p in snapshot.get('value') or [] if p.get('namespace')==context['namespace'] and
        p['labels'].get('osm.etsi.org/ns-id')==core and
        p['labels'].get('app.kubernetes.io/name') in ('amf','open5gs-amf')]
    if not candidates:
        result.update(availability='MISSING',provenance='LIVE');return result,[]
    if len(candidates)!=1:return result,[]
    pod=candidates[0];labels=pod['labels'];release=labels.get('app.kubernetes.io/instance')
    if (not pod.get('uid') or labels.get('component','amf')!='amf' or
        not isinstance(release,str) or not 1<=len(release)<=200):return result,[]
    ready=pod['ready'];availability='READY' if ready is True else 'NOT_READY' if ready is False else 'UNKNOWN'
    result.update(availability=availability,provenance='LIVE',pod_uid=pod['uid'],
        namespace=context['namespace'],namespace_uid=context['namespace_uid'],cluster_uid=context['cluster_uid'],
        ns_id=core,application=labels['app.kubernetes.io/name'],release=release)
    if type(ready) is not bool:return result,[]
    event={'alarm_type':'AMFUnavailable','cluster':context['cluster_uid'],'namespace':context['namespace'],
        'resource':pod['name']+'/'+pod['uid'],'service':core,'node':pod.get('node') or 'unknown',
        'source':'owned_amf_workload','timestamp':snapshot['observed_at'],'status':'resolved' if ready else 'firing',
        'observations':{'pod_ready':ready,'metric_present':True,'service_degraded':None,
            'ownership_verified':True,'namespace_uid':context['namespace_uid'],'resource_uid':pod['uid']}}
    return result,[event]



def osm_failure_events(snapshots, state, clock=time.time):
    """Fresh confirmed FAILED assertions, never API failures or lifecycle writes.

    Only the existing recorded active_operation_id is tracked. Historical source
    transition time is retained separately from a fresh authenticated read.
    """
    context=state.get('context',{});osm=state.get('osm',{});sample=snapshots.get('ran',{})
    ns= osm.get('active_instance_id');operation_id=osm.get('active_operation_id')
    if (sample.get('provenance')!='LIVE' or not 0<=clock()-sample.get('observed_at',0)<=120 or
        not all(isinstance(context.get(k),str) and context[k] for k in ('cluster_uid','namespace','namespace_uid','project_id')) or
        not ns or not operation_id):return []
    value=sample.get('value') or {};classification=value.get('operation_classification')
    if (value.get('ns_id')!=ns or value.get('operation_id')!=operation_id or
        value.get('ownership_verified') is not True or value.get('project_id')!=context['project_id'] or
        value.get('operation_provenance')!='LIVE' or classification not in ('FAILED','COMPLETED')):return []
    operation=value['operation']
    return [{'alarm_type':'OSMLifecycleOperationFailed','cluster':context['cluster_uid'],'namespace':context['namespace'],
        'service':ns,'resource':'osm-operation/'+operation_id,'source':'osm','timestamp':sample['observed_at'],
        'status':'firing' if classification=='FAILED' else 'resolved',
        'observations':{'operation_failed':classification=='FAILED','osm_trustworthy':True,'ownership_verified':True,
            'operation_id':operation_id,'namespace_uid':context['namespace_uid'],
            'operation_started_at':operation['startTime'],'operation_state_entered_at':operation['statusEnteredTime']}}]


def monitoring_correlations(telemetry):
    """Group endpoint evidence without merging or renumbering durable incidents."""
    from .model import digest
    groups={}
    for target in telemetry.get('prometheus_targets',{}).get('value') or []:
        if target['health']=='up' or not target['fresh']:continue
        endpoint=target['labels'].get('instance','unknown');host=endpoint.rsplit(':',1)[0]
        group=groups.setdefault(host,{'group_id':digest(['monitoring_endpoint',telemetry.get('cluster','unknown'),host]),
            'host':host,'targets':[],'classification':'MONITORING_ENDPOINT_UNAVAILABLE',
            'control_plane_process_health':'UNAVAILABLE','kubernetes_service_degraded':'UNAVAILABLE',
            'note':'Scrape failure alone does not establish process outage or service degradation',
            'provenance':'LIVE','observed_at':telemetry['observed_at']})
        group['targets'].append({'job':target['labels'].get('job'),'endpoint':endpoint,
                                'failure_class':target.get('failure_class')})
    return list(groups.values())


def _fresh_evidence(sample, now):
    return (sample.get('provenance')=='LIVE' and not sample.get('truncated') and
            type(sample.get('observed_at')) in (int,float) and 0<=now-sample['observed_at']<=120)


def _resource_scope(telemetry, state, now):
    from scenario_registry import load_registry
    ctx=state.get('context',{});ns=state.get('osm',{}).get('active_instance_id')
    if not all(isinstance(ctx.get(k),str) and ctx[k] for k in ('namespace','namespace_uid','cluster_uid')) or not isinstance(ns,str) or not ns or ns=='unknown' or ns==state.get('osm',{}).get('core_instance_id'):return []
    snapshot=telemetry.get('kubernetes',{})
    if not _fresh_evidence(snapshot,now) or snapshot.get('namespace')!=ctx['namespace']:return []
    rows=snapshot.get('value') or []
    if any(not p.get('uid') for p in rows) or len({p['uid'] for p in rows})!=len(rows) or len({p['name'] for p in rows})!=len(rows):return []
    try:apps={c['release_name']:('du' if c['kdu'] in ('macro','small') else c['kdu']) for s in load_registry()['scenarios'] if s['key']==state['active'] for c in s['charts']}
    except (ValueError,KeyError):return []
    return [p for p in snapshot.get('value') or [] if p.get('namespace')==ctx['namespace'] and p.get('uid') and
            p['labels'].get('osm.etsi.org/ns-id')==ns and p['labels'].get('app') in apps and
            p['labels'].get('component')==apps[p['labels']['app']]]


def _resource_event(kind, resource, uid, state, at, **observations):
    ctx=state['context']
    return {'alarm_type':kind,'resource':resource,'cluster':ctx['cluster_uid'],'namespace':ctx['namespace'],
            'service':state['osm']['active_instance_id'],'timestamp':at,'source':'scoped_resource_observer',
            'observations':{'resource_uid':uid,'namespace_uid':ctx['namespace_uid'],'ownership_verified':True,
                            'metric_present':True,'service_degraded':None,**observations}}


def cpu_contention_evidence(telemetry, state, clock=time.time):
    """PSI waiting ratio is contention; utilization/throttling are different evidence."""
    import math
    from urllib.parse import urlsplit
    now=clock();result={'provenance':'UNAVAILABLE','observed_at':now,'value':[],
        'metric':'node_pressure_cpu_waiting_seconds_total','unit':'ratio','window_seconds':300,
        'utilization':'NOT_COLLECTED','throttling':'NOT_COLLECTED'};events=[]
    nodes=telemetry.get('nodes',{});pressure=telemetry.get('cpu_pressure',{});targets=telemetry.get('prometheus_targets',{})
    owned=_resource_scope(telemetry,state,now)
    if not owned or not all(_fresh_evidence(s,now) for s in (nodes,pressure,targets)):return result,events
    inventory=nodes.get('value') or []
    if len({n.get('uid') for n in inventory})!=len(inventory) or len({n.get('name') for n in inventory})!=len(inventory):return result,events
    seen=set()
    for node in inventory:
        if not node.get('uid') or not any(p.get('node')==node['name'] for p in owned):continue
        samples=[]
        for sample in pressure.get('value') or []:
            labels=sample.get('labels',{});instance=labels.get('instance')
            if labels.get('job')!='node-exporter' or not instance or labels.get('cluster',state['context']['cluster_uid'])!=state['context']['cluster_uid']:continue
            host=urlsplit('http://'+instance).hostname
            if host not in [node['name'],*node.get('addresses',[])]:continue
            if labels.get('node',node['name'])!=node['name']:continue
            matching=[t for t in targets.get('value') or [] if t['labels'].get('job')=='node-exporter' and
                      t['labels'].get('instance')==instance and t.get('health')=='up' and t.get('fresh') and
                      0<=now-t.get('last_scrape',0)<=120 and t['labels'].get('node',node['name'])==node['name'] and
                      all(labels.get(k)==t['labels'].get(k) for k in ('cluster','namespace','pod','service','node','container'))]
            value=sample.get('value');stamp=sample.get('at')
            if (len(matching)==1 and type(value) in (int,float) and math.isfinite(value) and 0<=value<=1 and
                type(stamp) in (int,float) and 0<=now-stamp<=120):samples.append(sample)
        if len(samples)!=1 or node['uid'] in seen:continue
        seen.add(node['uid']);sample=samples[0]
        # Threshold is the existing reviewed example rule's laboratory candidate.
        high=sample['value']>.20
        result['value'].append({'node':node['name'],'node_uid':node['uid'],'ratio':sample['value'],'at':sample['at'],'high':high})
        event=_resource_event('HostCPUContentionHigh',node['name']+'/'+node['uid'],node['uid'],state,
            min(sample['at'],nodes['observed_at'],telemetry['kubernetes']['observed_at']),host_pressure=sample['value'])
        event.update(status='firing' if high else 'resolved',node=node['name']);events.append(event)
    if result['value']:result['provenance']='LIVE'
    return result,events


def capacity_evidence(telemetry, state, clock=time.time):
    """Prove a shortage from requests + fresh scheduler evidence, never aggregate usage.

    Namespace allocations are a lower bound. Their residual is an UPPER bound
    on headroom; fitting it cannot establish cluster schedulability.
    """
    from .adapters import resource_quantity
    now=clock();result={'provenance':'UNAVAILABLE','observed_at':now,'value':[],
        'accounting':'requested resources, not usage; namespace allocations are a lower bound'};events=[]
    snapshot=telemetry.get('kubernetes',{});nodes=telemetry.get('nodes',{});warnings=telemetry.get('kubernetes_warnings',{})
    owned=_resource_scope(telemetry,state,now)
    if not owned or not all(_fresh_evidence(s,now) for s in (nodes,warnings)):return result,events
    inventory=nodes.get('value') or []
    if any(not n.get('uid') for n in inventory) or len({n['uid'] for n in inventory})!=len(inventory) or len({n['name'] for n in inventory})!=len(inventory):return result,events
    for pod in owned:
        row={'pod_uid':pod['uid'],'status':'UNKNOWN'};result['value'].append(row)
        resource=pod['name']+'/'+pod['uid']
        common_time=min(snapshot['observed_at'],nodes['observed_at'],warnings['observed_at'])
        if pod.get('node') and pod.get('pod_scheduled')=='True':
            if any(n['name']==pod['node'] and n['conditions'].get('Ready')=='True' for n in inventory):
                row['status']='PLACEMENT_CONFIRMED'
                event=_resource_event('InsufficientClusterCapacity',resource,pod['uid'],state,common_time)
                event['status']='resolved';events.append(event)
            continue
        if pod.get('phase')!='Pending' or pod.get('node') or pod.get('requests') is None:continue
        schedule=pod.get('scheduling',{})
        if (schedule.get('affinity') or schedule.get('topologySpreadConstraints') or schedule.get('schedulingGates') or
            schedule.get('schedulerName') not in (None,'default-scheduler') or
            any(v.get('persistentVolumeClaim') for v in schedule.get('volumes') or [])):continue
        if not inventory or any(n['conditions'].get('Ready')!='True' or
            any(n['conditions'].get(k)!='False' for k in ('MemoryPressure','DiskPressure','PIDPressure')) for n in inventory):continue
        eligible=[];unknown=False
        for node in inventory:
            if node.get('scheduling_labels') is None or node.get('taints') is None:unknown=True;break
            if node.get('unschedulable'):continue
            if any(node['scheduling_labels'].get(k)!=v for k,v in (schedule.get('nodeSelector') or {}).items()):continue
            def tolerates(taint):
                return any(t.get('operator','Equal') in ('Equal','Exists') and
                    (not t.get('effect') or t['effect']==taint.get('effect')) and
                    (t.get('key')==taint.get('key') or t.get('operator')=='Exists' and not t.get('key')) and
                    (t.get('operator')=='Exists' or t.get('value','')==taint.get('value','')) for t in schedule.get('tolerations') or [])
            if any(t.get('effect') in ('NoSchedule','NoExecute') and not tolerates(t) for t in node['taints']):continue
            try:available={k:resource_quantity(node['allocatable'][k],cpu=k=='cpu') for k in ('cpu','memory')}
            except (ValueError,KeyError,TypeError):unknown=True;break
            allocations=[p for p in snapshot.get('value') or [] if p.get('node')==node['name'] and p.get('phase') not in ('Succeeded','Failed')]
            if any(p.get('requests') is None for p in allocations):unknown=True;break
            residual={'cpu':(round(available['cpu']*1000)-sum(round(p['requests']['cpu']*1000) for p in allocations))/1000,
                      'memory':available['memory']-sum(p['requests']['memory'] for p in allocations)}
            eligible.append({'node':node['name'],'uid':node['uid'],'allocatable':available,'headroom_upper_bound':residual,
                'shortage':[k for k in available if pod['requests'][k]>0 and pod['requests'][k]>residual[k]]})
        if unknown:continue
        if not eligible:row['status']='CONSTRAINTS_UNSATISFIED';continue
        row.update(eligible_nodes=eligible,requests=pod['requests'])
        if not all(n['shortage'] for n in eligible):row['status']='FIT_NOT_PROVEN';continue
        observed=[w for w in warnings.get('value') or [] if w.get('reason')=='FailedScheduling' and
            w.get('scheduler') in ('default-scheduler','kube-scheduler') and w.get('provenance')=='LIVE' and
            w.get('namespace')==state['context']['namespace'] and w.get('resource',{}).get('uid')==pod['uid'] and
            w.get('resource',{}).get('kind')=='Pod' and w['resource'].get('name')==pod['name'] and
            0<=now-w.get('at',0)<=120 and any(r in w.get('capacity_resources',[]) for n in eligible for r in n['shortage'])]
        if not observed:continue
        row['status']='PROVEN_SHORTAGE'
        event=_resource_event('InsufficientClusterCapacity',resource,pod['uid'],state,
            min(common_time,max(w['at'] for w in observed)))
        event['status']='firing';events.append(event)
    if result['value']:result['provenance']='LIVE'
    return result,events
