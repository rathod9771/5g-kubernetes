"""Shared lifecycle coordination against the offline OSM NBI simulation.

No network, Kubernetes or OSM access: the real lifecycle controller, NBI adapter,
state schema and dashboard switch code run against tests/osm_nbi_sim.py.
"""
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'ran-selector'), str(Path(__file__).resolve().parent)]

import lifecycle  # noqa: E402
import lifecycle_health  # noqa: E402
from lifecycle_osm import NbiLifecycle  # noqa: E402
from local_safety import exclusive_lock  # noqa: E402
from osm_catalog import CatalogError  # noqa: E402
from osm_nbi_sim import FakeClock, OsmNbiSim, content  # noqa: E402
from runtime_config import ConfigError  # noqa: E402
from scenario_registry import load_registry  # noqa: E402
from state_schema import validate_state  # noqa: E402

spec = importlib.util.spec_from_file_location('lifecycle_backend', ROOT / 'ran-selector/backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

REGISTRY = load_registry()
ENTRIES = {s['key']: s for s in REGISTRY['scenarios']}
CONTEXT = {k: '00000000-0000-4000-8000-000000000001' for k in ['project_id', 'vim_id', 'cluster_uid', 'namespace_uid']}
CONTEXT.update(namespace='test', repo_root='/test', workload_kubeconfig='/test/kubeconfig',
               deployment_profile='rfsim', osm_host='https://osm.invalid')
POLICY = {'branch': 'reproducible-installer', 'target': 'hp-lab', 'upgrade_scenarios': {'cran-oai', 'cran-srsran', 'vcran-oai'},
          'lock_timeout': 5, 'op_timeout': 60, 'health_timeout': 30}
COMMIT_A, COMMIT_B = 'a' * 40, 'b' * 40
# Test-only P4 approval: the upgrade-path tests exercise the controller as it will
# behave once P4 is approved. Shipped code keeps the gate closed (P4GateTests).
P4_TEST_APPROVAL = {'p4_report_sha256': '4' * 64, 'osm_lcm_digest': 'sha256:' + '2' * 64, 'approved_commit': 'c' * 40}
HEADER ='ASSOC SOCK STY SST ST HBKT ASSOC-ID TX_QUEUE RX_QUEUE UID INODE LPORT RPORT LADDRS <-> RADDRS\n'
CU_ASSOCS = HEADER + ('a b 2 1 3 0 1 0 0 0 42 43252 38412 10.0.0.2 <-> 10.0.1.1\n'
                      'c d 2 1 3 0 2 0 0 0 43 38472 57335 10.0.0.2 <-> 10.0.0.3\n')
DU_ASSOCS = HEADER + 'e f 2 1 3 0 3 0 0 0 44 57335 38472 10.0.0.3 <-> 10.0.0.2\n'


def release(keys, commit=COMMIT_A, tag='v1', **overrides):
    blobs, scenarios = {}, {}
    entries = {s['key']: s for s in REGISTRY['scenarios'] + REGISTRY['installer_components']}
    for key in keys:
        artifacts = {}
        for name in (entries[key]['knf_package'], entries[key]['nsd_package']):
            blobs[name + '.tar.gz'] = content(name, tag)
            artifacts[name + '.tar.gz'] = hashlib.sha256(blobs[name + '.tar.gz']).hexdigest()
        scenarios[key] = {'artifacts': artifacts}
    manifest = dict({'schema': 1, 'commit': commit, 'branch': 'reproducible-installer', 'target': 'hp-lab',
                     'ci_status': 'success', 'scenarios': scenarios}, **overrides)
    return manifest, blobs


def pod(ready=True, restarts=0):
    return {'metadata': {'name': 'p'}, 'status': {'phase': 'Running', 'containerStatuses': [{'restartCount': restarts}],
                                                  'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}]}}


class SimObserver:
    """Read-only Kubernetes view derived from the simulation; `broken` KDUs lose F1."""
    def __init__(self, sim, clock):
        self.sim, self.clock, self.broken = sim, clock, set()

    def helm_revision(self, instance):
        return self.sim.helm[instance][-1][0]

    def observations(self, entry):
        now, result = self.clock.time(), {}
        for chart in entry['charts']:
            kdu = chart['kdu']
            if kdu in self.broken:
                result[kdu] = {'pods': [pod()], 'assocs': HEADER, 'logs': []}
            elif kdu == 'cu':
                result[kdu] = {'pods': [pod()], 'assocs': CU_ASSOCS,
                               'logs': [(now, 'Received NG Setup Response from AMF'), (now, 'Received F1 Setup Request')]}
            else:
                result[kdu] = {'pods': [pod()], 'assocs': DU_ASSOCS, 'logs': [(now, 'received F1 Setup Response')]}
        return result

    def health(self, entry, kdus, since):
        return lifecycle_health.evaluate(entry, self.observations(entry), since)


class LifecycleHarness(unittest.TestCase):
    p4_approved = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        if self.p4_approved:
            gate = mock.patch.object(lifecycle, 'P4_KDU_UPGRADE_APPROVAL', P4_TEST_APPROVAL)
            gate.start()
            self.addCleanup(gate.stop)
        self.state_path = Path(self.temp.name) / 'active-ran.yaml'
        # Write ordering is what these tests check; disk durability is not simulated.
        fsync = mock.patch('local_safety.os.fsync')
        fsync.start()
        self.addCleanup(fsync.stop)
        self.clock = FakeClock()
        self.sim = OsmNbiSim(REGISTRY, self.clock)
        for key in [s['key'] for s in REGISTRY['scenarios'] if s['generation_status'] == 'ready'] + ['open5gs']:
            self.sim.onboard(key, 'v0')
        self.core, _ = self.sim.instantiate(self.sim.package_id('ns', 'open5gs_ns'), 'core-persistent')
        self.osm = NbiLifecycle(self.sim.request)
        self.observer = SimObserver(self.sim, self.clock)
        self.policy = copy.deepcopy(POLICY)
        self.activate('cran-oai')

    def activate(self, key):
        ns_id, op = self.sim.instantiate(self.sim.package_id('ns', ENTRIES[key]['nsd_package']), 'ran-' + key)
        self.active_id = ns_id
        self.write({'active': key, 'context': CONTEXT,
                    'osm': {'core_instance_id': self.core, 'active_instance_id': ns_id,
                            'active_scenario': key, 'active_operation_id': op}})

    def write(self, state):
        validate_state(state, REGISTRY, CONTEXT)
        self.state_path.write_text(yaml.safe_dump(state))

    def state(self):
        return yaml.safe_load(self.state_path.read_text())

    def run_release(self, keys, policy=None, **kwargs):
        manifest, blobs = release(keys, **kwargs)
        return lifecycle.process_release(self.osm, self.observer, REGISTRY, manifest, blobs, self.state_path,
                                         CONTEXT, policy or self.policy, clock=self.clock)

    def actions(self, primitive=None):
        ops = [o for o in self.sim.operations.values() if o['type'] == 'action']
        return [(o['primitive'], o['kdu'], o['ns']) for o in ops if primitive in (None, o['primitive'])]

    def assert_no_replacement(self):
        for method, path, _ in self.sim.mutations:
            self.assertFalse(path.endswith('/terminate') or path == '/nslcm/v1/ns_instances_content' or method == 'DELETE',
                             'CD must never replace a RAN: ' + method + ' ' + path)

    def assert_single_primary(self):
        primaries = [i for i in self.sim.primaries() if i['nsState'] == 'READY']
        self.assertLessEqual(len(primaries), 1)
        state = self.state()
        if state['active'] != 'none':
            self.assertEqual([p['_id'] for p in primaries], [state['osm']['active_instance_id']])
            self.assertEqual(primaries[0]['name'], 'ran-' + state['active'])


class ReleaseVerificationTests(LifecycleHarness):
    def rejected(self, keys=('cran-oai',), blobs_change=None, **overrides):
        manifest, blobs = release(keys, **overrides)
        if blobs_change:
            blobs_change(blobs)
        before = self.state_path.read_bytes()
        with self.assertRaises(lifecycle.ReleaseRejected):
            lifecycle.process_release(self.osm, self.observer, REGISTRY, manifest, blobs, self.state_path,
                                      CONTEXT, self.policy, clock=self.clock)
        self.assertEqual(self.sim.mutations, [])
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertFalse((self.state_path.parent / lifecycle.LEDGER_NAME).exists())

    def test_unauthorized_branch(self):
        self.rejected(branch='main')

    def test_failed_ci(self):
        self.rejected(ci_status='failure')

    def test_other_target_and_short_commit(self):
        self.rejected(target='dell')
        self.rejected(commit='abc123')

    def test_invalid_artifact_digest(self):
        self.rejected(blobs_change=lambda b: b.__setitem__('cran_oai_knf.tar.gz', b'tampered'))

    def test_unknown_blocked_or_mismatched_scenario(self):
        manifest, blobs = release(['cran-oai'])
        manifest['scenarios']['oran-srsran'] = manifest['scenarios']['cran-oai']
        with self.assertRaises(lifecycle.ReleaseRejected):
            lifecycle.verify_release(manifest, blobs, REGISTRY, self.policy)
        manifest, blobs = release(['cran-oai'])
        manifest['scenarios']['cran-oai']['artifacts']['extra.tar.gz'] = '0' * 64
        with self.assertRaises(lifecycle.ReleaseRejected):
            lifecycle.verify_release(manifest, blobs, REGISTRY, self.policy)


class ActiveUpdateTests(LifecycleHarness):
    def test_active_ran_is_upgraded_in_place(self):
        result = self.run_release(['cran-oai'])
        self.assertEqual(result['action'], 'upgraded')
        self.assertEqual(result['kdus'], ['cu', 'du'])
        self.assertEqual(self.actions(), [('upgrade', 'cu', self.active_id), ('upgrade', 'du', self.active_id)])
        self.assert_no_replacement()
        state = self.state()
        self.assertEqual(state['active'], 'cran-oai')
        self.assertEqual(state['osm']['active_instance_id'], self.active_id)
        self.assertNotIn('pending_update', state['osm'])
        self.assertEqual(state['osm']['active_release'],
                         {'scenario': 'cran-oai', 'instance_id': self.active_id, 'commit': COMMIT_A,
                          'knf_revision': 2, 'helm': {'cu': 2, 'du': 2}})
        self.assertEqual(state['osm']['previous_release'],
                         {'scenario': 'cran-oai', 'instance_id': self.active_id, 'commit': None,
                          'knf_revision': 1, 'helm': {'cu': 1, 'du': 1}})
        self.assertEqual(self.sim.running_revision(self.active_id, 'du'), 2)
        self.assertEqual(self.sim.revision('knf', 'cran_oai_knf'), 2)
        self.assertEqual(self.sim.instances[self.core]['nsState'], 'READY')
        self.assert_single_primary()
        events = [e['event'] for e in json.loads((self.state_path.parent / lifecycle.LEDGER_NAME).read_text())]
        self.assertEqual(events, ['published', 'upgraded'])

    def test_kdu_action_payload_matches_osm_v19_lcm(self):
        self.run_release(['cran-oai'])
        method, path, _ = [m for m in self.sim.mutations if m[1].endswith('/action')][0]
        self.assertEqual((method, path), ('POST', '/nslcm/v1/ns_instances/' + self.active_id + '/action'))
        knf = self.sim.package_id('knf', 'cran_oai_knf')
        with mock.patch.object(self.sim, '_action', wraps=self.sim._action) as action:
            self.run_release(['cran-oai'], commit=COMMIT_B, tag='v2')
        body = action.call_args_list[0].args[1]
        self.assertEqual(body, {'member_vnf_index': '1', 'kdu_name': 'cu', 'primitive': 'upgrade',
                                'primitive_params': {'kdu_model': '/app/storage/' + knf + ':3/cran_oai_knf/helm-chart-v3s/cu',
                                                     'kdu_atomic_upgrade': 'true', 'kdu_reset_values': 'true'}})

    def test_same_release_twice_is_idempotent(self):
        self.run_release(['cran-oai'])
        result = self.run_release(['cran-oai'])
        self.assertEqual(result['action'], 'already-current')
        self.assertEqual(self.sim.revision('knf', 'cran_oai_knf'), 2)
        self.assertEqual(len(self.actions()), 2)

    def test_experimental_active_scenario_requires_approval(self):
        self.sim.terminate(self.active_id)
        self.sim.instances.pop(self.active_id)
        self.activate('fran-oai')
        policy = dict(self.policy, upgrade_scenarios={'fran-oai'})
        result = self.run_release(['fran-oai'], policy=policy)
        self.assertEqual(result['action'], 'approval-required')
        self.assertEqual(self.actions(), [])
        result = self.run_release(['fran-oai'], commit=COMMIT_B, tag='v2', policy=dict(policy, allow_experimental=True))
        self.assertEqual(result['action'], 'upgraded')
        self.assertEqual(result['kdus'], ['cu', 'du', 'fog'])

    def test_unlisted_scenario_is_published_but_not_upgraded(self):
        result = self.run_release(['cran-oai'], policy=dict(self.policy, upgrade_scenarios=set()))
        self.assertEqual(result['action'], 'approval-required')
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.sim.revision('knf', 'cran_oai_knf'), 2)


class CatalogOnlyTests(LifecycleHarness):
    def test_inactive_ran_is_catalog_only(self):
        before = self.state_path.read_bytes()
        result = self.run_release(['cran-srsran'])
        self.assertEqual(result['action'], 'catalog-only')
        self.assertEqual(self.sim.revision('knf', 'cran_srsran_knf'), 2)
        self.assertEqual(self.sim.revision('ns', 'cran_srsran_ns'), 2)
        self.assertEqual(self.actions(), [])
        self.assert_no_replacement()
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertFalse(any(i['name'] == 'ran-cran-srsran' for i in self.sim.instances.values()))
        self.assertEqual({p for _, p, _ in self.sim.mutations},
                         {'/vnfpkgm/v1/vnf_packages/' + self.sim.package_id('knf', 'cran_srsran_knf') + '/package_content',
                          '/nsd/v1/ns_descriptors/' + self.sim.package_id('ns', 'cran_srsran_ns') + '/nsd_content'})

    def test_multiple_changed_scenarios_upgrade_only_the_active_one(self):
        result = self.run_release(['cran-oai', 'cran-srsran', 'vcran-oai', 'cloudran-oai'])
        self.assertEqual(result['published'], ['cloudran-oai', 'cran-oai', 'cran-srsran', 'vcran-oai'])
        self.assertEqual({ns for _, _, ns in self.actions()}, {self.active_id})
        for knf in ('cran_oai_knf', 'cran_srsran_knf', 'vcran_oai_knf', 'cloudran_oai_knf'):
            self.assertEqual(self.sim.revision('knf', knf), 2)
        self.assert_no_replacement()
        self.assert_single_primary()

    def test_no_active_ran_is_catalog_only(self):
        self.sim.terminate(self.active_id)
        self.sim.instances.pop(self.active_id)
        self.write({'active': 'none', 'context': CONTEXT, 'osm': {'core_instance_id': self.core}})
        self.assertEqual(self.run_release(['cran-oai'])['action'], 'catalog-only')
        self.assertEqual(self.actions(), [])

    def test_core_update_never_touches_ran_selection(self):
        before = self.state_path.read_bytes()
        result = self.run_release(['open5gs'], policy=dict(self.policy, upgrade_scenarios={'open5gs', 'cran-oai'}))
        self.assertEqual(result['action'], 'catalog-only')
        self.assertEqual(self.sim.revision('knf', 'open5gs_knf'), 2)
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.sim.instances[self.core]['nsState'], 'READY')
        self.assert_no_replacement()

    def test_core_and_inactive_ran_together(self):
        self.assertEqual(self.run_release(['open5gs', 'cran-srsran'])['action'], 'catalog-only')
        self.assertEqual(self.state()['active'], 'cran-oai')
        self.assertEqual(self.actions(), [])


class FailureTests(LifecycleHarness):
    def test_failed_catalog_upload_never_deploys(self):
        self.sim.fail_upload = {'cran_oai_ns'}
        before = self.state_path.read_bytes()
        with self.assertRaises(lifecycle.PublishError) as caught:
            self.run_release(['cran-oai'])
        self.assertEqual(sorted(caught.exception.published['cran-oai']), ['knf'])
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.state_path.read_bytes(), before)
        events = [e['event'] for e in json.loads((self.state_path.parent / lifecycle.LEDGER_NAME).read_text())]
        self.assertEqual(events, ['publish-failed'])

    def test_first_kdu_failure_preserves_previous_release_without_retry(self):
        self.sim.outcomes[('upgrade', 'cu')] = 'FAILED'
        with self.assertRaisesRegex(lifecycle.UpdateFailed, 'previous release preserved'):
            self.run_release(['cran-oai'])
        self.assertEqual(self.actions(), [('upgrade', 'cu', self.active_id)])
        state = self.state()
        self.assertNotIn('pending_update', state['osm'])
        self.assertNotIn('active_release', state['osm'])
        self.assertEqual(self.sim.running_revision(self.active_id, 'cu'), 1)
        self.assert_no_replacement()

    def test_partial_failure_is_retained_and_blocks_the_dashboard(self):
        self.sim.outcomes[('upgrade', 'du')] = 'FAILED'
        with self.assertRaisesRegex(lifecycle.UpdateFailed, 'reconcile explicitly'):
            self.run_release(['cran-oai'])
        update = self.state()['osm']['pending_update']
        self.assertEqual((update['stage'], update['upgraded']), ('failed-partial', ['cu']))
        with self.assertRaises(lifecycle.LifecycleBusy):
            self.run_release(['cran-oai'], commit=COMMIT_B, tag='v2')
        self.assertEqual(len(self.actions()), 2)
        DashboardSwitchTests.assert_dashboard_refuses(self, 'cran-srsran')

    def test_partial_failure_auto_rollback_restores_previous_release(self):
        self.sim.outcomes[('upgrade', 'du')] = 'FAILED'
        with self.assertRaisesRegex(lifecycle.UpdateFailed, 'rolled back'):
            self.run_release(['cran-oai'], policy=dict(self.policy, auto_rollback=True))
        self.assertEqual(self.actions(), [('upgrade', 'cu', self.active_id), ('upgrade', 'du', self.active_id),
                                          ('rollback', 'cu', self.active_id)])
        self.assertEqual(self.sim.running_revision(self.active_id, 'cu'), 1)
        self.assertNotIn('pending_update', self.state()['osm'])

    def test_health_gate_rejects_ready_pods_without_f1(self):
        self.observer.broken = {'du'}
        with self.assertRaisesRegex(lifecycle.UpdateFailed, 'F1'):
            self.run_release(['cran-oai'])
        self.assertEqual(self.state()['osm']['pending_update']['stage'], 'health-failed')
        self.assertEqual(self.actions('rollback'), [])

    def test_health_failure_with_auto_rollback(self):
        self.observer.broken = {'du'}
        observer_health = self.observer.health
        calls = []
        def health(entry, kdus, since):
            calls.append(since)
            if self.actions('rollback'):
                self.observer.broken = set()
            return observer_health(entry, kdus, since)
        self.observer.health = health
        with self.assertRaisesRegex(lifecycle.UpdateFailed, 'rolled back'):
            self.run_release(['cran-oai'], policy=dict(self.policy, auto_rollback=True))
        self.assertEqual([a[:2] for a in self.actions('rollback')], [('rollback', 'du'), ('rollback', 'cu')])
        self.assertEqual(self.sim.running_revision(self.active_id, 'du'), 1)
        self.assertNotIn('pending_update', self.state()['osm'])

    def test_timeout_is_uncertain_and_never_retried(self):
        self.sim.outcomes[('upgrade', 'du')] = 'TIMEOUT'
        with self.assertRaises(lifecycle.UpdateUncertain):
            self.run_release(['cran-oai'])
        update = self.state()['osm']['pending_update']
        self.assertEqual((update['stage'], update['current_kdu'], update['upgraded']), ('uncertain', 'du', ['cu']))
        with self.assertRaises(lifecycle.LifecycleBusy):
            self.run_release(['cran-oai'])
        self.assertEqual(len(self.actions()), 2)
        DashboardSwitchTests.assert_dashboard_refuses(self, 'cran-srsran')
        # Read-only reconciliation once OSM reports the real outcome.
        reconcile = lambda: lifecycle.reconcile_update(self.osm, self.observer, REGISTRY, self.state_path,
                                                       CONTEXT, self.policy, clock=self.clock)
        self.assertEqual(reconcile()['action'], 'still-running')
        self.sim.finish_operation(update['operations']['du'])
        mutations = len(self.sim.mutations)
        self.assertEqual(reconcile()['action'], 'confirmed-upgraded')
        self.assertEqual(len(self.sim.mutations), mutations)
        self.assertEqual(self.state()['osm']['active_release']['commit'], COMMIT_A)

    def test_lost_request_cannot_be_resolved_automatically(self):
        self.sim.outcomes[('upgrade', 'cu')] = 'LOST'
        with self.assertRaises(lifecycle.UpdateUncertain):
            self.run_release(['cran-oai'])
        update = self.state()['osm']['pending_update']
        self.assertEqual((update['stage'], update['current_kdu'], update['operations']), ('uncertain', 'cu', {}))
        with self.assertRaises(lifecycle.UpdateUncertain):
            lifecycle.reconcile_update(self.osm, self.observer, REGISTRY, self.state_path, CONTEXT, self.policy,
                                       clock=self.clock)
        self.assertEqual(len(self.actions()), 1)


