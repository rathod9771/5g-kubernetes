"""Shared lifecycle coordination for the dashboard and a future CD agent.

One lock (<RUNTIME_DIR>/.lifecycle.lock) and one state file (active-ran.yaml)
serialize every lifecycle change; CD adds records to that state, never a second
one. Catalog publication never deploys. Only the recorded active primary RAN may
be upgraded, in place, on its existing NS through OSM KDU actions.

Phase P1: exercised only against the offline NBI simulation. No service or
entry point calls process_release; the OSM KDU upgrade path is unverified live,
so P4_KDU_UPGRADE_APPROVAL keeps every in-place KDU upgrade disabled.
"""
import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path

import yaml

from local_safety import LockTimeout, atomic_write, exclusive_lock
from state_schema import validate_state

LOCK_NAME = '.lifecycle.lock'
HOLDER_NAME = '.lifecycle.holder'
LEDGER_NAME = 'release-ledger.json'
LEDGER_LIMIT = 500
TERMINAL = ('COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'FAILED_TEMP')
# CU before DUs so DUs reconnect F1 to the upgraded CU; auxiliary KDUs last.
KDU_ORDER = {'cu': 0, 'du': 1, 'macro': 1, 'small': 1}

# P4 release gate: in-place RAN/Core KDU upgrades (and the rollbacks that undo them)
# stay disabled until the isolated P4 experiment has validated OSM v19 KDU upgrades.
# Opening it is a reviewed code change to this constant, shipped through the attested
# release pipeline. It is deliberately never read from the environment, the release
# policy, the runtime state or any configuration file.
P4_KDU_UPGRADE_APPROVAL = None
_P4_APPROVAL_FORMAT = {'p4_report_sha256': re.compile(r'[0-9a-f]{64}'),
                       'osm_lcm_digest': re.compile(r'sha256:[0-9a-f]{64}'),
                       'approved_commit': re.compile(r'[0-9a-f]{40}')}
KDU_MODEL = re.compile(r'(?P<root>/[A-Za-z0-9_./-]+)/(?P<id>[0-9a-f-]{36}):(?P<rev>[1-9][0-9]*)/'
                       r'(?P<package>[A-Za-z0-9_-]+)/helm-chart-v3s/(?P<kdu>[A-Za-z0-9_-]+)')


class LifecycleError(ValueError):
    pass


class LifecycleBusy(LifecycleError):
    pass


class ReleaseRejected(LifecycleError):
    pass


class PublishError(LifecycleError):
    def __init__(self, message, published):
        super().__init__(message)
        self.published = published


class StaleActiveState(LifecycleError):
    pass


class UpdateFailed(LifecycleError):
    pass


class UpdateUncertain(LifecycleError):
    pass


class UpgradeNotApproved(LifecycleError):
    pass


def require_p4_approval():
    """Raise unless the P4 gate in this module has been opened by a reviewed release."""
    approval = P4_KDU_UPGRADE_APPROVAL
    if (type(approval) is not dict or set(approval) != set(_P4_APPROVAL_FORMAT) or
            any(type(approval[k]) is not str or not p.fullmatch(approval[k]) for k, p in _P4_APPROVAL_FORMAT.items())):
        raise UpgradeNotApproved('In-place KDU upgrades are disabled until P4 validation is approved')
    return approval


def lock_path(state_path):
    return Path(state_path).parent / LOCK_NAME


@contextlib.contextmanager
def lifecycle_lock(state_path, holder, timeout=None):
    """The dashboard's lock file; holder metadata is advisory only."""
    directory = Path(state_path).parent
    marker = directory / HOLDER_NAME
    try:
        lock = exclusive_lock(directory / LOCK_NAME, timeout=timeout)
        lock.__enter__()
    except LockTimeout:
        raise LifecycleBusy('Lifecycle operation in progress' + _holder_text(marker)) from None
    try:
        atomic_write(marker, json.dumps({'holder': holder, 'pid': os.getpid(), 'since': time.time()}).encode())
        yield
    finally:
        with contextlib.suppress(OSError):
            marker.unlink()
        lock.__exit__(None, None, None)


def _holder_text(marker):
    try:
        holder = json.loads(marker.read_text()).get('holder')
    except (OSError, ValueError, AttributeError):
        return ''
    return ' (holder: ' + holder + ')' if isinstance(holder, str) and len(holder) < 64 else ''


def read_state(state_path, registry, context):
    try:
        state = yaml.safe_load(Path(state_path).read_text())
    except FileNotFoundError:
        raise LifecycleError('Missing runtime state; initialize explicitly before lifecycle operations') from None
    except yaml.YAMLError:
        raise LifecycleError('Malformed runtime state; reconcile explicitly') from None
    validate_state(state, registry, context)
    state.setdefault('osm', {})
    return state


def write_state(state_path, state, registry, context):
    validate_state(state, registry, context)
    atomic_write(state_path, yaml.safe_dump(state).encode())


def blocking_reason(state):
    osm = state.get('osm', {})
    if osm.get('experimental_recovery'):
        return 'Experimental recovery is in progress'
    if osm.get('pending_instance'):
        return 'A dashboard instance operation is pending reconciliation'
    if osm.get('pending_update'):
        return 'A CD update is pending reconciliation (stage ' + str(osm['pending_update'].get('stage')) + ')'
    return None


def primary_instances(instances, registry):
    """NS records named for a primary (non-additive) RAN scenario."""
    names = {'ran-' + s['key'] for s in registry['scenarios'] if not s.get('additive') and s['key'] != 'none'}
    return [i for i in instances if isinstance(i, dict) and i.get('name', i.get('nsName')) in names]


def verify_release(release, blobs, registry, policy):
    """Reject before any OSM call: provenance, target, scenario identity and digests."""
    if not isinstance(release, dict) or release.get('schema') != 1:
        raise ReleaseRejected('Unsupported release manifest')
    if release.get('ci_status') != 'success':
        raise ReleaseRejected('CI did not succeed for this release')
    if release.get('branch') != policy['branch']:
        raise ReleaseRejected('Release branch is not authorized for CD')
    if release.get('target') != policy['target']:
        raise ReleaseRejected('Release targets another environment')
    if not isinstance(release.get('commit'), str) or not re.fullmatch(r'[0-9a-f]{40}', release['commit']):
        raise ReleaseRejected('Release commit is not a full Git SHA')
    entries = {s['key']: s for s in registry['scenarios'] + registry.get('installer_components', [])}
    scenarios = release.get('scenarios')
    if not isinstance(scenarios, dict) or not scenarios:
        raise ReleaseRejected('Release names no scenarios')
    verified = []
    for key in sorted(scenarios):
        entry, item = entries.get(key), scenarios[key]
        if (not entry or entry['generation_status'] != 'ready' or entry.get('deployment_mechanism') != 'osm'
                or not isinstance(item, dict) or not isinstance(item.get('artifacts'), dict)):
            raise ReleaseRejected('Release scenario is unknown or not ready: ' + str(key))
        expected = {entry['knf_package'] + '.tar.gz', entry['nsd_package'] + '.tar.gz'}
        if set(item['artifacts']) != expected:
            raise ReleaseRejected('Release artifacts differ from registry packages: ' + key)
        content = {}
        for name, digest in item['artifacts'].items():
            blob = blobs.get(name)
            if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or
                    not isinstance(blob, bytes) or hashlib.sha256(blob).hexdigest() != digest):
                raise ReleaseRejected('Artifact digest mismatch: ' + name)
            content[name[:-len('.tar.gz')]] = blob
        verified.append((key, entry, content))
    return verified


