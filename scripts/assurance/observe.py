"""One-shot observe-only collection and evidence-derived alarms; no lifecycle client methods."""
import time
from .adapters import Prometheus, Alertmanager, Kubernetes, OSM, service_health, unavailable
from .http import ReadOnlyHTTP


def collect(cfg, state, read_kubernetes, osm_reader, *, alertmanager_url=None,
            transport=None, clock=time.time):
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
        if ns:telemetry['osm'][role]=OSM(osm_reader,clock).snapshot(ns,state['osm'].get('active_operation_id') if role=='ran' else None)
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
    # Only a declared, observed role with a Kubernetes UID can emit availability evidence.
    for pod in telemetry['kubernetes'].get('value') or []:
        role=pod['labels'].get('component')
        if role not in ('cu','du','fog') or pod['labels'].get('osm.etsi.org/ns-id')!=state['osm'].get('active_instance_id'):continue
        kind={'cu':'CUUnavailable','du':'DUUnavailable','fog':'FogApplicationUnavailable'}[role]
        events.append({**common,'alarm_type':kind,'resource':pod['name'],'node':pod['node'] or 'unknown',
            'service':state['osm'].get('active_instance_id','unknown'),
            'status':'resolved' if pod['ready'] else 'firing','observations':{'pod_ready':pod['ready'],'resource_uid':pod['uid']}})
    return telemetry,events


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