class ProcessCrash(BaseException):
    """Simulated process death: not an Exception, so no handler in the controller runs."""


class PreviousReleaseDurabilityTests(LifecycleHarness):
    EXPECTED_PREVIOUS = {'scenario': 'cran-oai', 'commit': None, 'knf_revision': 1, 'helm': {'cu': 1, 'du': 1}}

    def expected_previous(self):
        return dict(self.EXPECTED_PREVIOUS, instance_id=self.active_id)

    def fresh_process(self):
        """Recovery uses new adapter/observer objects and only what is on disk."""
        return NbiLifecycle(self.sim.request), SimObserver(self.sim, self.clock)

    def test_previous_release_is_durable_before_the_first_action(self):
        seen = []
        original = self.osm.kdu_action
        def kdu_action(*args):
            seen.append(yaml.safe_load(self.state_path.read_text())['osm']['pending_update'])
            return original(*args)
        self.osm.kdu_action = kdu_action
        self.run_release(['cran-oai'])
        self.assertEqual(seen[0]['previous_release'], self.expected_previous())
        self.assertEqual(set(seen[0]['kdu_instances']), {'cu', 'du'})
        self.assertEqual((seen[0]['operations'], seen[0]['stage']), ({}, 'requesting'))
        self.assertEqual(self.state()['osm']['previous_release'], self.expected_previous())

    def test_crash_during_health_gate_preserves_previous_release(self):
        def crash(*args):
            raise ProcessCrash()
        self.observer.health = crash
        with self.assertRaises(ProcessCrash):
            self.run_release(['cran-oai'])
        on_disk = self.state()['osm']
        self.assertEqual(on_disk['pending_update']['previous_release'], self.expected_previous())
        self.assertNotIn('previous_release', on_disk)
        osm, observer = self.fresh_process()
        mutations = len(self.sim.mutations)
        result = lifecycle.reconcile_update(osm, observer, REGISTRY, self.state_path, CONTEXT, self.policy,
                                            clock=self.clock)
        self.assertEqual(result['action'], 'confirmed-upgraded')
        self.assertEqual(len(self.sim.mutations), mutations, 'reconciliation must be read-only')
        state = self.state()['osm']
        self.assertNotIn('pending_update', state)
        self.assertEqual(state['previous_release'], self.expected_previous())
        self.assertEqual(state['active_release'], {'scenario': 'cran-oai', 'instance_id': self.active_id,
                                                   'commit': COMMIT_A, 'knf_revision': 2,
                                                   'helm': {'cu': 2, 'du': 2}})
        self.assertEqual(len(self.actions()), 2)

    def test_reconcile_fails_closed_when_current_release_is_unobservable(self):
        def crash(*args):
            raise ProcessCrash()
        self.observer.health = crash
        with self.assertRaises(ProcessCrash):
            self.run_release(['cran-oai'])
        before = self.state_path.read_bytes()
        osm, observer = self.fresh_process()
        observer.helm_revision = lambda instance: (_ for _ in ()).throw(OSError('helm unavailable'))
        with self.assertRaises(lifecycle.UpdateUncertain):
            lifecycle.reconcile_update(osm, observer, REGISTRY, self.state_path, CONTEXT, self.policy, clock=self.clock)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_unknown_previous_release_fails_closed_before_any_action(self):
        for broken in (lambda instance: (_ for _ in ()).throw(OSError('helm unavailable')),
                       lambda instance: 0, lambda instance: None, lambda instance: True):
            with self.subTest(broken=broken):
                before = self.state_path.read_bytes()
                self.observer.helm_revision = broken
                with self.assertRaisesRegex(lifecycle.StaleActiveState, 'previous release unknown'):
                    self.run_release(['cran-oai'])
                self.assertEqual(self.actions(), [])
                self.assertEqual(self.state_path.read_bytes(), before)

    def test_partial_uncertainty_retains_previous_release_without_retry(self):
        self.sim.outcomes[('upgrade', 'du')] = 'TIMEOUT'
        with self.assertRaises(lifecycle.UpdateUncertain):
            self.run_release(['cran-oai'])
        update = self.state()['osm']['pending_update']
        self.assertEqual((update['stage'], update['upgraded']), ('uncertain', ['cu']))
        self.assertEqual(update['previous_release'], self.expected_previous())
        osm, observer = self.fresh_process()
        self.assertEqual(lifecycle.reconcile_update(osm, observer, REGISTRY, self.state_path, CONTEXT,
                                                    self.policy, clock=self.clock)['action'], 'still-running')
        self.sim.finish_operation(update['operations']['du'])
        lifecycle.reconcile_update(osm, observer, REGISTRY, self.state_path, CONTEXT, self.policy, clock=self.clock)
        self.assertEqual(self.state()['osm']['previous_release'], self.expected_previous())
        self.assertEqual(len(self.actions()), 2)