def _ledger(state_path, record):
    path = Path(state_path).parent / LEDGER_NAME
    try:
        entries = json.loads(path.read_text())
    except FileNotFoundError:
        entries = []
    if not isinstance(entries, list):
        raise LifecycleError('Malformed release ledger; reconcile explicitly')
    entries.append(dict(record, recorded_at=time.time()))
    atomic_write(path, json.dumps(entries[-LEDGER_LIMIT:], sort_keys=True).encode())


def publish(osm, verified, state_path, commit):
    """Catalog only: every KNF before any NSD, as onboarding does. Never deploys."""
    published = {}
    try:
        for kind, field in (('knf', 'knf_package'), ('ns', 'nsd_package')):
            for key, entry, content in verified:
                published.setdefault(key, {})[kind] = osm.publish(kind, entry[field], content[entry[field]])
    except Exception as error:
        _ledger(state_path, {'commit': commit, 'event': 'publish-failed', 'published': published})
        raise PublishError('Catalog publication failed; no deployment attempted: ' + str(error), published) from None
    _ledger(state_path, {'commit': commit, 'event': 'published', 'published': published})
    return published


def _ns_idle(ns, key):
    return (ns.get('name', ns.get('nsName')) == 'ran-' + key and ns.get('nsState') == 'READY' and
            ns.get('operational-status') == 'running' and ns.get('currentOperation', 'IDLE') in ('IDLE', None))


