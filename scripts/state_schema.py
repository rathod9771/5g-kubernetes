"""Validate runtime identities before any lifecycle operation."""
import re
from runtime_config import ConfigError, uuid_value


def validate_state(state, registry, context=None):
    if not isinstance(state, dict) or not isinstance(state.get('osm', {}), dict):
        raise ConfigError('Invalid runtime state mapping; reconcile explicitly')
    entries = {s['key']: s for s in registry['scenarios']}
    active = state.get('active', 'none')
    if not isinstance(active, str) or active not in entries:
        raise ConfigError('Invalid active scenario in runtime state')
    osm = state.get('osm', {})
    def identity(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise ConfigError('Invalid runtime instance/operation identity')
    for field in ('active_instance_id', 'core_instance_id', 'active_operation_id'):
        if field in osm:
            identity(osm[field])
    if 'active_scenario' in osm:
        key = osm['active_scenario']
        if not isinstance(key, str) or registry['aliases'].get(key, key) not in entries:
            raise ConfigError('Invalid recorded runtime scenario')
    if osm.get('active_instance_id') and active != registry['aliases'].get(osm.get('active_scenario'), osm.get('active_scenario')):
        raise ConfigError('Active scenario disagrees with recorded instance scenario')
    if bool(osm.get('active_instance_id')) != bool(osm.get('active_scenario')):
        raise ConfigError('Incomplete active instance/scenario pair')
    additive = osm.get('additive_instances', {})
    if not isinstance(additive, dict):
        raise ConfigError('Invalid additive_instances mapping')
    for key, value in additive.items():
        if key not in entries or not entries[key]['additive']:
            raise ConfigError('Invalid additive runtime scenario')
        identity(value)
    if 'pending_instance' in osm:
        pending = osm['pending_instance']
        if not isinstance(pending, dict) or not isinstance(pending.get('scenario'), str) or pending['scenario'] not in entries:
            raise ConfigError('Invalid pending operation mapping/scenario')
        if 'stage' in pending and pending['stage'] != 'requesting':
            raise ConfigError('Invalid pending operation stage')
        if pending.get('stage') == 'requesting':
            for field in ('name', 'nsd_uuid'):
                identity(pending.get(field))
        else:
            identity(pending.get('id'))
            identity(pending.get('operation'))
        if 'termination_operation' in pending:
            identity(pending['termination_operation'])
    for field in ('experimental_recovery', 'experimental_recovery_receipt'):
        record = osm.get(field)
        if record is None:
            continue
        if not isinstance(record, dict):
            raise ConfigError('Invalid experimental recovery record')
        key = record.get('scenario')
        if key not in entries or not entries[key].get('experimental') or entries[key].get('additive'):
            raise ConfigError('Recovery scenario is not experimental/nonadditive')
        for name in ('instance_id', 'core_id', 'nsd_id'):
            uuid_value(record.get(name), 'recovery ' + name)
        if record['core_id'] != osm.get('core_instance_id') or record['instance_id'] == record['core_id']:
            raise ConfigError('Recovery record overlaps or differs from protected core')
        if field == 'experimental_recovery':
            if (active != key or osm.get('active_instance_id') != record['instance_id'] or
                    osm.get('pending_instance') or record.get('phase') not in ('requesting','terminating','terminated','deleting')):
                raise ConfigError('Recovery journal disagrees with active state')
        elif record.get('phase') != 'completed':
            raise ConfigError('Recovery receipt is not complete')
        import math
        if type(record.get('requested_at')) not in (int,float) or not math.isfinite(record['requested_at']) or record['requested_at'] < 0:
            raise ConfigError('Invalid recovery request time')
        targets=record.get('resource_targets',[])
        if not isinstance(targets,list) or any(not isinstance(t,list) or len(t)!=2 or
                t[0] not in ('Deployment','Service','ConfigMap','PodMonitor') or
                not isinstance(t[1],str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,62}',t[1]) for t in targets):
            raise ConfigError('Invalid recovery resource identity set')
        existing=record.get('existing_termination_ops',[])
        if not isinstance(existing,list) or len(existing)>128:
            raise ConfigError('Invalid recovery operation baseline')
        for operation_id in existing:
            uuid_value(operation_id, 'recovery historical termination operation')
        if record.get('phase') != 'requesting':
            uuid_value(record.get('termination_operation'), 'recovery termination operation')
    bound = state.get('context')
    has_instances = any(osm.get(k) for k in ('active_instance_id', 'core_instance_id', 'pending_instance', 'additive_instances'))
    if bound is not None:
        if not isinstance(bound, dict):
            raise ConfigError('Invalid runtime context mapping')
        for key in ('project_id', 'vim_id', 'cluster_uid', 'namespace_uid'):
            uuid_value(bound.get(key), 'runtime context ' + key)
        for key in ('namespace', 'repo_root', 'workload_kubeconfig', 'deployment_profile', 'osm_host'):
            if not isinstance(bound.get(key), str) or not bound[key]:
                raise ConfigError('Incomplete runtime context; reconcile explicitly')
        if 'k8s_cluster_id' in bound:
            uuid_value(bound['k8s_cluster_id'], 'runtime OSM cluster identity')
    if (has_instances or context is not None) and bound is None:
        raise ConfigError('Runtime state is unbound; reconcile explicitly')
    if context is not None and bound is not None and bound != context:
        raise ConfigError('Runtime state context differs; reconcile explicitly before lifecycle operations')
    return state