class StaleStateTests(LifecycleHarness):
    def stale(self, mutate, reason=''):
        mutate()
        before = self.state_path.read_bytes()
        # Refused by the confirmation step, before any pending update is recorded.
        with self.assertRaisesRegex(lifecycle.StaleActiveState, reason):
            self.run_release(['cran-oai'])
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assert_no_replacement()

    def test_recorded_instance_missing_in_osm(self):
        self.stale(lambda: self.sim.instances.pop(self.active_id), 'Single-primary')

    def test_recorded_instance_broken(self):
        self.stale(lambda: self.sim.instances[self.active_id].update(nsState='BROKEN'), 'idle, running')

    def test_osm_operation_in_progress(self):
        self.stale(lambda: self.sim.instances[self.active_id].update(currentOperation='SCALING'), 'idle, running')

    def test_second_primary_ran_in_osm(self):
        self.stale(lambda: self.sim.instantiate(self.sim.package_id('ns', 'cran_srsran_ns'), 'ran-cran-srsran'))

    def test_instance_from_another_descriptor(self):
        self.stale(lambda: self.sim.instances[self.active_id].update(**{'nsd-id': self.sim.package_id('ns', 'vcran_oai_ns')}))

    def test_ns_changes_between_confirmation_and_first_action(self):
        original = self.osm.get_ns
        calls = []
        def get_ns(ns_id):
            calls.append(ns_id)
            record = original(ns_id)
            return dict(record, nsState='BROKEN') if len(calls) > 1 else record
        self.osm.get_ns = get_ns
        with self.assertRaisesRegex(lifecycle.StaleActiveState, 'nothing was modified'):
            self.run_release(['cran-oai'])
        self.assertEqual(self.actions(), [])
        self.assertNotIn('pending_update', self.state()['osm'])

    def test_pending_dashboard_operation_blocks_before_publication(self):
        state = self.state()
        state['osm'].pop('active_instance_id'); state['osm'].pop('active_scenario'); state['active'] = 'none'
        state['osm']['pending_instance'] = {'id': 'pending', 'operation': 'op', 'scenario': 'cran-oai'}
        self.write(state)
        with self.assertRaisesRegex(lifecycle.LifecycleBusy, 'pending'):
            self.run_release(['cran-srsran'])
        self.assertEqual(self.sim.mutations, [])