def confirm_active(osm, state, entry, published, registry):
    """Prove with OSM that the recorded active NS is the only primary RAN and is idle."""
    key, records = entry['key'], state['osm']
    instance_id = records.get('active_instance_id')
    if not instance_id:
        raise StaleActiveState('Active scenario has no recorded OSM instance')
    primaries = primary_instances(osm.list_ns(), registry)
    if [p.get('_id') for p in primaries] != [instance_id]:
        raise StaleActiveState('Single-primary-RAN invariant cannot be confirmed against OSM')
    ns = osm.get_ns(instance_id)
    if not _ns_idle(ns, key):
        raise StaleActiveState('Active NS is not the idle, running ' + key + ' instance')
    if ns.get('nsd-id') != published['ns']['id']:
        raise StaleActiveState('Active NS was not instantiated from the published NSD')
    vnfrs = osm.vnf_instances(instance_id)
    if len(vnfrs) != 1 or vnfrs[0].get('vnfd-id') != published['knf']['id']:
        raise StaleActiveState('Active NS does not use exactly the published KNF')
    deployed = ns.get('_admin', {}).get('deployed', {}).get('K8s', [])
    expected = {c['kdu'] for c in entry['charts']}
    if not isinstance(deployed, list) or sorted(k.get('kdu-name', '') for k in deployed) != sorted(expected):
        raise StaleActiveState('Deployed KDUs differ from the scenario charts')
    target = published['knf']['revision']
    kdus = []
    for kdu in deployed:
        match = KDU_MODEL.fullmatch(str(kdu.get('kdu-model', '')))
        if (not match or match['id'] != published['knf']['id'] or match['package'] != entry['knf_package']
                or match['kdu'] != kdu['kdu-name'] or not kdu.get('kdu-instance') or not kdu.get('member-vnf-index')):
            raise StaleActiveState('Deployed KDU model is not a revision of the published KNF')
        kdus.append({'name': kdu['kdu-name'], 'member_index': kdu['member-vnf-index'],
                     'instance': kdu['kdu-instance'],
                     'model': '{}/{}:{}/{}/helm-chart-v3s/{}'.format(match['root'], match['id'], target,
                                                                    match['package'], match['kdu'])})
    kdus.sort(key=lambda k: (KDU_ORDER.get(k['name'], 2), k['name']))
    # OSM leaves the VNF record revision unchanged after KDU upgrades; trust our
    # record only when it names this exact instance.
    release = records.get('active_release')
    running = (release['knf_revision'] if release and release.get('instance_id') == instance_id
               else vnfrs[0].get('revision', 1))
    if running >= target:
        return None
    return {'instance_id': instance_id, 'kdus': kdus, 'from_revision': running, 'to_revision': target,
            'commit': release.get('commit') if release and release.get('instance_id') == instance_id else None}


def wait_operation(osm, operation, timeout, clock, interval=5):
    deadline = clock.monotonic() + timeout
    while clock.monotonic() < deadline:
        state = osm.operation_state(operation)
        if state in TERMINAL:
            return state
        clock.sleep(interval)
    return 'TIMEOUT'


def wait_healthy(observer, entry, plan, since, policy, clock):
    deadline = clock.monotonic() + policy.get('health_timeout', 600)
    detail = 'health not evaluated'
    while True:
        healthy, detail = observer.health(entry, plan['kdus'], since)
        if healthy or clock.monotonic() >= deadline:
            return healthy, detail
        clock.sleep(policy.get('health_interval', 10))


class _Update:
    """Persist every step before and after each OSM mutation."""
    def __init__(self, state_path, state, registry, context):
        self.path, self.state, self.registry, self.context = state_path, state, registry, context

    @property
    def record(self):
        return self.state['osm']['pending_update']

    def save(self, **changes):
        self.record.update(changes)
        write_state(self.path, self.state, self.registry, self.context)

    def finish(self, **records):
        self.state['osm'].pop('pending_update', None)
        self.state['osm'].update(records)
        write_state(self.path, self.state, self.registry, self.context)


def _uncertain(update, message):
    update.save(stage='uncertain')
    raise UpdateUncertain(message + '; pending update retained for explicit reconciliation')


def _rollback(osm, observer, update, entry, plan, since, policy, clock):
    update.save(stage='rolling-back')
    by_name = {k['name']: k for k in plan['kdus']}
    for name in reversed(list(update.record['upgraded'])):
        kdu = by_name[name]
        try:
            operation = osm.kdu_action(plan['instance_id'], kdu['member_index'], name, 'rollback', {})
            outcome = wait_operation(osm, operation, policy.get('op_timeout', 600), clock)
        except Exception:
            _uncertain(update, 'Rollback of ' + name + ' has an unknown outcome')
        if outcome != 'COMPLETED':
            _uncertain(update, 'Rollback of ' + name + ' ended ' + outcome)
        update.record['upgraded'].remove(name)
        update.save()
    healthy, detail = wait_healthy(observer, entry, plan, since, policy, clock)
    if not healthy:
        _uncertain(update, 'Previous release is unhealthy after rollback: ' + detail)
    update.finish()


