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
    for field in ('active_instance_id', 'core_instance_id'):
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