class StateSchemaTests(LifecycleHarness):
    def update_state(self, **update):
        state = self.state()
        previous = {'scenario': 'cran-oai', 'instance_id': self.active_id, 'commit': None, 'knf_revision': 1,
                    'helm': {'cu': 1, 'du': 1}}
        state['osm']['pending_update'] = dict({'scenario': 'cran-oai', 'instance_id': self.active_id, 'commit': COMMIT_A,
                                               'knf_revision': 2, 'stage': 'submitted', 'operations': {}, 'upgraded': [],
                                               'requested_at': 1_800_000_000.5, 'previous_release': previous,
                                               'kdu_instances': {'cu': 'cu-0000000001', 'du': 'du-0000000002'}},
                                              **update)
        return state

    def test_requested_at_rejects_adversarial_values(self):
        for value in (float('nan'), float('inf'), float('-inf'), -1, -0.001, '1800000000', None, True, False,
                      [1], {'t': 1}):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, 'request time'):
                validate_state(self.update_state(requested_at=value), REGISTRY, CONTEXT)
        state = self.update_state()
        state['osm']['pending_update'].pop('requested_at')
        with self.assertRaisesRegex(ConfigError, 'request time'):
            validate_state(state, REGISTRY, CONTEXT)

    def test_requested_at_accepts_finite_non_negative_numbers(self):
        for value in (0, 0.0, 1_800_000_000, 1_800_000_000.25):
            with self.subTest(value=value):
                validate_state(self.update_state(requested_at=value), REGISTRY, CONTEXT)

    def test_requested_at_yaml_special_floats_are_rejected_on_read(self):
        for text in ('.nan', '.inf', '-.inf', '-1', "'1800000000'", 'null', 'true'):
            with self.subTest(text=text):
                state = self.update_state()
                document = yaml.safe_dump(state).replace('requested_at: 1800000000.5', 'requested_at: ' + text)
                self.assertIn('requested_at: ' + text, document)
                self.state_path.write_text(document)
                with self.assertRaisesRegex(ConfigError, 'request time'):
                    lifecycle.read_state(self.state_path, REGISTRY, CONTEXT)

    def test_previous_release_is_required_and_bound_to_the_instance(self):
        previous = self.update_state()['osm']['pending_update']['previous_release']
        bad = [None, dict(previous, instance_id='other'), dict(previous, scenario='cran-srsran'),
               dict(previous, helm={}), dict(previous, helm={'cu': 1}), dict(previous, knf_revision=0),
               dict(previous, helm={'cu': True, 'du': 1})]
        for value in bad:
            with self.subTest(previous=value), self.assertRaises(ConfigError):
                validate_state(self.update_state(previous_release=value), REGISTRY, CONTEXT)
        for instances in (None, {}, {'cu': 'cu-1'}, {'cu': 'cu-1', 'du': 'bad/instance'}):
            with self.subTest(instances=instances), self.assertRaises(ConfigError):
                validate_state(self.update_state(kdu_instances=instances), REGISTRY, CONTEXT)
        with self.assertRaises(ConfigError):
            validate_state(self.update_state(operations={'fog': 'op-1'}), REGISTRY, CONTEXT)
        with self.assertRaises(ConfigError):
            validate_state(self.update_state(current_kdu='fog'), REGISTRY, CONTEXT)

    def test_valid_update_record(self):
        validate_state(self.update_state(), REGISTRY, CONTEXT)

    def test_update_must_target_the_active_instance(self):
        for change in ({'scenario': 'cran-srsran'}, {'instance_id': 'other'}, {'stage': 'done'},
                       {'commit': 'abc'}, {'knf_revision': 0}, {'upgraded': ['cu']}):
            with self.subTest(change=change), self.assertRaises(ConfigError):
                validate_state(self.update_state(**change), REGISTRY, CONTEXT)

    def test_consistent_update_for_another_instance_is_rejected(self):
        # Internally consistent record (previous release agrees) that names a non-active NS.
        state = self.update_state(instance_id='other')
        state['osm']['pending_update']['previous_release']['instance_id'] = 'other'
        with self.assertRaisesRegex(ConfigError, 'disagrees with the active RAN instance'):
            validate_state(state, REGISTRY, CONTEXT)

    def test_update_excludes_other_pending_operations(self):
        state = self.update_state()
        state['osm']['pending_instance'] = {'id': 'x', 'operation': 'y', 'scenario': 'cran-oai'}
        with self.assertRaises(ConfigError):
            validate_state(state, REGISTRY, CONTEXT)

    def test_release_records(self):
        state = self.state()
        state['osm']['active_release'] = {'scenario': 'fran', 'instance_id': 'x', 'commit': None, 'knf_revision': 1}
        with self.assertRaises(ConfigError):
            validate_state(state, REGISTRY, CONTEXT)
        state['osm']['active_release'] = {'scenario': 'cran-oai', 'instance_id': 'x', 'commit': None,
                                          'knf_revision': 1, 'helm': {'cu': 0}}
        with self.assertRaises(ConfigError):
            validate_state(state, REGISTRY, CONTEXT)

    def test_existing_dashboard_states_remain_valid(self):
        validate_state(self.state(), REGISTRY, CONTEXT)
        validate_state({'active': 'none', 'context': CONTEXT, 'osm': {'core_instance_id': 'core'}}, REGISTRY, CONTEXT)