def _helm_revisions(observer, instances):
    """Observed Helm revisions per KDU; never guessed."""
    try:
        revisions = {kdu: observer.helm_revision(instance) for kdu, instance in instances.items()}
    except Exception:
        raise LifecycleError('Helm release revisions cannot be observed') from None
    if not revisions or any(type(v) is not int or v < 1 for v in revisions.values()):
        raise LifecycleError('Helm release revisions are missing or invalid')
    return revisions


def _apply(osm, observer, state_path, state, registry, context, entry, plan, commit, policy, clock):
    require_p4_approval()               # before any state write or OSM action, whoever the caller is
    key = entry['key']
    instances = {k['name']: k['instance'] for k in plan['kdus']}
    try:
        helm = _helm_revisions(observer, instances)
    except LifecycleError as error:
        raise StaleActiveState(str(error) + '; previous release unknown, nothing was modified') from None
    previous = {'scenario': key, 'instance_id': plan['instance_id'], 'commit': plan['commit'],
                'knf_revision': plan['from_revision'], 'helm': helm}
    # Durable before any OSM action: recovery reads the previous release from disk.
    state['osm']['pending_update'] = {'scenario': key, 'instance_id': plan['instance_id'], 'commit': commit,
                                      'knf_revision': plan['to_revision'], 'stage': 'requesting',
                                      'operations': {}, 'upgraded': [], 'requested_at': clock.time(),
                                      'previous_release': previous, 'kdu_instances': instances}
    update = _Update(state_path, state, registry, context)
    update.save()
    since = clock.time()
    for kdu in plan['kdus']:
        # Other OSM clients are outside our lock: recheck right before each mutation.
        try:
            idle = _ns_idle(osm.get_ns(plan['instance_id']), key)
        except Exception:
            idle = False
        if not idle:
            if not update.record['upgraded']:
                update.finish()
                raise StaleActiveState('Active NS changed before the update; nothing was modified')
            update.save(stage='failed-partial')
            raise UpdateFailed('Active NS changed during the update; mixed release requires reconciliation')
        update.save(stage='requesting', current_kdu=kdu['name'])
        try:
            operation = osm.kdu_action(plan['instance_id'], kdu['member_index'], kdu['name'], 'upgrade',
                                       {'kdu_model': kdu['model'], 'kdu_atomic_upgrade': 'true',
                                        'kdu_reset_values': 'true'})
        except Exception:
            _uncertain(update, 'Upgrade request for ' + kdu['name'] + ' has an unknown outcome')
        update.record['operations'][kdu['name']] = operation
        update.save(stage='submitted')
        try:
            outcome = wait_operation(osm, operation, policy.get('op_timeout', 600), clock)
        except Exception:
            _uncertain(update, 'Upgrade of ' + kdu['name'] + ' could not be observed')
        if outcome == 'COMPLETED':
            update.record['upgraded'].append(kdu['name'])
            update.save()
            continue
        if outcome in ('FAILED', 'FAILED_TEMP'):
            # helm --atomic restored this KDU; never retry automatically.
            _ledger(state_path, {'commit': commit, 'event': 'upgrade-failed', 'kdu': kdu['name']})
            if not update.record['upgraded']:
                update.finish()
                raise UpdateFailed('Upgrade of ' + kdu['name'] + ' failed; previous release preserved')
            if policy.get('auto_rollback'):
                _rollback(osm, observer, update, entry, plan, since, policy, clock)
                raise UpdateFailed('Upgrade of ' + kdu['name'] + ' failed; earlier KDUs rolled back')
            update.save(stage='failed-partial')
            raise UpdateFailed('Upgrade of ' + kdu['name'] + ' failed after other KDUs upgraded; reconcile explicitly')
        _uncertain(update, 'Upgrade of ' + kdu['name'] + ' ended ' + outcome)
    healthy, detail = wait_healthy(observer, entry, plan, since, policy, clock)
    if not healthy:
        _ledger(state_path, {'commit': commit, 'event': 'health-failed', 'detail': detail})
        update.save(stage='health-failed')
        if policy.get('auto_rollback'):
            _rollback(osm, observer, update, entry, plan, since, policy, clock)
            raise UpdateFailed('Health gate failed (' + detail + '); rolled back to the previous release')
        raise UpdateFailed('Health gate failed (' + detail + '); reconcile explicitly')
    try:
        helm = _helm_revisions(observer, instances)
    except LifecycleError as error:
        _uncertain(update, 'Upgraded release cannot be identified: ' + str(error))
    current = {'scenario': key, 'instance_id': plan['instance_id'], 'commit': commit,
               'knf_revision': plan['to_revision'], 'helm': helm}
    update.finish(active_release=current, previous_release=update.record['previous_release'])
    _ledger(state_path, {'commit': commit, 'event': 'upgraded', 'scenario': key, 'instance_id': plan['instance_id']})
    return {'action': 'upgraded', 'scenario': key, 'instance_id': plan['instance_id'],
            'kdus': [k['name'] for k in plan['kdus']]}


