"""Pure reuse adapter for existing Layer 3 snapshots. No journal/client mutation."""
from .contract import identity


def correlate_snapshot(release, snapshot):
    target=release['operation']['target']
    provenance=release['operation']['provenance']
    alarms=[a for a in snapshot.get('alarm',[]) if a.get('service')==target['ns_id']
            and a.get('cluster')==target['cluster_uid'] and a.get('provenance')==provenance]
    ids={a['alarm_id'] for a in alarms}
    incidents=[i for i in snapshot.get('incident',[]) if set(i.get('source_alarms',[])) & ids]
    return {'id':identity({'release':release['id'],'alarms':sorted(ids)}),'release_id':release['id'],
            'alarm_ids':sorted(ids),'incident_ids':sorted(i['incident_id'] for i in incidents),
            'correlation_ids':sorted({a.get('correlation_id','unknown') for a in alarms}),
            'provenance':provenance,'causality':'TEMPORAL_ASSOCIATION_ONLY',
            'proposal':'Investigate post-update health and compare release evidence; association does not prove causality',
            'rollback_proposal':{'execution':'DISABLED','status':'BLOCKED_NO_VALIDATED_PREVIOUS_RELEASE'},
            'production_calls':0}