class AdapterTests(LifecycleHarness):
    def test_publish_adds_revision_and_keeps_previous(self):
        knf = self.sim.package_id('knf', 'cran_oai_knf')
        result = self.osm.publish('knf', 'cran_oai_knf', content('cran_oai_knf', 'v1'))
        self.assertEqual((result['id'], result['revision'], result['uploaded']), (knf, 2, True))
        self.assertEqual(sorted(self.sim.packages[('knf', 'cran_oai_knf')]['revisions']), [1, 2])
        again = self.osm.publish('knf', 'cran_oai_knf', content('cran_oai_knf', 'v1'))
        self.assertEqual((again['revision'], again['uploaded']), (2, False))

    def test_concurrent_revision_change_is_detected(self):
        original = self.sim._upload
        def racing(kind, name, data):
            original(kind, name, data)
            return original(kind, name, b'pkg:' + name.encode() + b':other')
        self.sim._upload = racing
        with self.assertRaisesRegex(CatalogError, 'concurrently'):
            self.osm.publish('knf', 'cran_oai_knf', content('cran_oai_knf', 'v1'))

    def test_new_package_is_created_without_delete(self):
        self.sim.packages.pop(('knf', 'cran_oai_knf'))
        self.assertEqual(self.osm.publish('knf', 'cran_oai_knf', content('cran_oai_knf', 'v1'))['revision'], 1)
        self.assertFalse(any(m[0] == 'DELETE' for m in self.sim.mutations))