def process_release(osm, observer, registry, release, blobs, state_path, context, policy, clock=time):
    """Publish a verified release; upgrade only the active primary RAN when it is affected."""
    verified = verify_release(release, blobs, registry, policy)
    commit = release['commit']
    with lifecycle_lock(state_path, 'cd:' + commit[:12], timeout=policy.get('lock_timeout', 600)):
        reason = blocking_reason(read_state(state_path, registry, context))
        if reason:
            raise LifecycleBusy(reason)
        published = publish(osm, verified, state_path, commit)
        result = {'commit': commit, 'published': sorted(published), 'action': 'catalog-only'}
        # Evaluated only now, under the lock, so a dashboard switch that finished
        # while we waited is what decides the target.
        state = read_state(state_path, registry, context)
        active = state['active']
        entries = {s['key']: s for s in registry['scenarios']}
        if active == 'none' or active not in published:
            return dict(result, reason='active primary RAN is not affected')
        entry = entries[active]
        try:
            require_p4_approval()
        except UpgradeNotApproved as error:
            # Publication above stays; upgrade_scenarios cannot override the gate.
            return dict(result, action='approval-required', scenario=active, reason=str(error))
        if active not in policy.get('upgrade_scenarios', ()) or (
                entry.get('experimental') and not policy.get('allow_experimental')):
            return dict(result, action='approval-required', scenario=active)
        plan = confirm_active(osm, state, entry, published[active], registry)
        if plan is None:
            return dict(result, action='already-current', scenario=active)
        return dict(result, **_apply(osm, observer, state_path, state, registry, context, entry, plan,
                                     commit, policy, clock))


def reconcile_update(osm, observer, registry, state_path, context, policy, clock=time):
    """Read-only resolution of a retained update: never issues OSM actions."""
    with lifecycle_lock(state_path, 'reconcile-update', timeout=policy.get('lock_timeout', 600)):
        state = read_state(state_path, registry, context)
        record = state['osm'].get('pending_update')
        if not record:
            return {'action': 'nothing-pending'}
        if record['stage'] == 'requesting' or record.get('current_kdu', '') not in record['operations']:
            raise UpdateUncertain('An upgrade request may or may not exist in OSM; inspect OSM operations manually')
        update = _Update(state_path, state, registry, context)
        outcomes = {k: osm.operation_state(op) for k, op in record['operations'].items()}
        if any(v not in TERMINAL for v in outcomes.values()):
            return {'action': 'still-running', 'operations': outcomes}
        entry = {s['key']: s for s in registry['scenarios']}[record['scenario']]
        kdus = [{'name': c['kdu']} for c in entry['charts']]
        healthy, detail = observer.health(entry, kdus, record['requested_at'])
        if not healthy:
            return {'action': 'unhealthy', 'detail': detail, 'operations': outcomes}
        if record['stage'] in ('submitted', 'uncertain') and set(outcomes) == {c['kdu'] for c in entry['charts']} \
                and all(v == 'COMPLETED' for v in outcomes.values()):
            try:
                helm = _helm_revisions(observer, record['kdu_instances'])
            except LifecycleError as error:
                raise UpdateUncertain(str(error) + '; pending update retained') from None
            # Both identities come from disk and read-only observation, not process memory.
            update.finish(active_release={'scenario': record['scenario'], 'instance_id': record['instance_id'],
                                          'commit': record['commit'], 'knf_revision': record['knf_revision'],
                                          'helm': helm},
                          previous_release=record['previous_release'])
            return {'action': 'confirmed-upgraded'}
        if record['stage'] in ('submitted', 'uncertain') and all(v != 'COMPLETED' for v in outcomes.values()):
            update.finish()
            return {'action': 'confirmed-previous-release'}
        return {'action': 'operator-decision-required', 'stage': record['stage'], 'operations': outcomes}