class HealthGateTests(unittest.TestCase):
    entry = ENTRIES['cran-oai']

    def observations(self):
        return {'cu': {'pods': [pod()], 'assocs': CU_ASSOCS,
                       'logs': [(10, 'Received NG Setup Response from AMF'), (10, 'Received F1 Setup Request')]},
                'du': {'pods': [pod()], 'assocs': DU_ASSOCS, 'logs': [(10, 'received F1 Setup Response')]}}

    def test_healthy(self):
        self.assertTrue(lifecycle_health.evaluate(self.entry, self.observations(), 5)[0])

    def test_pod_readiness_alone_is_not_enough(self):
        obs = self.observations()
        obs['du']['assocs'] = HEADER
        self.assertFalse(lifecycle_health.evaluate(self.entry, obs, 5)[0])
        obs = self.observations()
        obs['cu']['assocs'] = HEADER + 'c d 2 1 3 0 2 0 0 0 43 38472 57335 10.0.0.2 <-> 10.0.0.3\n'
        self.assertIn('NGAP', lifecycle_health.evaluate(self.entry, obs, 5)[1])

    def test_setup_logs_must_follow_the_update(self):
        self.assertIn('NG Setup', lifecycle_health.evaluate(self.entry, self.observations(), 11)[1])

    def test_overlapping_or_restarted_pods_fail(self):
        obs = self.observations()
        obs['du']['pods'].append(pod())
        self.assertIn('exactly one pod', lifecycle_health.evaluate(self.entry, obs, 5)[1])
        obs = self.observations()
        obs['cu']['pods'] = [pod(restarts=1)]
        self.assertFalse(lifecycle_health.evaluate(self.entry, obs, 5)[0])

    def test_srsran_uses_sctp_state_without_oai_log_markers(self):
        obs = self.observations()
        for item in obs.values():
            item['logs'] = []
        self.assertTrue(lifecycle_health.evaluate(ENTRIES['cran-srsran'], obs, 5)[0])
        self.assertFalse(lifecycle_health.evaluate(self.entry, obs, 5)[0])


class DashboardSwitchTests(LifecycleHarness):
    """The real dashboard switch (_deploy_verified) and CD share one lock and state file."""
    def setUp(self):
        super().setUp()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(backend, 'CONFIG_FILE', str(self.state_path)))
        stack.enter_context(mock.patch.object(backend.osm_client, '_request', side_effect=self.sim.request))
        stack.enter_context(mock.patch.object(backend.osm_client, 'runtime_config', return_value={}))
        stack.enter_context(mock.patch.object(backend.osm_client, 'runtime_session',
                                              side_effect=lambda cfg: contextlib.nullcontext()))
        stack.enter_context(mock.patch.object(backend, '_wait_pods_gone', return_value=True))

    def switch(self, key):
        """What POST /api/deploy does after its catalog preflight."""
        with exclusive_lock(lifecycle.lock_path(self.state_path)), backend.app.test_request_context():
            self.sim.source = 'dashboard'
            try:
                scenarios = backend.dashboard_scenarios(REGISTRY)
                return backend._deploy_verified(key, REGISTRY, ENTRIES, scenarios[key], ENTRIES[key], CONTEXT,
                                                {'ns': self.sim.package_id('ns', ENTRIES[key]['nsd_package'])})
            finally:
                self.sim.source = 'test'

    @staticmethod
    def assert_dashboard_refuses(case, key):
        with mock.patch.object(backend, 'CONFIG_FILE', str(case.state_path)), \
             mock.patch.object(backend.osm_client, 'terminate_ns') as terminate, \
             mock.patch.object(backend.osm_client, 'instantiate_ns') as instantiate, \
             backend.app.test_request_context():
            scenarios = backend.dashboard_scenarios(REGISTRY)
            with case.assertRaisesRegex(RuntimeError, 'CD update'):
                backend._deploy_verified(key, REGISTRY, ENTRIES, scenarios[key], ENTRIES[key], CONTEXT, {'ns': 'x'})
            terminate.assert_not_called()
            instantiate.assert_not_called()

    def test_dashboard_switch_still_replaces_the_primary_ran(self):
        self.switch('cran-srsran')
        self.assertEqual(self.state()['active'], 'cran-srsran')
        self.assertNotIn(self.active_id, self.sim.instances)
        self.assert_single_primary()

    def test_switch_while_cd_waits_retargets_to_the_new_active_scenario(self):
        held, release_lock, results = threading.Event(), threading.Event(), {}

        def dashboard():
            with exclusive_lock(lifecycle.lock_path(self.state_path)):
                held.set()
                release_lock.wait(5)
                with backend.app.test_request_context():
                    scenarios = backend.dashboard_scenarios(REGISTRY)
                    backend._deploy_verified('cran-srsran', REGISTRY, ENTRIES, scenarios['cran-srsran'],
                                             ENTRIES['cran-srsran'], CONTEXT,
                                             {'ns': self.sim.package_id('ns', 'cran_srsran_ns')})

        def agent():
            results['cd'] = self.run_release(['cran-oai'])

        first = threading.Thread(target=dashboard)
        first.start()
        self.assertTrue(held.wait(5))
        second = threading.Thread(target=agent)
        with mock.patch.object(self.clock, 'sleep', side_effect=lambda s: time.sleep(0.01)):
            second.start()
            time.sleep(0.3)
            self.assertEqual(self.sim.mutations, [], 'CD must not publish while the dashboard holds the lock')
            release_lock.set()
            first.join(5); second.join(5)
        self.assertEqual(results['cd']['action'], 'catalog-only')
        self.assertEqual(self.state()['active'], 'cran-srsran')
        self.assertEqual(self.actions(), [])
        self.assert_single_primary()

    def test_switch_to_an_affected_scenario_is_then_upgraded_not_replaced(self):
        self.switch('vcran-oai')
        new_id = self.state()['osm']['active_instance_id']
        mutations = len(self.sim.mutations)
        result = self.run_release(['cran-oai', 'vcran-oai'])
        self.assertEqual((result['action'], result['scenario'], result['instance_id']), ('upgraded', 'vcran-oai', new_id))
        cd_mutations = self.sim.mutations[mutations:]
        self.assertFalse(any(p.endswith('/terminate') or p == '/nslcm/v1/ns_instances_content' for _, p, _ in cd_mutations))
        self.assert_single_primary()

    def test_dashboard_waits_for_cd_and_cd_busy_times_out(self):
        with lifecycle.lifecycle_lock(self.state_path, 'cd:test'):
            self.assertIn('cd:test', (self.state_path.parent / lifecycle.HOLDER_NAME).read_text())
            with self.assertRaisesRegex(lifecycle.LifecycleBusy, 'holder: cd:test'):
                with lifecycle.lifecycle_lock(self.state_path, 'other', timeout=0.2):
                    pass
            with self.assertRaises(lifecycle.LifecycleBusy):
                self.run_release(['cran-oai'], policy=dict(self.policy, lock_timeout=0.2))
        self.assertFalse((self.state_path.parent / lifecycle.HOLDER_NAME).exists())
        self.assertEqual(self.sim.mutations, [])

    def test_concurrent_cd_jobs_apply_one_upgrade(self):
        results = []
        with mock.patch.object(self.clock, 'sleep', side_effect=lambda s: time.sleep(0.001)):
            threads = [threading.Thread(target=lambda: results.append(self.run_release(['cran-oai']))) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(sorted(r['action'] for r in results), ['already-current', 'already-current', 'upgraded'])
        self.assertEqual(self.actions(), [('upgrade', 'cu', self.active_id), ('upgrade', 'du', self.active_id)])
        self.assertEqual(self.sim.revision('knf', 'cran_oai_knf'), 2)

    def test_single_primary_invariant_over_random_operation_sequences(self):
        ready = [s['key'] for s in REGISTRY['scenarios']
                 if s['generation_status'] == 'ready' and not s.get('additive') and not s.get('experimental')]
        policy = dict(self.policy, upgrade_scenarios=set(ready), auto_rollback=True)
        seen = []
        for seed in range(8):
            rng = random.Random(seed)
            for step in range(12):
                choice = rng.random()
                self.sim.outcomes = {}
                self.observer.broken = set()
                try:
                    if choice < 0.35:
                        self.switch(rng.choice(ready))
                        seen.append('switch')
                    else:
                        if choice > 0.85:
                            self.sim.outcomes[('upgrade', rng.choice(['cu', 'du']))] = rng.choice(['FAILED', 'TIMEOUT'])
                        elif choice > 0.75:
                            self.observer.broken = {'du'}
                        commit = '{:040x}'.format(seed * 100 + step + 1)
                        seen.append(self.run_release(rng.sample(ready, rng.randint(1, 3)), commit=commit,
                                                     tag='s{}-{}'.format(seed, step), policy=policy)['action'])
                except (lifecycle.LifecycleError, RuntimeError, ValueError) as error:
                    seen.append(type(error).__name__)
                with self.subTest(seed=seed, step=step):
                    self.assert_single_primary()
                    for method, path, source in self.sim.mutations:
                        if path.endswith('/terminate') or path == '/nslcm/v1/ns_instances_content' or method == 'DELETE':
                            self.assertEqual(source, 'dashboard')
                pending = self.state()['osm'].get('pending_update')
                if pending:
                    for op in pending['operations'].values():
                        if self.sim.operations[op]['state'] == 'PROCESSING':
                            self.sim.finish_operation(op, 'FAILED')
                    with contextlib.suppress(lifecycle.LifecycleError):
                        lifecycle.reconcile_update(self.osm, self.observer, REGISTRY, self.state_path, CONTEXT,
                                                   policy, clock=self.clock)
                    state = self.state()
                    if state['osm'].get('pending_update'):
                        # Operator decision: discard after confirming OSM state (simulated).
                        state['osm'].pop('pending_update')
                        self.write(state)
        # The sequences must really cover switches, upgrades, catalog-only releases and failures.
        for outcome in ('switch', 'upgraded', 'catalog-only', 'UpdateFailed', 'UpdateUncertain'):
            self.assertIn(outcome, seen)


class P4GateTests(LifecycleHarness):
    """The shipped controller: P4 is not approved, so no KDU upgrade can happen."""
    p4_approved = False
    OPEN_EVERYTHING = dict(POLICY, upgrade_scenarios={'cran-oai', 'cran-srsran', 'vcran-oai', 'open5gs'},
                           allow_experimental=True, auto_rollback=True, p4_approved=True,
                           P4_KDU_UPGRADE_APPROVAL=P4_TEST_APPROVAL, p4_kdu_upgrade_approval=P4_TEST_APPROVAL)

    def assert_untouched_ran(self):
        self.assertEqual(self.actions(), [])
        self.assertFalse([m for m in self.sim.mutations if m[1].endswith('/action')])
        self.assert_no_replacement()
        state = self.state()
        self.assertEqual((state['active'], state['osm']['active_instance_id']), ('cran-oai', self.active_id))
        for field in ('pending_update', 'active_release', 'previous_release'):
            self.assertNotIn(field, state['osm'])
        self.assertEqual((self.sim.running_revision(self.active_id, 'cu'), self.sim.running_revision(self.active_id, 'du')),
                         (1, 1))

    def test_shipped_gate_is_closed(self):
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        with self.assertRaisesRegex(lifecycle.UpgradeNotApproved, 'P4'):
            lifecycle.require_p4_approval()

    def test_active_release_is_published_but_never_upgraded(self):
        # Neither caller policy nor the environment can open the gate.
        env = {'P4_KDU_UPGRADE_APPROVAL': json.dumps(P4_TEST_APPROVAL), 'P4_APPROVED': 'true', 'P4_VALIDATED': '1'}
        with mock.patch.dict('os.environ', env):
            result = self.run_release(['cran-oai'], policy=self.OPEN_EVERYTHING)
        self.assertEqual((result['action'], result['scenario']), ('approval-required', 'cran-oai'))
        self.assertIn('P4', result['reason'])
        self.assertEqual(result['published'], ['cran-oai'])
        self.assertEqual(self.sim.revision('knf', 'cran_oai_knf'), 2)        # catalog-only publication happened
        self.assert_untouched_ran()
        events = [e['event'] for e in json.loads((self.state_path.parent / lifecycle.LEDGER_NAME).read_text())]
        self.assertEqual(events, ['published'])

    def test_inactive_and_core_releases_stay_catalog_only(self):
        self.assertEqual(self.run_release(['cran-srsran'], policy=self.OPEN_EVERYTHING)['action'], 'catalog-only')
        result = self.run_release(['open5gs'], commit=COMMIT_B, tag='v2', policy=self.OPEN_EVERYTHING)
        self.assertEqual(result['action'], 'catalog-only')
        self.assertEqual(self.sim.instances[self.core]['nsState'], 'READY')
        self.assert_untouched_ran()

    def test_direct_controller_and_adapter_calls_are_refused(self):
        before, mutations = self.state_path.read_bytes(), list(self.sim.mutations)
        entry = ENTRIES['cran-oai']
        plan = {'instance_id': self.active_id, 'commit': None, 'from_revision': 1, 'to_revision': 2,
                'kdus': [{'name': 'cu', 'instance': 'cu-1', 'member_index': '1', 'model': '/app/storage/x'}]}
        with self.assertRaises(lifecycle.UpgradeNotApproved):
            lifecycle._apply(self.osm, self.observer, self.state_path, self.state(), REGISTRY, CONTEXT, entry, plan,
                             COMMIT_A, self.OPEN_EVERYTHING, self.clock)
        for primitive, params in (('upgrade', {'kdu_model': '/app/storage/x', 'kdu_atomic_upgrade': 'true'}),
                                  ('rollback', {})):
            with self.subTest(primitive=primitive), self.assertRaises(lifecycle.UpgradeNotApproved):
                self.osm.kdu_action(self.active_id, '1', 'cu', primitive, params)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.sim.mutations, mutations)
        self.assert_untouched_ran()

    def test_malformed_approvals_do_not_open_the_gate(self):
        good = P4_TEST_APPROVAL
        for value in (True, 1, 'approved', [], {}, {k: v for k, v in good.items() if k != 'approved_commit'},
                      dict(good, extra='x'), dict(good, p4_report_sha256='4' * 63), dict(good, osm_lcm_digest='2' * 64),
                      dict(good, approved_commit='C' * 40), dict(good, approved_commit=None),
                      dict(good, p4_report_sha256='4' * 64 + '\n')):
            with self.subTest(value=value), mock.patch.object(lifecycle, 'P4_KDU_UPGRADE_APPROVAL', value):
                with self.assertRaises(lifecycle.UpgradeNotApproved):
                    lifecycle.require_p4_approval()
        with mock.patch.object(lifecycle, 'P4_KDU_UPGRADE_APPROVAL', dict(good)):
            self.assertEqual(lifecycle.require_p4_approval(), good)

    def test_gate_is_a_reviewed_code_constant_only(self):
        for name in ('lifecycle.py', 'lifecycle_osm.py'):
            source = (ROOT / 'scripts' / name).read_text()
            for read in ('os.environ', 'getenv', 'environ[', 'environ.get'):
                self.assertFalse(read in source, name + ' reads the environment: ' + read)
        source = (ROOT / 'scripts/lifecycle.py').read_text()
        self.assertEqual(source.count('P4_KDU_UPGRADE_APPROVAL ='), 1)
        self.assertIn('\nP4_KDU_UPGRADE_APPROVAL = None\n', source)


if __name__ == '__main__':
    unittest.main()
