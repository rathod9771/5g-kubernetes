"""P4 experiment artifacts: packages, charts, target guards and the full runner offline.

The runner executes against tests/p4_sim.py (OSM v19 NBI/LCM, Helm and kubectl
behaviour); no network, Kubernetes, OSM or process is used.
"""
import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'scripts/experiments/p4'), str(Path(__file__).resolve().parent)]

import lifecycle  # noqa: E402
from lifecycle_osm import NbiLifecycle  # noqa: E402
import p4_experiment as p4  # noqa: E402
import p4_packages  # noqa: E402
from osm_nbi_sim import FakeClock  # noqa: E402
from p4_sim import LCM_DIGEST, P4Target, helm_release_objects, kube_root_ca  # noqa: E402

HELM = os.environ.get('HELM_BIN', 'helm')
TARGET = '172.30.0.20'


def members(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        return {m.name: (archive.extractfile(m).read() if m.isfile() else None) for m in archive.getmembers()}


class PackageTests(unittest.TestCase):
    def test_builds_are_deterministic(self):
        for revision in p4_packages.REVISIONS:
            self.assertEqual(p4_packages.build(revision), p4_packages.build(revision))

    def test_layout_identities_and_revision_contents(self):
        descriptors, nsds = set(), set()
        for revision, settings in p4_packages.REVISIONS.items():
            archives = p4_packages.build(revision)
            self.assertEqual(sorted(archives), ['p4kdu_knf.tar.gz', 'p4kdu_ns.tar.gz'])
            knf = members(archives['p4kdu_knf.tar.gz'])
            self.assertTrue(all(name.startswith('p4kdu_knf/') or name == 'p4kdu_knf' for name in knf))
            vnfd = yaml.safe_load(knf['p4kdu_knf/p4kdu_knf_vnfd.yaml'])['vnfd']
            self.assertEqual((vnfd['id'], vnfd['product-name']), ('p4kdu_knf', 'p4kdu_knf'))
            self.assertEqual(vnfd['kdu'], [{'name': 'cu', 'helm-chart': 'cu'}, {'name': 'du', 'helm-chart': 'du'}])
            descriptors.add(knf['p4kdu_knf/p4kdu_knf_vnfd.yaml'])
            nsds.add(archives['p4kdu_ns.tar.gz'])
            nsd = yaml.safe_load(members(archives['p4kdu_ns.tar.gz'])['p4kdu_ns/p4kdu_ns_nsd.yaml'])['nsd']['nsd'][0]
            self.assertEqual((nsd['id'], nsd['vnfd-id'], nsd['df'][0]['vnf-profile'][0]['id']),
                             ('p4kdu_ns', ['p4kdu_knf'], 'p4kdu'))
            for kdu in ('cu', 'du'):
                chart = yaml.safe_load(knf['p4kdu_knf/helm-chart-v3s/' + kdu + '/Chart.yaml'])
                values = yaml.safe_load(knf['p4kdu_knf/helm-chart-v3s/' + kdu + '/values.yaml'])
                self.assertEqual((chart['name'], chart['version']), ('p4kdu-' + kdu, settings[kdu]['version']))
                self.assertEqual((values['component'], values['marker']), (kdu, settings[kdu]['marker']))
                self.assertEqual(values['broken'], settings[kdu].get('broken', False))
        # OSM v19 accepts a new revision of an in-use KNF when the descriptor is unchanged.
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(len(nsds), 1)
        self.assertEqual(p4_packages.REVISIONS[3]['du'].get('broken'), True)
        self.assertEqual(p4_packages.REVISIONS[3]['cu'].get('readyDelaySeconds'), 45)

    def test_write_all_manifest_matches_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = p4_packages.write_all(directory)
            for revision, entry in manifest['revisions'].items():
                for name, digest in entry['archives'].items():
                    data = (Path(directory) / ('rev' + revision) / name).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest)


@unittest.skipUnless(shutil.which(HELM), 'helm not available')
class ChartRenderingTests(unittest.TestCase):
    def render(self, revision, kdu):
        with tempfile.TemporaryDirectory() as directory:
            with tarfile.open(fileobj=io.BytesIO(p4_packages.build(revision)['p4kdu_knf.tar.gz']), mode='r:gz') as archive:
                archive.extractall(directory, filter='data')
            chart = Path(directory) / 'p4kdu_knf/helm-chart-v3s' / kdu
            lint = subprocess.run([HELM, 'lint', str(chart)], capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
            result = subprocess.run([HELM, 'template', 'p4kdu-' + kdu, str(chart)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return [d for d in yaml.safe_load_all(result.stdout) if d]

    def test_every_revision_renders_only_namespaced_dummy_objects(self):
        for revision, settings in p4_packages.REVISIONS.items():
            for kdu in ('cu', 'du'):
                with self.subTest(revision=revision, kdu=kdu):
                    documents = self.render(revision, kdu)
                    self.assertEqual(sorted(d['kind'] for d in documents), ['ConfigMap', 'Deployment'])
                    for document in documents:
                        self.assertNotIn('namespace', document['metadata'])
                        self.assertEqual(document['metadata']['labels']['app.kubernetes.io/part-of'], 'p4-kdu-upgrade')
                    config = next(d for d in documents if d['kind'] == 'ConfigMap')
                    self.assertEqual((config['metadata']['name'], config['data']['release'], config['data']['chart']),
                                     ('p4kdu-' + kdu + '-release', settings[kdu]['marker'], settings[kdu]['version']))
                    pod = next(d for d in documents if d['kind'] == 'Deployment')['spec']['template']['spec']
                    container, = pod['containers']
                    self.assertRegex(container['image'], r'^docker\.io/library/busybox:1\.38\.0@sha256:[0-9a-f]{64}$')
                    self.assertEqual(container['imagePullPolicy'], 'IfNotPresent')
                    self.assertEqual(container['resources']['limits'], {'cpu': '50m', 'memory': '32Mi'})
                    self.assertFalse(pod['automountServiceAccountToken'])
                    self.assertTrue(pod['securityContext']['runAsNonRoot'])
                    for forbidden in ('hostNetwork', 'hostPID', 'hostIPC', 'nodeSelector', 'tolerations'):
                        self.assertNotIn(forbidden, pod)
                    probe = container['readinessProbe']
                    if settings[kdu].get('broken'):
                        self.assertEqual(probe['exec']['command'], ['false'])
                    else:
                        self.assertIn('"' + settings[kdu]['marker'] + '"', probe['exec']['command'][2])
                    self.assertEqual(probe['initialDelaySeconds'], settings[kdu].get('readyDelaySeconds', 2))


def config(work):
    return {'target_address': TARGET, 'osm_url': 'https://nbi.172.30.0.20.nip.io:30843', 'osm_user': 'admin',
            'osm_password_file': '/unused', 'osm_project': 'admin', 'vim_account': 'dell-dummy-vim',
            'k8s_cluster': 'dell-cluster', 'kubeconfig': str(work / 'kubeconfig'),
            'allowed_api_addresses': [TARGET], 'allowed_node_addresses': [TARGET], 'work_dir': str(work),
            'osm_namespace': 'osm', 'kubectl': '/usr/bin/kubectl', 'helm': '/usr/local/bin/helm',
            'timeouts': {'kdu_timeout': 150, 'op_timeout': 900, 'health_timeout': 300, 'poll': 5, 'clock_skew': 10}}


class Harness(unittest.TestCase):
    COMMIT = 'e' * 40

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Write ordering is checked by the P1 tests; disk durability is not simulated here.
        fsync = mock.patch('local_safety.os.fsync')
        fsync.start()
        self.addCleanup(fsync.stop)
        self.work = Path(self.temp.name)
        (self.work / 'kubeconfig').write_text(yaml.safe_dump({'clusters': [{'cluster': {'server': 'https://172.30.0.20:6443'}}]}))
        self.clock = FakeClock()
        self.target = self.make_target()
        self.config = config(self.work)
        self.resolved = {'nbi.172.30.0.20.nip.io': {TARGET}, '172.30.0.20': {TARGET}}

    def make_target(self, **kwargs):
        return P4Target(self.clock, **kwargs)

    def experiment(self, request=None):
        cluster = p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', self.config['kubeconfig'], 'osm',
                             run=self.target.run)
        evidence = p4.Evidence(self.work / 'evidence', clock=self.clock)
        return p4.Experiment(self.config, request or self.target.request, cluster, evidence, clock=self.clock,
                             commit=self.COMMIT, resolver=lambda host: self.resolved[host])


class TargetGuardTests(Harness):
    def refused(self, change):
        change()
        with self.assertRaises(p4.TargetError):
            self.experiment().verify_target()
        self.assertEqual(self.target.mutations, [])

    def test_isolated_target_is_accepted(self):
        result = self.experiment().verify_target()
        self.assertEqual(result['osm_addresses'], [TARGET])

    def test_hp_as_configured_target(self):
        self.config.update(target_address='172.30.18.32')
        self.resolved['nbi.172.30.0.20.nip.io'] = {'172.30.18.32'}
        with self.assertRaisesRegex(p4.TargetError, 'HP production system'):
            self.experiment().verify_target()
        self.assertEqual(self.target.mutations, [])

    def test_osm_endpoint_resolving_to_the_hp(self):
        self.refused(lambda: self.resolved.update({'nbi.172.30.0.20.nip.io': {'172.30.18.32'}}))

    def test_osm_endpoint_resolving_elsewhere(self):
        self.refused(lambda: self.resolved.update({'nbi.172.30.0.20.nip.io': {TARGET, '10.0.0.9'}}))

    def test_kubernetes_api_of_the_hp(self):
        def change():
            (self.work / 'kubeconfig').write_text(yaml.safe_dump(
                {'clusters': [{'cluster': {'server': 'https://172.30.18.32:6443'}}]}))
            self.resolved['172.30.18.32'] = {'172.30.18.32'}
        self.refused(change)

    def test_hp_address_cannot_be_allowed(self):
        self.refused(lambda: self.config['allowed_api_addresses'].append('172.30.18.32'))

    def test_hp_node_name_or_address(self):
        original = self.target._kubectl
        def nodes(args):
            if args[:2] == ['get', 'nodes']:
                return {'items': [{'metadata': {'name': 'rclab-hp-406-g1-mt'},
                                   'status': {'addresses': [{'type': 'InternalIP', 'address': TARGET}]}}]}
            return original(args)
        self.refused(lambda: setattr(self.target, '_kubectl', nodes))

    def test_unexpected_node_address(self):
        self.refused(lambda: self.config.update(allowed_node_addresses=['172.30.0.99']))

    def test_osm_with_hp_identities(self):
        self.refused(lambda: self.target.project.update(_id='c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c'))


class ClusterScopeTests(unittest.TestCase):
    def setUp(self):
        self.cluster = p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', '/k', 'osm',
                                  run=lambda *a, **k: subprocess.CompletedProcess(a, 0, b'{}', b''))

    def test_only_experiment_mutations_are_possible(self):
        k, h = self.cluster.kubectl, self.cluster.helm
        allowed = [k('get', 'pods', '-n', 'anything', '-o', 'json'), k('delete', 'namespace', 'p4-kdu-upgrade'),
                   k('rollout', 'restart', 'deployment/lcm', '-n', 'osm'),
                   h('status', 'p4kdu-cu', '-n', 'p4-kdu-upgrade'), h('rollback', 'p4kdu-du', '-n', 'p4-kdu-upgrade')]
        refused = [k('delete', 'namespace', 'osm'), k('delete', 'pod', 'x', '-n', 'p4-kdu-upgrade'),
                   k('apply', '-f', 'x'), k('rollout', 'restart', 'deployment/nbi', '-n', 'osm'),
                   k('rollout', 'restart', 'deployment/lcm', '-n', 'default'), k('scale', 'deployment/lcm'),
                   h('rollback', 'cu-0010984531', '-n', 'p4-kdu-upgrade'), h('rollback', 'p4kdu-cu', '-n', 'osm'),
                   h('upgrade', 'p4kdu-cu', 'x', '-n', 'p4-kdu-upgrade'), h('uninstall', 'p4kdu-cu', '-n', 'p4-kdu-upgrade'),
                   h('install', 'x', 'y'), h('status', 'osm', '-n', 'osm'), h('list', '-A')]
        for argv in allowed:
            self.cluster._run(argv)
        for argv in refused:
            with self.subTest(argv=argv), self.assertRaises(p4.ClusterError):
                self.cluster._run(argv)


class GateTests(Harness):
    def setUp(self):
        super().setUp()
        self.exp = self.experiment()
        self.exp.run(('preflight', 'onboard', 'instantiate'))

    def test_gate_is_open_only_inside_and_only_for_the_experiment_ns(self):
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        with self.exp.gate() as osm:
            self.assertEqual(lifecycle.require_p4_approval()['osm_lcm_digest'], LCM_DIGEST)
            with self.assertRaises(p4.ExperimentError):
                osm.kdu_action(self.target.other_ns, 'p4kdu', 'cu', 'rollback', {})
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        with self.assertRaises(RuntimeError), self.exp.gate():
            raise RuntimeError('boom')
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        with self.assertRaises(lifecycle.UpgradeNotApproved):
            NbiLifecycle(self.target.request).kdu_action(self.exp.ns_id, 'p4kdu', 'cu', 'rollback', {})

    def test_gate_refuses_a_non_isolated_ns(self):
        ns = self.target.instances[self.exp.ns_id]
        ns['_admin']['deployed']['K8s'][0]['namespace'] = 'c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c'
        with self.assertRaises(p4.ExperimentError), self.exp.gate():
            pass
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)

    def test_osm_mutations_only_name_experiment_identities(self):
        with self.assertRaises(p4.ExperimentError):
            self.exp.osm.terminate(self.target.other_ns)
        with self.assertRaises(p4.ExperimentError):
            self.exp.osm.delete_package('knf', 'fran_oai_knf')


class FullRunTests(Harness):
    def assert_clean_run(self, summary, skipped_restart=False):
        failed = [c for c in summary['checks'] if c['passed'] is False]
        self.assertEqual(failed, [])
        expected = list(p4.STEPS) if not skipped_restart else list(p4.STEPS[:-1])
        self.assertEqual([s['step'] for s in summary['steps'] if s['result'] == 'passed'], expected)
        self.assertGreater(summary['passed'], 60)
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        # The unrelated NS was never touched and no OSM mutation named it.
        self.assertFalse([m for m in self.target.mutations if self.target.other_ns in m[1]])
        self.assertEqual(self.target.instances[self.target.other_ns]['nsState'], 'READY')
        evidence = self.work / 'evidence'
        for name, digest in summary['evidence_sha256'].items():
            self.assertEqual(hashlib.sha256((evidence / name).read_bytes()).hexdigest(), digest)

    def test_complete_experiment_with_lcm_restart_resuming(self):
        exp = self.experiment()
        summary = exp.run(allow_lcm_restart=True)
        self.assert_clean_run(summary)
        ops = [o for o in self.target.operations.values() if o['nsInstanceId'] == exp.ns_id]
        upgrades = [o for o in ops if o['operationParams'].get('primitive') == 'upgrade']
        self.assertTrue(all(o['operationParams']['timeout_ns_action'] == 150 for o in upgrades))
        self.assertTrue(all(isinstance(o['operationParams']['primitive_params'], str) for o in upgrades))
        self.assertEqual({lifecycle.primitive_params(o)['kdu_model'].split(':')[1].split('/')[0]
                          for o in upgrades}, {'2', '3'})
        observed = [c for c in summary['checks'] if c['passed'] is None]
        self.assertTrue(any('kdu-model' in c['check'] for c in observed))

    def test_lcm_restart_failing_the_operation_is_recovered(self):
        self.target.lcm_restart_mode = 'fails'
        summary = self.experiment().run(allow_lcm_restart=True)
        self.assert_clean_run(summary)
        recovery = json.loads((self.work / 'evidence/lcm_restart_recovery/recovery.json').read_text())
        self.assertIn(['helm-rollback', 'p4kdu-cu'], recovery)

    def test_lcm_restart_leaving_the_operation_stuck_is_reported(self):
        self.target.lcm_restart_mode = 'stuck'
        summary = self.experiment().run(allow_lcm_restart=True)
        failed = [c['check'] for c in summary['checks'] if c['passed'] is False]
        self.assertEqual(summary['steps'][-1]['step'], 'lcm_restart_recovery')
        self.assertIn('NS READY and idle', failed)

    def test_restart_step_is_opt_in(self):
        summary = self.experiment().run()
        self.assert_clean_run(summary, skipped_restart=True)
        self.assertEqual(summary['steps'][-1]['result'], 'skipped (requires --allow-lcm-restart)')
        self.assertFalse([m for m in self.target.mutations if 'rollout' in m[1]])
        self.assertEqual(self.target.lcm_generation, 1)

    def test_broken_atomic_rollback_is_detected(self):
        self.target.atomic = False
        summary = self.experiment().run()
        self.assertEqual(summary['steps'][-1], dict(summary['steps'][-1], step='failed_upgrade_auto_rollback',
                                                    result='failed'))
        failed = [c['check'] for c in summary['checks'] if c['passed'] is False]
        self.assertIn('helm --atomic recorded the failed DU revision and rolled it back', failed)

    def test_ineffective_rollback_is_detected(self):
        self.target.rollback_works = False
        summary = self.experiment().run()
        self.assertEqual((summary['steps'][-1]['step'], summary['steps'][-1]['result']), ('explicit_rollback', 'failed'))

    def test_leftovers_stop_the_run_before_any_mutation(self):
        self.experiment().run(('preflight', 'onboard'))
        before = list(self.target.mutations)
        summary = self.experiment().run()
        self.assertEqual(summary['steps'][0]['result'], 'error')
        self.assertEqual(self.target.mutations, before)

    def test_teardown_removes_only_experiment_resources(self):
        exp = self.experiment()
        exp.run()
        summary = self.experiment().teardown()
        self.assertEqual([c for c in summary['checks'] if c['passed'] is False], [])
        self.assertNotIn('p4-kdu-upgrade', self.target.namespaces)
        self.assertFalse(self.target.packages)
        self.assertEqual(list(self.target.instances), [self.target.other_ns])
        self.assertIn('osm', self.target.namespaces)

    def test_teardown_waits_for_pods_of_an_uninstalled_release(self):
        self.experiment().run(('preflight', 'onboard', 'instantiate'))
        original, lingering = self.target._kubectl, [2]
        def kubectl(args):
            value = original(args)
            if 'pods' in args[1].split(',') and not self.target.releases and lingering[0]:
                lingering[0] -= 1      # pod of the deleted ReplicaSet still terminating
                value['items'].append({'kind': 'Pod', 'metadata': {
                    'name': 'p4kdu-cu-584b65f7bc-9fn6v', 'uid': 'u', 'deletionTimestamp': '2026-10-22T10:00:00Z',
                    'ownerReferences': [{'kind': 'ReplicaSet', 'name': 'p4kdu-cu-584b65f7bc', 'uid': 'gone',
                                         'controller': True}]}})
            return value
        self.target._kubectl = kubectl
        summary = self.experiment().teardown()
        self.assertEqual([c for c in summary['checks'] if c['passed'] is False], [])
        self.assertEqual(lingering, [0])
        self.assertNotIn('p4-kdu-upgrade', self.target.namespaces)

    def test_teardown_keeps_a_namespace_with_foreign_objects(self):
        self.experiment().run(('preflight', 'onboard', 'instantiate'))
        self.target.extra_objects['p4-kdu-upgrade'] = [('Deployment', 'someone-else')]
        before = list(self.target.mutations)
        summary = self.experiment().teardown()
        failed = [c for c in summary['checks'] if c['passed'] is False]
        self.assertEqual([c['check'] for c in failed], ['teardown completed'])
        self.assertIn('non-experiment', failed[0]['detail'])
        self.assertEqual(self.target.mutations, before)     # nothing terminated or deleted
        self.assertIn('p4-kdu-upgrade', self.target.namespaces)


# Helm 3 `-o json` output for the revision-1 deployment on the Dell (p4kdu-cu-0.1.0 / r1,
# p4kdu-du-0.1.0 / r1). helm status there carries no chart object, which broke the
# original status['chart']['metadata']['version'] read.
DELL_STATUS = {release: {
    'name': release, 'namespace': 'p4-kdu-upgrade', 'version': 1, 'config': {},
    'info': {'first_deployed': '2026-10-22T09:14:03.513811273Z', 'last_deployed': '2026-10-22T09:14:03.513811273Z',
             'deleted': '', 'description': 'Install complete', 'status': 'deployed'}}
    for release in ('p4kdu-cu', 'p4kdu-du')}
DELL_HISTORY = {release: [{'revision': 1, 'updated': '2026-10-22T09:14:03.513811273Z', 'status': 'deployed',
                           'chart': release + '-0.1.0', 'app_version': 'r1', 'description': 'Install complete'}]
                for release in ('p4kdu-cu', 'p4kdu-du')}
DELL_LIST = [{'name': release, 'namespace': 'p4-kdu-upgrade', 'revision': '1',
              'updated': '2026-10-22 09:14:03.513811273 +0000 UTC', 'status': 'deployed',
              'chart': release + '-0.1.0', 'app_version': 'r1'} for release in ('p4kdu-cu', 'p4kdu-du')]


class HelmMetadataTests(unittest.TestCase):
    def view(self, release='p4kdu-cu', status=None, history=None, listing=None):
        return p4.release_view(release, DELL_STATUS[release] if status is None else status,
                               DELL_HISTORY[release] if history is None else history,
                               DELL_LIST if listing is None else listing)

    def rejected(self, pattern, **changes):
        with self.assertRaisesRegex(p4.HelmMetadataError, pattern):
            self.view(**changes)

    def test_dell_revision_1_output(self):
        self.assertNotIn('chart', DELL_STATUS['p4kdu-cu'])
        for release in ('p4kdu-cu', 'p4kdu-du'):
            view = self.view(release)
            self.assertEqual({k: v for k, v in view.items() if k != 'history'},
                             {'version': 1, 'state': 'deployed', 'chart': '0.1.0', 'chart_ref': release + '-0.1.0',
                              'app_version': 'r1'})
            self.assertEqual([(h['revision'], h['chart_version'], h['app_version']) for h in view['history']],
                             [(1, '0.1.0', 'r1')])

    def test_absent_everywhere_is_missing_not_an_error(self):
        self.assertIsNone(p4.release_view('p4kdu-cu', None, [], [DELL_LIST[1]]))

    def test_partially_known_release_is_rejected(self):
        self.rejected('only some', listing=[DELL_LIST[1]])
        self.rejected('only some', history=[])
        with self.assertRaisesRegex(p4.HelmMetadataError, 'only some'):
            p4.release_view('p4kdu-cu', None, DELL_HISTORY['p4kdu-cu'], DELL_LIST)

    def test_ambiguous_list_is_rejected(self):
        self.rejected('ambiguous', listing=DELL_LIST + [dict(DELL_LIST[0])])

    def test_non_list_output_is_rejected(self):
        self.rejected('not a JSON list', listing={'releases': DELL_LIST})
        self.rejected('not a JSON list', history={'revision': 1})

    def test_name_and_namespace_must_match(self):
        self.rejected('name or namespace', status=dict(DELL_STATUS['p4kdu-cu'], name='p4kdu-du'))
        self.rejected('name or namespace', status=dict(DELL_STATUS['p4kdu-cu'], namespace='osm'))
        self.rejected('name or namespace', listing=[dict(DELL_LIST[0], namespace='default'), DELL_LIST[1]])

    def test_chart_field_must_be_this_release_chart_with_a_plain_version(self):
        for chart in ('p4kdu-du-0.1.0', 'p4kdu-cu-0.1.0-rc1', 'p4kdu-cu', 'p4kdu-cu-0.1', 'cu-0.1.0',
                      'xp4kdu-cu-0.1.0', '', None, 1):
            with self.subTest(chart=chart):
                self.rejected('chart field', history=[dict(DELL_HISTORY['p4kdu-cu'][0], chart=chart)])
        with self.assertRaisesRegex(p4.HelmMetadataError, 'chart field'):
            p4.chart_version('ran-fran-oai', 'ran-fran-oai-0.1.0')

    def test_every_history_entry_must_belong_to_the_release(self):
        history = [dict(DELL_HISTORY['p4kdu-cu'][0], chart='other-0.1.0', status='superseded'),
                   dict(DELL_HISTORY['p4kdu-cu'][0], revision=2)]
        self.rejected('chart field', history=history,
                      status=dict(DELL_STATUS['p4kdu-cu'], version=2),
                      listing=[dict(DELL_LIST[0], revision='2')])

    def test_missing_fields_are_rejected(self):
        self.rejected('info.status', status=dict(DELL_STATUS['p4kdu-cu'], info={}))
        self.rejected('malformed', history=[{k: v for k, v in DELL_HISTORY['p4kdu-cu'][0].items() if k != 'app_version'}])
        self.rejected('malformed', history=[{k: v for k, v in DELL_HISTORY['p4kdu-cu'][0].items() if k != 'status'}])
        self.rejected('invalid revision in helm status', status={k: v for k, v in DELL_STATUS['p4kdu-cu'].items()
                                                                 if k != 'version'})
        for revision in ('01', '1.0', '', 0, True, None):
            with self.subTest(revision=revision):
                self.rejected('invalid revision in helm list', listing=[dict(DELL_LIST[0], revision=revision)])

    def test_inconsistent_sources_are_rejected(self):
        cu = DELL_LIST[0]
        self.rejected('status and latest history', status=dict(DELL_STATUS['p4kdu-cu'], version=2))
        self.rejected('status and latest history', status=dict(DELL_STATUS['p4kdu-cu'], info={'status': 'failed'}))
        for change in ({'revision': '2'}, {'status': 'failed'}, {'chart': 'p4kdu-cu-0.2.0'}, {'app_version': 'r2'}):
            with self.subTest(change=change):
                self.rejected('list and helm history', listing=[dict(cu, **change), DELL_LIST[1]])

    def test_history_order_and_uniqueness(self):
        entry = DELL_HISTORY['p4kdu-cu'][0]
        self.rejected('duplicated or out of order', history=[entry, dict(entry)])
        self.rejected('duplicated or out of order', history=[dict(entry, revision=2), entry])

    def test_latest_history_entry_gives_the_current_chart(self):
        history = [dict(DELL_HISTORY['p4kdu-du'][0], status='superseded'),
                   dict(DELL_HISTORY['p4kdu-du'][0], revision=2, chart='p4kdu-du-0.2.0', app_version='r2',
                        description='Upgrade complete'),
                   dict(DELL_HISTORY['p4kdu-du'][0], revision=3, status='failed', chart='p4kdu-du-0.3.0',
                        app_version='r3', description='Upgrade "p4kdu-du" failed: context deadline exceeded'),
                   dict(DELL_HISTORY['p4kdu-du'][0], revision=4, chart='p4kdu-du-0.2.0', app_version='r2',
                        description='Rollback to 2')]
        view = self.view('p4kdu-du', status=dict(DELL_STATUS['p4kdu-du'], version=4), history=history,
                         listing=[dict(DELL_LIST[1], revision='4', chart='p4kdu-du-0.2.0', app_version='r2')])
        self.assertEqual((view['version'], view['chart'], view['app_version']), (4, '0.2.0', 'r2'))
        self.assertEqual([h['chart_version'] for h in view['history']], ['0.1.0', '0.2.0', '0.3.0', '0.2.0'])

    def test_pending_release_is_reported_consistently(self):
        history = [dict(DELL_HISTORY['p4kdu-cu'][0], status='superseded'),
                   dict(DELL_HISTORY['p4kdu-cu'][0], revision=2, status='pending-upgrade', chart='p4kdu-cu-0.3.0',
                        app_version='r3', description='Preparing upgrade')]
        view = self.view(status=dict(DELL_STATUS['p4kdu-cu'], version=2, info={'status': 'pending-upgrade'}),
                         history=history, listing=[dict(DELL_LIST[0], revision='2', status='pending-upgrade',
                                                        chart='p4kdu-cu-0.3.0', app_version='r3')])
        self.assertEqual((view['state'], view['chart']), ('pending-upgrade', '0.3.0'))

    def test_chart_object_in_status_is_cross_checked_when_present(self):
        status = dict(DELL_STATUS['p4kdu-cu'], chart={'metadata': {'name': 'p4kdu-cu', 'version': '0.1.0',
                                                                   'appVersion': 'r1'}})
        self.assertEqual(self.view(status=status)['chart'], '0.1.0')
        for metadata in ({'name': 'p4kdu-cu', 'version': '0.2.0', 'appVersion': 'r1'},
                         {'name': 'p4kdu-du', 'version': '0.1.0', 'appVersion': 'r1'},
                         {'name': 'p4kdu-cu', 'version': '0.1.0', 'appVersion': 'r2'}, None):
            with self.subTest(metadata=metadata):
                self.rejected('contradicts', status=dict(status, chart={'metadata': metadata}))
        self.rejected('contradicts', status=dict(status, chart={}))

    def cluster(self, outputs):
        """Cluster whose helm calls return the next prepared output for each verb."""
        def run(argv, **_):
            return subprocess.CompletedProcess(argv, 0, json.dumps(outputs[argv[1]].pop(0)).encode(), b'')
        return p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', '/k', 'osm', run=run)

    def test_cluster_reads_status_history_and_list(self):
        cluster = self.cluster({'status': [DELL_STATUS['p4kdu-cu']], 'history': [DELL_HISTORY['p4kdu-cu']],
                                'list': [DELL_LIST]})
        self.assertEqual(cluster.release_view('p4kdu-cu')['chart_ref'], 'p4kdu-cu-0.1.0')
        self.assertEqual([c[1:3] for c in cluster.commands], [['status', 'p4kdu-cu'], ['history', 'p4kdu-cu'],
                                                              ['list', '-n']])
        self.assertTrue(all(c[c.index('-n') + 1] == 'p4-kdu-upgrade' and c[-2:] == ['-o', 'json']
                            for c in cluster.commands))

    def test_cluster_rereads_once_then_rejects(self):
        moving = [dict(DELL_LIST[0], revision='2', chart='p4kdu-cu-0.2.0', app_version='r2')]
        def outputs(lists):
            return {'status': [DELL_STATUS['p4kdu-cu']] * 2, 'history': [DELL_HISTORY['p4kdu-cu']] * 2, 'list': lists}
        self.assertEqual(self.cluster(outputs([moving, DELL_LIST])).release_view('p4kdu-cu')['version'], 1)
        with self.assertRaisesRegex(p4.HelmMetadataError, 'helm list and helm history'):
            self.cluster(outputs([moving, moving])).release_view('p4kdu-cu')


class ExpectReleaseTests(Harness):
    """A release passes only when Helm (state, chart, chart ref, app version), the
    ConfigMap marker and the Deployment all agree and the Helm view had no error."""
    def snap(self, **helm):
        status = dict({'version': 1, 'state': 'deployed', 'chart': '0.1.0', 'chart_ref': 'p4kdu-du-0.1.0',
                       'app_version': 'r1'}, **helm)
        return {'helm': {'du': {'status': status, 'history': [], 'error': None}},
                'markers': {'du': 'r1'}, 'deployments': {'du': 'ready'}}

    def expect(self, snap):
        return self.experiment().expect_release('s', snap, 'du', '0.1.0', 'r1', 'baseline')

    def test_consistent_release_passes(self):
        self.assertTrue(self.expect(self.snap()))

    def test_each_disagreement_fails(self):
        cases = {'app version': self.snap(app_version='r2'), 'chart': self.snap(chart='0.2.0'),
                 'chart ref': self.snap(chart_ref='p4kdu-cu-0.1.0'), 'state': self.snap(state='failed'),
                 'marker': dict(self.snap(), markers={'du': 'r2'}),
                 'deployment': dict(self.snap(), deployments={'du': 'not ready'})}
        errored = self.snap()
        errored['helm']['du']['error'] = 'p4kdu-du: helm list and helm history disagree'
        cases['helm error'] = errored
        missing = self.snap()
        missing['helm']['du']['status'] = None
        cases['missing release'] = missing
        for name, snap in cases.items():
            with self.subTest(name):
                self.assertFalse(self.expect(snap))


class BaselineValidationTests(Harness):
    """validate-baseline on a deployment left by the instantiate step (as on the Dell)."""
    def setUp(self):
        super().setUp()
        self.first = self.experiment()
        self.first.run(('preflight', 'onboard', 'instantiate'))
        self.state = self.first.state_path.read_bytes()
        self.mutations = list(self.target.mutations)

    def validate(self):
        self.config = config(self.work)
        exp = self.experiment()
        exp.evidence = p4.Evidence(self.work / 'evidence-baseline', clock=self.clock)
        summary = exp.validate_baseline()
        self.assertEqual(self.target.mutations, self.mutations)
        self.assertEqual(exp.state_path.read_bytes() if exp.state_path.exists() else None, self.state)
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        return summary, [c['check'] for c in summary['checks'] if c['passed'] is False]

    def test_instantiate_baseline_checks_pass_with_dell_helm_output(self):
        status = json.loads(self.target.run(['/usr/local/bin/helm', 'status', 'p4kdu-cu', '-n', 'p4-kdu-upgrade',
                                             '-o', 'json']).stdout)
        self.assertNotIn('chart', status)
        baseline = [c for c in self.first.evidence.checks if c['check'].startswith('baseline: ')]
        self.assertEqual([(c['check'], c['passed']) for c in baseline],
                         [('baseline: cu serves r1', True), ('baseline: du serves r1', True)])
        self.assertEqual(baseline[0]['detail']['helm']['chart_ref'], 'p4kdu-cu-0.1.0')

    def test_existing_revision_1_deployment_validates_read_only(self):
        summary, failed = self.validate()
        self.assertEqual(failed, [])
        self.assertEqual(summary['steps'][0]['result'], 'passed')
        self.assertEqual((summary['ns_id'], summary['read_only']), (self.first.ns_id, True))
        self.assertGreaterEqual(summary['passed'], 14)

    def test_inconsistent_helm_metadata_fails_without_guessing(self):
        original = self.target._helm
        def helm(args):
            value = original(args)
            if args[0] == 'list':
                value = [dict(r, app_version='r2') if r['name'] == 'p4kdu-du' else r for r in value]
            return value
        self.target._helm = helm
        summary, failed = self.validate()
        self.assertIn('baseline: du serves r1', failed)
        self.assertNotIn('baseline: cu serves r1', failed)
        detail = next(c['detail'] for c in summary['checks'] if c['check'] == 'baseline: du serves r1')
        self.assertIn('helm list and helm history disagree', detail['helm_error'])

    def test_upgraded_deployment_is_not_a_baseline(self):
        self.first.run(('upgrade_rev2_lost_response',))
        self.state, self.mutations = self.first.state_path.read_bytes(), list(self.target.mutations)
        _, failed = self.validate()
        self.assertIn('baseline: cu serves r1', failed)
        self.assertIn('catalog holds revision 1 of the experiment KNF and NSD', failed)
        self.assertIn('P1 state names this NS as active with nothing pending', failed)

    def test_baseline_state_is_the_instantiation_state(self):
        state = yaml.safe_load(self.state)
        self.assertEqual(sorted(state['osm']), ['active_instance_id', 'active_operation_id', 'active_scenario'])
        summary, failed = self.validate()
        self.assertEqual(failed, [])
        self.assertIn('P1 state names this NS as active with nothing pending',
                      [c['check'] for c in summary['checks'] if c['passed'] is True])

    def test_missing_state_is_recorded_not_rejected(self):
        self.first.state_path.unlink()
        self.state = None
        summary, failed = self.validate()
        self.assertEqual(failed, [])
        self.assertIn('P1 state not present (re-established before any upgrade)',
                      [c['check'] for c in summary['checks'] if c['passed'] is None])

    def test_state_of_another_ns_or_with_pending_work_fails(self):
        for change in ({'active_instance_id': self.target.other_ns},
                       {'pending_update': {'stage': 'submitted'}}):
            with self.subTest(change=change):
                state = yaml.safe_load(self.state)
                state['osm'].update(change)
                self.first.state_path.write_text(yaml.safe_dump(state))
                self.state = self.first.state_path.read_bytes()
                _, failed = self.validate()
                self.assertEqual(failed, ['P1 state names this NS as active with nothing pending'])

    def test_state_bound_to_another_context_fails(self):
        state = yaml.safe_load(self.state)
        state['context']['namespace_uid'] = '00000000-0000-4000-8000-000000000000'
        self.first.state_path.write_text(yaml.safe_dump(state))
        self.state = self.first.state_path.read_bytes()
        _, failed = self.validate()
        self.assertEqual(failed, ['P1 state names this NS as active with nothing pending'])

    def test_hp_target_is_refused_before_reading(self):
        self.config = config(self.work)
        self.resolved['nbi.172.30.0.20.nip.io'] = {'172.30.18.32'}
        exp = self.experiment()
        summary = exp.validate_baseline()
        failed = [c for c in summary['checks'] if c['passed'] is False]
        self.assertEqual([c['check'] for c in failed], ['baseline readable'])
        self.assertIn('TargetError', failed[0]['detail'])
        self.assertEqual(self.target.mutations, self.mutations)


# Namespace p4-kdu-upgrade on the Dell after the revision-1 instantiation (object names
# as reported by validate-baseline; metadata as Kubernetes and Helm 3 set it).
DELL_INVENTORY_NAMES = [
    ('ConfigMap', 'kube-root-ca.crt'), ('ConfigMap', 'p4kdu-cu-release'), ('ConfigMap', 'p4kdu-du-release'),
    ('Deployment', 'p4kdu-cu'), ('Deployment', 'p4kdu-du'),
    ('Pod', 'p4kdu-cu-584b65f7bc-9fn6v'), ('Pod', 'p4kdu-du-67647b8b96-krkqf'),
    ('ReplicaSet', 'p4kdu-cu-584b65f7bc'), ('ReplicaSet', 'p4kdu-du-67647b8b96'),
    ('Secret', 'sh.helm.release.v1.p4kdu-cu.v1'), ('Secret', 'sh.helm.release.v1.p4kdu-du.v1')]


def dell_items():
    return ([kube_root_ca()] + helm_release_objects('p4-kdu-upgrade', 'p4kdu-cu', [1], '584b65f7bc', '9fn6v') +
            helm_release_objects('p4-kdu-upgrade', 'p4kdu-du', [1], '67647b8b96', 'krkqf'))


class NamespaceInventoryTests(unittest.TestCase):
    def inventory(self, items):
        return [p4.inventory_item(i) for i in items]

    def foreign(self, items):
        return [(kind, name) for kind, name, _ in p4.foreign_objects(self.inventory(items))]

    def find(self, items, kind, name):
        return next(i for i in items if i['kind'] == kind and i['metadata']['name'] == name)

    def test_dell_revision_1_namespace_is_accepted(self):
        items = dell_items()
        self.assertEqual(sorted((i['kind'], i['metadata']['name']) for i in items), DELL_INVENTORY_NAMES)
        self.assertEqual(self.foreign(items), [])

    def test_default_service_account_is_accepted_only_as_created_by_kubernetes(self):
        from p4_sim import default_service_account
        self.assertEqual(self.foreign(dell_items() + [default_service_account()]), [])
        labelled = default_service_account()
        labelled['metadata']['labels'] = {'app': 'x'}
        owned = default_service_account()
        owned['metadata']['ownerReferences'] = [{'kind': 'Deployment', 'name': 'x', 'uid': 'y', 'controller': True}]
        robot = default_service_account()
        robot['metadata']['name'] = 'robot'
        for item in (labelled, owned, robot):
            with self.subTest(item=item['metadata']):
                self.assertEqual(self.foreign(dell_items() + [item]), [('ServiceAccount', item['metadata']['name'])])

    def test_previous_prefix_predicate_rejected_only_the_helm_secrets(self):
        names = [(i['kind'], i['metadata']['name']) for i in dell_items()]
        old = [n for n in names if not (n[1].startswith(('p4kdu-cu', 'p4kdu-du')) or n[1] == 'kube-root-ca.crt')]
        self.assertEqual(old, [('Secret', 'sh.helm.release.v1.p4kdu-cu.v1'), ('Secret', 'sh.helm.release.v1.p4kdu-du.v1')])

    def test_inventory_item_keeps_identity_metadata_only(self):
        items = dell_items()
        reduced = json.dumps(self.inventory(items))
        for leaked in ('H4sI', 'PEM', 'checksum/config', 'kubernetes.io/description', 'p4kdu/release', '"data"'):
            self.assertNotIn(leaked, reduced)
        secret = self.inventory([self.find(items, 'Secret', 'sh.helm.release.v1.p4kdu-cu.v1')])[0]
        self.assertEqual((secret['type'], secret['labels']['owner'], secret['owners']), ('helm.sh/release.v1', 'helm', []))

    def test_cluster_inventory_lists_every_discovered_kind_and_drops_data(self):
        from p4_sim import API_RESOURCES
        def run(argv, **_):
            if argv[1] == 'api-resources':
                return subprocess.CompletedProcess(argv, 0, '\n'.join(API_RESOURCES).encode(), b'')
            return subprocess.CompletedProcess(argv, 0, json.dumps({'items': dell_items()}).encode(), b'')
        cluster = p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', '/k', 'osm', run=run)
        inventory = cluster.inventory()
        self.assertEqual([(i['kind'], i['name']) for i in inventory], DELL_INVENTORY_NAMES)
        self.assertNotIn('H4sI', json.dumps(inventory))
        self.assertEqual(cluster.commands[0][1:], ['api-resources', '--namespaced=true', '--verbs=list', '-o', 'name'])
        kinds = cluster.commands[1][2].split(',')
        self.assertEqual(sorted(kinds), sorted(k for k in API_RESOURCES if k not in
                                               ('events', 'events.events.k8s.io', 'pods.metrics.k8s.io')))
        self.assertIn('servicemonitors.monitoring.coreos.com', kinds)       # CRDs are inventoried too
        self.assertEqual(cluster.commands[1][3:], ['-n', 'p4-kdu-upgrade', '-o', 'json'])

    def test_incomplete_discovery_fails_closed(self):
        from p4_sim import API_RESOURCES
        def cluster(returncode, names):
            return p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', '/k', 'osm', run=lambda argv, **_:
                              subprocess.CompletedProcess(argv, returncode, '\n'.join(names).encode(), b'error: partial'))
        with self.assertRaisesRegex(p4.ClusterError, 'discovery incomplete: error: partial'):
            cluster(1, API_RESOURCES).inventory()
        for missing in ('roles.rbac.authorization.k8s.io', 'pods', 'deployments.apps', 'secrets'):
            with self.subTest(missing=missing), self.assertRaisesRegex(p4.ClusterError, 'missing ' + re.escape(missing)):
                cluster(0, [k for k in API_RESOURCES if k != missing]).inventory()

    def test_rollout_with_old_and_new_replicasets_is_accepted(self):
        items = dell_items() + [i for i in helm_release_objects('p4-kdu-upgrade', 'p4kdu-cu', [1, 2], '7d9c8b6f5a', 'abcde')
                                if i['kind'] in ('ReplicaSet', 'Pod') or i['metadata']['name'].endswith('.v2')]
        self.assertEqual(self.foreign(items), [])

    def mutated(self, kind, name, change):
        items = copy.deepcopy(dell_items())
        change(self.find(items, kind, name))
        return self.foreign(items)

    def test_tampered_experiment_objects_are_rejected(self):
        cu_rs, cu_pod = 'p4kdu-cu-584b65f7bc', 'p4kdu-cu-584b65f7bc-9fn6v'
        cases = {
            'root CA with an owner': ('ConfigMap', 'kube-root-ca.crt',
                                      lambda i: i['metadata'].update(ownerReferences=[{'kind': 'X', 'controller': True}])),
            'release ConfigMap of another Helm release': ('ConfigMap', 'p4kdu-cu-release', lambda i: i['metadata'][
                'annotations'].update({'meta.helm.sh/release-name': 'other'})),
            'release ConfigMap in another release namespace': ('ConfigMap', 'p4kdu-cu-release', lambda i: i['metadata'][
                'annotations'].update({'meta.helm.sh/release-namespace': 'osm'})),
            'release ConfigMap not managed by Helm': ('ConfigMap', 'p4kdu-cu-release',
                                                      lambda i: i['metadata']['labels'].pop('app.kubernetes.io/managed-by')),
            'release ConfigMap without chart labels': ('ConfigMap', 'p4kdu-cu-release',
                                                       lambda i: i['metadata']['labels'].pop('app.kubernetes.io/part-of')),
            'Deployment of another instance': ('Deployment', 'p4kdu-cu', lambda i: i['metadata']['labels'].update(
                {'app.kubernetes.io/instance': 'p4kdu-du'})),
            'Deployment without Helm annotations': ('Deployment', 'p4kdu-cu',
                                                    lambda i: i['metadata']['annotations'].clear()),
            'ReplicaSet owned by another Deployment uid': ('ReplicaSet', cu_rs, lambda i: i['metadata'][
                'ownerReferences'][0].update(uid='00000000-0000-4000-8000-000000000000')),
            'ReplicaSet without controller owner': ('ReplicaSet', cu_rs,
                                                    lambda i: i['metadata']['ownerReferences'][0].update(controller=False)),
            'ReplicaSet hash not its name': ('ReplicaSet', cu_rs, lambda i: i['metadata']['labels'].update(
                {'pod-template-hash': 'ffffffffff'})),
            'Pod without owner': ('Pod', cu_pod, lambda i: i['metadata'].pop('ownerReferences')),
            'Pod owned by an unknown ReplicaSet uid': ('Pod', cu_pod, lambda i: i['metadata']['ownerReferences'][0].update(
                uid='00000000-0000-4000-8000-000000000000')),
            'Pod with a second owner': ('Pod', cu_pod, lambda i: i['metadata']['ownerReferences'].append(
                {'kind': 'Job', 'name': 'x', 'uid': 'y', 'controller': False})),
            'Pod template hash differs': ('Pod', cu_pod, lambda i: i['metadata']['labels'].update(
                {'pod-template-hash': '0000000000'})),
            'Helm Secret of wrong type': ('Secret', 'sh.helm.release.v1.p4kdu-cu.v1', lambda i: i.update(type='Opaque')),
            'Helm Secret for another release': ('Secret', 'sh.helm.release.v1.p4kdu-cu.v1', lambda i: i['metadata'][
                'labels'].update(name='p4kdu-du')),
            'Helm Secret version mismatch': ('Secret', 'sh.helm.release.v1.p4kdu-cu.v1', lambda i: i['metadata'][
                'labels'].update(version='2')),
            'Helm Secret not owned by helm': ('Secret', 'sh.helm.release.v1.p4kdu-cu.v1', lambda i: i['metadata'][
                'labels'].update(owner='someone')),
        }
        for label, (kind, name, change) in cases.items():
            with self.subTest(label):
                self.assertIn((kind, name), self.mutated(kind, name, change))

    def test_pods_of_a_rejected_replicaset_are_rejected(self):
        foreign = self.mutated('ReplicaSet', 'p4kdu-cu-584b65f7bc', lambda i: i['metadata'].pop('ownerReferences'))
        self.assertEqual(foreign, [('ReplicaSet', 'p4kdu-cu-584b65f7bc'), ('Pod', 'p4kdu-cu-584b65f7bc-9fn6v')])

    def test_unrelated_objects_are_rejected_even_with_experiment_names(self):
        cu = self.find(dell_items(), 'Pod', 'p4kdu-cu-584b65f7bc-9fn6v')
        extra = [
            {'kind': 'Secret', 'type': 'Opaque', 'metadata': {'name': 'p4kdu-cu-credentials', 'uid': 'a'}},
            {'kind': 'Secret', 'type': 'helm.sh/release.v1', 'metadata': {
                'name': 'sh.helm.release.v1.ran-fran-oai.v1', 'uid': 'b',
                'labels': {'owner': 'helm', 'name': 'ran-fran-oai', 'version': '1'}}},
            {'kind': 'ConfigMap', 'metadata': {'name': 'p4kdu-cu-extra', 'uid': 'c', 'labels': cu['metadata']['labels']}},
            {'kind': 'ConfigMap', 'metadata': {'name': 'istio-ca-root-cert', 'uid': 'd'}},
            {'kind': 'Deployment', 'metadata': {'name': 'p4kdu-cu-shadow', 'uid': 'e'}},
            {'kind': 'Pod', 'metadata': {'name': 'p4kdu-cu-584b65f7bc-zzzzz', 'uid': 'f', 'labels': cu['metadata']['labels']}},
            {'kind': 'Service', 'metadata': {'name': 'p4kdu-cu', 'uid': 'g'}},
            {'kind': 'StatefulSet', 'metadata': {'name': 'p4kdu-du', 'uid': 'h'}},
            {'kind': 'Job', 'metadata': {'name': 'p4kdu-cu-hook', 'uid': 'i'}},
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'p4kdu-cu-data', 'uid': 'j'}},
        ]
        foreign = self.foreign(dell_items() + extra)
        self.assertEqual(sorted(foreign), sorted((i['kind'], i['metadata']['name']) for i in extra))

    def test_baseline_fails_on_a_foreign_secret(self):
        harness = BaselineValidationTests('test_existing_revision_1_deployment_validates_read_only')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.target.extra_objects['p4-kdu-upgrade'] = [('Secret', 'p4kdu-cu-credentials')]
        summary, failed = harness.validate()
        self.assertEqual(failed, ['namespace holds only experiment objects'])
        detail = next(c['detail'] for c in summary['checks'] if c['check'] == 'namespace holds only experiment objects')
        self.assertEqual([tuple(f[:2]) for f in detail['foreign']], [('Secret', 'p4kdu-cu-credentials')])


class TeardownSafetyTests(Harness):
    """G1-G4: identity, termination safety, namespace contents, other NS and stale state."""
    def setUp(self):
        super().setUp()
        self.first = self.experiment()
        self.first.run(('preflight', 'onboard', 'instantiate'))
        self.ns_id = self.first.ns_id
        self.before = list(self.target.mutations)

    def teardown_failed(self):
        summary = self.experiment().teardown()
        return summary, {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}

    def new_mutations(self):
        return self.target.mutations[len(self.before):]

    def instantiate_op(self):
        return next(o for o in self.target.operations.values()
                    if o['nsInstanceId'] == self.ns_id and o['lcmOperationType'] == 'instantiate')

    def assert_untouched(self, failed, reason):
        self.assertIn('teardown completed', failed)
        self.assertIn(reason, failed['teardown completed'])
        self.assertEqual(self.new_mutations(), [])
        self.assertEqual(self.target.instances[self.ns_id]['nsState'], 'READY')
        self.assertIn(('ns', 'p4kdu_ns'), self.target.packages)
        self.assertIn('p4-kdu-upgrade', self.target.namespaces)

    # G1 ------------------------------------------------------------------------------------------------
    def test_genuine_experiment_ns_identity_is_recorded(self):
        summary, failed = self.teardown_failed()
        self.assertEqual(failed, {})
        identity = json.loads((self.work / 'evidence/teardown/ns-identity.json').read_text())
        self.assertEqual((identity['ns_id'], identity['problems']), (self.ns_id, []))
        self.assertEqual(identity['requested'], [['p4kdu', 'cu', 'p4kdu-cu', 'p4-kdu-upgrade'],
                                                 ['p4kdu', 'du', 'p4kdu-du', 'p4-kdu-upgrade']])

    def test_same_named_ns_that_is_not_the_experiment_is_refused(self):
        ns = lambda: self.target.instances[self.ns_id]
        op = lambda: self.instantiate_op()['operationParams']
        def request(change):
            """Edit the JSON-encoded additionalParamsForVnf as OSM v19 stores it."""
            def edit():
                value = json.loads(op()['additionalParamsForVnf'])
                change(value)
                op()['additionalParamsForVnf'] = json.dumps(value)
            return edit
        other_nsd = '00000000-0000-4000-8000-000000000001'
        cases = {
            'instantiated from another NSD': lambda: op().update(nsdId=other_nsd),
            'requested under another name': lambda: op().update(nsName='ran-fran-oai'),
            'requested into another namespace': request(lambda v: v[0]['additionalParamsForKdu'][0].update(
                {'k8s-namespace': 'c63ff4ec-6bd4-46bc-90a2'})),
            'requested another release name': request(lambda v: v[0]['additionalParamsForKdu'][1].update(
                {'kdu-deployment-name': 'du-0010984531'})),
            'requested an extra KDU': request(lambda v: v[0]['additionalParamsForKdu'].append(
                {'kdu_name': 'core', 'k8s-namespace': 'p4-kdu-upgrade', 'kdu-deployment-name': 'p4kdu-core'})),
            'another member index': request(lambda v: v[0].update({'member-vnf-index': 'core'})),
            'no recorded instantiation': lambda: self.instantiate_op().update(lcmOperationType='scale'),
            'two instantiations': lambda: self.target.operations.update(
                {'dup': dict(self.instantiate_op(), _id='dup')}),
            'NSD reference differs': lambda: ns().update({'nsd-id': other_nsd}),
            'deployed into another namespace': lambda: ns()['_admin']['deployed']['K8s'][0].update(
                namespace='c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c'),
            'extra deployed KDU': lambda: ns()['_admin']['deployed']['K8s'].append(
                {'member-vnf-index': 'p4kdu', 'kdu-name': 'x', 'kdu-instance': 'x', 'namespace': 'osm'}),
            'VNFR of another descriptor': lambda: next(v for v in self.target.vnfrs.values()
                                                       if v['nsr-id-ref'] == self.ns_id).update({'vnfd-id': other_nsd}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                saved = copy.deepcopy((self.target.instances, self.target.operations, self.target.vnfrs))
                change()
                _, failed = self.teardown_failed()
                self.assert_untouched(failed, 'not provably the experiment NS')
                self.target.instances, self.target.operations, self.target.vnfrs = saved

    def test_already_terminated_experiment_ns_is_still_identified(self):
        self.target._terminate(self.ns_id)
        self.before = list(self.target.mutations)
        _, failed = self.teardown_failed()
        self.assertEqual(failed, {})
        self.assertNotIn(self.ns_id, self.target.instances)
        self.assertFalse([m for m in self.new_mutations() if m[1].endswith('/terminate')])

    def test_terminated_ns_still_listing_kdus_is_refused(self):
        self.target._terminate(self.ns_id)
        self.target.instances[self.ns_id]['_admin']['deployed']['K8s'] = [
            {'member-vnf-index': 'p4kdu', 'kdu-name': 'cu', 'kdu-instance': 'p4kdu-cu', 'namespace': 'p4-kdu-upgrade'}]
        self.before = list(self.target.mutations)
        _, failed = self.teardown_failed()
        self.assertIn('terminated NS lists unexpected KDUs', failed['teardown completed'])
        self.assertEqual(self.new_mutations(), [])

    # G2 ------------------------------------------------------------------------------------------------
    def assert_kept_after_termination(self):
        self.assertFalse([m for m in self.new_mutations() if m[0] == 'DELETE'])
        self.assertIn(self.ns_id, self.target.instances)
        self.assertIn(('ns', 'p4kdu_ns'), self.target.packages)
        self.assertIn(('knf', 'p4kdu_knf'), self.target.packages)

    def test_failed_termination_keeps_ns_and_packages(self):
        self.target._terminate = lambda ns_id: self.target._new_op(ns_id, 'terminate', {}, state='FAILED')
        _, failed = self.teardown_failed()
        self.assertEqual(failed['experiment NS terminated']['state'], 'FAILED')
        self.assertIn('not COMPLETED', failed['teardown completed'])
        self.assert_kept_after_termination()

    def test_completed_termination_leaving_a_release_keeps_ns_and_packages(self):
        original = self.target._terminate
        def terminate(ns_id):
            releases = dict(self.target.releases)
            op = original(ns_id)
            self.target.releases[('p4-kdu-upgrade', 'p4kdu-du')] = releases[('p4-kdu-upgrade', 'p4kdu-du')]
            return op
        self.target._terminate = terminate
        _, failed = self.teardown_failed()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['releases'], ['p4kdu-du'])
        self.assert_kept_after_termination()

    def test_completed_termination_with_a_running_operation_keeps_ns_and_packages(self):
        original = self.target._terminate
        def terminate(ns_id):
            op = original(ns_id)
            self.target.operations['late'] = dict(self.target.operations[op], _id='late', lcmOperationType='action',
                                                  operationState='PROCESSING', polls=-10 ** 6)
            return op
        self.target._terminate = terminate
        self.target._poll = lambda op_id: None
        # OSM may show the NS idle while a stale operation record is still PROCESSING.
        self.target._refresh = lambda ns_id: self.target.instances[ns_id].update(currentOperation='IDLE')
        _, failed = self.teardown_failed()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['running'], ['late'])
        self.assert_kept_after_termination()

    def test_busy_ns_is_not_terminated(self):
        original = self.target._refresh
        def refresh(ns_id):
            original(ns_id)
            if ns_id == self.ns_id:
                self.target.instances[ns_id]['currentOperation'] = 'ACTION'
        self.target._refresh = refresh
        self.target.instances[self.ns_id]['currentOperation'] = 'ACTION'
        _, failed = self.teardown_failed()
        self.assert_untouched(failed, 'operation in progress')

    def test_packages_kept_when_the_ns_record_survives_deletion(self):
        original = self.target.request
        def request(method, path, data=None, content_type='application/yaml'):
            if method == 'DELETE' and path.startswith('/nslcm/v1/ns_instances/'):
                self.target.mutations.append((method, path))       # accepted, but the record stays
                return b''
            return original(method, path, data, content_type)
        self.target.request = request
        summary = self.experiment(request=request).teardown()
        failed = {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}
        self.assertIn('still present', failed['teardown completed'])
        self.assertIn(self.ns_id, self.target.instances)
        self.assertFalse([m for m in self.new_mutations()
                          if m[0] == 'DELETE' and ('/ns_descriptors/' in m[1] or '/vnf_packages/' in m[1])])

    def test_packages_kept_while_another_ns_uses_the_nsd(self):
        nsd = self.target.packages[('ns', 'p4kdu_ns')]['_id']
        self.target.instances[self.target.other_ns]['nsd-id'] = nsd
        _, failed = self.teardown_failed()
        self.assertIn('still referenced', failed['teardown completed'])
        self.assertIn(('ns', 'p4kdu_ns'), self.target.packages)
        self.assertFalse([m for m in self.new_mutations()
                          if m[0] == 'DELETE' and ('/ns_descriptors/' in m[1] or '/vnf_packages/' in m[1])])

    # G3 ------------------------------------------------------------------------------------------------
    def test_unexpected_namespaced_objects_stop_teardown_before_anything_is_removed(self):
        for kind, name in (('Role', 'p4kdu-cu'), ('RoleBinding', 'p4kdu-cu'), ('ServiceAccount', 'p4kdu-cu'),
                           ('NetworkPolicy', 'deny-all'), ('Ingress', 'p4kdu-cu'),
                           ('HorizontalPodAutoscaler', 'p4kdu-cu'), ('PodDisruptionBudget', 'p4kdu-du'),
                           ('Endpoints', 'p4kdu-cu'), ('ResourceQuota', 'q'), ('LimitRange', 'l')):
            with self.subTest(kind=kind):
                self.target.extra_objects['p4-kdu-upgrade'] = [(kind, name)]
                _, failed = self.teardown_failed()
                self.assert_untouched(failed, 'non-experiment')
        self.target.extra_objects.clear()

    def test_inventory_requires_rbac_policy_and_scaling_kinds(self):
        for kind in ('serviceaccounts', 'roles.rbac.authorization.k8s.io', 'rolebindings.rbac.authorization.k8s.io',
                     'networkpolicies.networking.k8s.io', 'ingresses.networking.k8s.io',
                     'horizontalpodautoscalers.autoscaling', 'poddisruptionbudgets.policy', 'endpoints',
                     'endpointslices.discovery.k8s.io', 'resourcequotas', 'limitranges', 'replicationcontrollers',
                     'persistentvolumeclaims', 'pods', 'deployments.apps', 'statefulsets.apps', 'jobs.batch'):
            self.assertIn(kind, p4.REQUIRED_KINDS)

    def test_discovery_failure_stops_teardown_before_anything_is_removed(self):
        self.target.discovery_error = True
        _, failed = self.teardown_failed()
        self.assert_untouched(failed, 'discovery incomplete')

    def test_custom_resources_in_the_namespace_stop_teardown(self):
        self.target.extra_objects['p4-kdu-upgrade'] = [('ServiceMonitor', 'p4kdu-cu')]
        _, failed = self.teardown_failed()
        self.assert_untouched(failed, 'non-experiment')

    # G4 ------------------------------------------------------------------------------------------------
    def test_other_ns_state_change_during_teardown_is_reported(self):
        original = self.target._terminate
        def terminate(ns_id):
            self.target.instances[self.target.other_ns]['nsState'] = 'BROKEN'
            return original(ns_id)
        self.target._terminate = terminate
        _, failed = self.teardown_failed()
        detail = failed['every other OSM NS unchanged']
        self.assertEqual([r[2] for r in detail['before']], ['READY'])
        self.assertEqual([r[2] for r in detail['after']], ['BROKEN'])

    def test_other_ns_witness_recorded_even_when_teardown_stops(self):
        self.instantiate_op()['operationParams']['nsdId'] = 'x'
        summary, failed = self.teardown_failed()
        self.assertNotIn('every other OSM NS unchanged', failed)
        before = json.loads((self.work / 'evidence/teardown/other-ns-before.json').read_text())
        after = json.loads((self.work / 'evidence/teardown/other-ns-after.json').read_text())
        self.assertEqual(before, after)
        self.assertEqual([r[0] for r in before], [self.target.other_ns])

    def test_preflight_rejects_stale_runner_state(self):
        self.experiment().teardown()
        self.assertTrue(self.first.state_path.exists())
        before = list(self.target.mutations)
        summary = self.experiment().run(('preflight', 'onboard'))
        failed = {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}
        self.assertEqual(failed['no stale P1 runner state']['files'], ['active-ran.yaml'])
        self.assertEqual(summary['steps'][0]['result'], 'error')
        self.assertEqual(self.target.mutations, before)
        shutil.move(str(self.first.state_path.parent), str(self.work / 'archived-state'))
        summary = self.experiment().run(('preflight',))
        self.assertEqual([c for c in summary['checks'] if c['passed'] is False], [])


# OSM v19 instantiate operation recorded on the Dell for the experiment NS (fields as
# inspected; additionalParamsForVnf is a JSON-encoded string).
DELL_NS_ID = 'c3cf57ff-d85b-48b9-980d-5c8616b9a66d'
DELL_NSD_ID = 'ea61f9e3-aba2-46c1-a1fa-60f3547cd4dd'
DELL_KNF_ID = '5a0b7c1e-3d2f-4e6a-9b8c-7d6e5f4a3b2c'
DELL_VNF_PARAMS = [{'member-vnf-index': 'p4kdu', 'additionalParamsForKdu': [
    {'kdu_name': 'cu', 'k8s-namespace': 'p4-kdu-upgrade', 'kdu-deployment-name': 'p4kdu-cu'},
    {'kdu_name': 'du', 'k8s-namespace': 'p4-kdu-upgrade', 'kdu-deployment-name': 'p4kdu-du'}]}]


def dell_instantiate_op(**params):
    return {'_id': '7e1d2c3b-4a59-4687-9a0b-1c2d3e4f5a6b', 'lcmOperationType': 'instantiate',
            'operationState': 'COMPLETED', 'nsInstanceId': DELL_NS_ID,
            'operationParams': dict({'nsInstanceId': DELL_NS_ID, 'nsdId': DELL_NSD_ID, 'nsName': 'p4-kdu-upgrade-test',
                                     'vimAccountId': 'b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e',
                                     'lcmOperationType': 'instantiate',
                                     'additionalParamsForVnf': json.dumps(DELL_VNF_PARAMS)}, **params)}


class DellRecordLifecycle:
    """Read-only OSM view of the Dell experiment NS as recorded (revision-1 deployment)."""
    def __init__(self, operations):
        self.operations = operations
        self.ns = {'_id': DELL_NS_ID, 'name': 'p4-kdu-upgrade-test', 'nsState': 'READY', 'currentOperation': 'IDLE',
                   'nsd-id': DELL_NSD_ID, '_admin': {'deployed': {'K8s': [
                       {'member-vnf-index': 'p4kdu', 'kdu-name': kdu, 'kdu-instance': 'p4kdu-' + kdu,
                        'namespace': 'p4-kdu-upgrade'} for kdu in ('cu', 'du')]}}}

    def get_ns(self, ns_id):
        assert ns_id == DELL_NS_ID
        return self.ns

    def ns_operations(self, ns_id):
        return list(self.operations)

    def package(self, kind, name):
        return {'id': DELL_NSD_ID if kind == 'ns' else DELL_KNF_ID, 'revision': 1}

    def vnf_instances(self, ns_id):
        return [{'_id': 'f1e2d3c4-b5a6-4978-8695-a4b3c2d1e0f9', 'nsr-id-ref': ns_id, 'vnfd-id': DELL_KNF_ID}]


class DellInstantiateRecordTests(Harness):
    def verify(self, *operations):
        exp = self.experiment()
        exp.osm.lifecycle = DellRecordLifecycle(operations)
        return exp.verified_teardown_ns(DELL_NS_ID, {'id': DELL_NSD_ID, 'revision': 1})

    def refused(self, *operations, reason='not the isolated experiment request'):
        with self.assertRaisesRegex(p4.TargetError, reason):
            self.verify(*operations)

    def test_real_record_is_the_experiment_ns(self):
        self.assertIsInstance(dell_instantiate_op()['operationParams']['additionalParamsForVnf'], str)
        _, identity = self.verify(dell_instantiate_op(), {'_id': 'x', 'lcmOperationType': 'action'})
        self.assertEqual(identity['problems'], [])
        self.assertEqual(identity['requested'], [('p4kdu', 'cu', 'p4kdu-cu', 'p4-kdu-upgrade'),
                                                 ('p4kdu', 'du', 'p4kdu-du', 'p4-kdu-upgrade')])

    def test_native_list_form_is_accepted_with_the_same_checks(self):
        self.verify(dell_instantiate_op(additionalParamsForVnf=copy.deepcopy(DELL_VNF_PARAMS)))

    def test_operation_identity_fields_must_match(self):
        for change in ({'nsInstanceId': '00000000-0000-4000-8000-000000000009'}, {'nsdId': DELL_KNF_ID},
                       {'nsName': 'ran-fran-oai'}):
            with self.subTest(change=change):
                self.refused(dell_instantiate_op(**change))
        for field in ('nsInstanceId', 'nsdId', 'nsName', 'additionalParamsForVnf'):
            with self.subTest(missing=field):
                op = dell_instantiate_op()
                del op['operationParams'][field]
                self.refused(op)
        op = dell_instantiate_op()
        op['operationParams'] = json.dumps(op['operationParams'])
        self.refused(op)

    def test_duplicated_instantiation_is_refused(self):
        self.refused(dell_instantiate_op(), dict(dell_instantiate_op(), _id='second'))
        self.refused({'_id': 'x', 'lcmOperationType': 'action'})

    def test_malformed_or_unexpected_parameters_are_refused(self):
        def edited(change):
            value = copy.deepcopy(DELL_VNF_PARAMS)
            change(value)
            return json.dumps(value)
        kdus = lambda v: v[0]['additionalParamsForKdu']
        cases = {
            'invalid JSON': json.dumps(DELL_VNF_PARAMS)[:-2],
            'JSON object, not a list': json.dumps(DELL_VNF_PARAMS[0]),
            'empty list': '[]',
            'null': 'null',
            'double-encoded': json.dumps(json.dumps(DELL_VNF_PARAMS)),
            'duplicate JSON key': json.dumps(DELL_VNF_PARAMS).replace(
                '"kdu_name": "cu"', '"kdu_name": "du", "kdu_name": "cu"'),
            'duplicate member split': json.dumps([
                {'member-vnf-index': 'p4kdu', 'additionalParamsForKdu': [DELL_VNF_PARAMS[0]['additionalParamsForKdu'][0]]},
                {'member-vnf-index': 'p4kdu', 'additionalParamsForKdu': [DELL_VNF_PARAMS[0]['additionalParamsForKdu'][1]]}]),
            'duplicated KDU entry': edited(lambda v: kdus(v).append(dict(kdus(v)[0]))),
            'KDU entry missing': edited(lambda v: kdus(v).pop()),
            'extra KDU parameter': edited(lambda v: kdus(v)[0].update(additionalParams={'replicas': 3})),
            'extra string KDU parameter': edited(lambda v: kdus(v)[0].update({'kdu-model': 'stable/other'})),
            'extra VNF parameter': edited(lambda v: v[0].update({'k8s-namespace': 'osm'})),
            'KDU field missing': edited(lambda v: kdus(v)[1].pop('k8s-namespace')),
            'non-string namespace': edited(lambda v: kdus(v)[0].update({'k8s-namespace': None})),
            'non-string member index': edited(lambda v: v[0].update({'member-vnf-index': 1})),
            'KDU list not a list': edited(lambda v: v[0].update(additionalParamsForKdu=kdus(v)[0])),
            'second VNF member': edited(lambda v: v.append({'member-vnf-index': 'core', 'additionalParamsForKdu': []})),
            'release renamed': edited(lambda v: kdus(v)[0].update({'kdu-deployment-name': 'cu-0010984531'})),
            'namespace changed': edited(lambda v: kdus(v)[1].update({'k8s-namespace': 'default'})),
        }
        for label, value in cases.items():
            with self.subTest(label):
                self.refused(dell_instantiate_op(additionalParamsForVnf=value))

    def test_decoder_reports_the_reason(self):
        decode = p4.Experiment.requested_kdus
        for value, reason in (('{', 'Expecting'), ('[]', 'non-empty list'),
                              ('[{"member-vnf-index": "a", "member-vnf-index": "b", "additionalParamsForKdu": []}]',
                               'duplicate key'),
                              ('[{"member-vnf-index": "a", "additionalParamsForKdu": [%s]},'
                               ' {"member-vnf-index": "a", "additionalParamsForKdu": [%s]}]' % ((json.dumps(
                                   DELL_VNF_PARAMS[0]['additionalParamsForKdu'][0]),) * 2), 'duplicate member-vnf-index'),
                              ('[{"member-vnf-index": "a", "additionalParamsForKdu": []}]', 'unexpected VNF entry'),
                              ([{'member-vnf-index': 'a', 'additionalParamsForKdu': [{'kdu_name': 'cu'}]}],
                               'unexpected KDU entry'),
                              ([{'member-vnf-index': 'a', 'additionalParamsForKdu': [
                                  {'kdu_name': 'cu', 'k8s-namespace': None, 'kdu-deployment-name': 'p4kdu-cu'}]}],
                               'unexpected KDU entry'),
                              ([{'member-vnf-index': 'a', 'additionalParamsForKdu': [
                                  {'kdu_name': 'cu', 'k8s-namespace': 'n', 'kdu-deployment-name': 'r', 'x': 'y'}]}],
                               'unexpected KDU entry')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, reason):
                decode(value)


class PrimitiveParamsTests(unittest.TestCase):
    """lifecycle.primitive_params: OSM v19 NBI stores non-empty primitive_params JSON-encoded."""
    def params(self, value, **extra):
        return lifecycle.primitive_params({'operationParams': dict({'primitive': 'upgrade'}, primitive_params=value,
                                                                   **extra)})

    def test_accepted_forms(self):
        model = {'kdu_model': '/app/storage/x:2/p4kdu_knf/helm-chart-v3s/cu'}
        self.assertEqual(self.params(json.dumps(model)), model)          # as stored by NBI v19
        self.assertEqual(self.params(model), model)                      # native mapping
        self.assertEqual(self.params({}), {})                            # empty: NBI leaves it as is
        self.assertEqual(lifecycle.primitive_params({'operationParams': {'primitive': 'rollback'}}), {})
        self.assertEqual(lifecycle.primitive_params({}), {})

    def test_malformed_values_are_rejected(self):
        for value in ('', '{', '[]', '"{}"', 'null', '1', json.dumps(json.dumps({'kdu_model': 'x'})),
                      '{"kdu_model": "a", "kdu_model": "b"}', ['kdu_model'], 3, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.params(value)
        with self.assertRaises(ValueError):
            lifecycle.primitive_params({'operationParams': '{"primitive_params": {}}'})

    def test_snapshot_records_unreadable_parameters_instead_of_failing(self):
        harness = TeardownCheckTests('test_instantiated_deployment_passes_read_only')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        op = next(o for o in harness.target.operations.values() if o['nsInstanceId'] == harness.ns_id)
        harness.target.operations['odd'] = dict(op, _id='odd', lcmOperationType='action',
                                                operationParams={'primitive': 'upgrade', 'kdu_name': 'cu',
                                                                 'primitive_params': '{'})
        exp = harness.experiment()
        exp.ns_id = harness.ns_id
        snap = exp.snapshot('s', 'x')
        odd = next(o for o in snap['operations'] if o['id'] == 'odd')
        self.assertTrue(odd['kdu_model'].startswith('unreadable primitive_params'))


class TeardownCheckTests(Harness):
    """teardown-check: read-only target, G1, G3 and G4 inspection of the existing deployment."""
    def setUp(self):
        super().setUp()
        self.first = self.experiment()
        self.first.run(('preflight', 'onboard', 'instantiate'))
        self.ns_id = self.first.ns_id
        self.state = self.first.state_path.read_bytes()
        self.before = list(self.target.mutations)

    def check(self, read_only=True):
        cluster = p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', self.config['kubeconfig'], 'osm',
                             run=self.target.run, read_only=read_only)
        evidence = p4.Evidence(self.work / 'evidence-check', clock=self.clock)
        exp = p4.Experiment(self.config, p4.read_only_request(self.target.request) if read_only else self.target.request,
                            cluster, evidence, clock=self.clock, commit=self.COMMIT,
                            resolver=lambda host: self.resolved[host])
        summary = exp.teardown_check()
        self.assertEqual(self.target.mutations, self.before)
        self.assertEqual(self.first.state_path.read_bytes(), self.state)
        self.assertIsNone(lifecycle.P4_KDU_UPGRADE_APPROVAL)
        return summary, {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}

    def test_instantiated_deployment_passes_read_only(self):
        summary, failed = self.check()
        self.assertEqual(failed, {})
        passed = [c['check'] for c in summary['checks'] if c['passed'] is True]
        for name in ('target is the isolated experiment system', 'NS identity proven from OSM records',
                     'NS nsd-id is the p4kdu_ns package', 'every VNFR vnfd-id is the p4kdu_knf package',
                     'namespace holds only experiment objects', 'no other NS uses the experiment NSD',
                     'NS idle (no operation in progress)', 'committed P4 gate is closed'):
            self.assertIn(name, passed)
        operations = json.loads((self.work / 'evidence-check/teardown-check/ns-operations.json').read_text())
        self.assertEqual([(o['type'], o['state']) for o in operations], [('instantiate', 'COMPLETED')])
        others = json.loads((self.work / 'evidence-check/teardown-check/other-ns.json').read_text())
        self.assertEqual([o[0] for o in others], [self.target.other_ns])
        self.assertTrue(summary['read_only'])

    def test_no_mutating_operation_is_reachable(self):
        forbidden = [mock.patch.object(p4.Osm, name, side_effect=AssertionError(name))
                     for name in ('terminate', 'delete_ns', 'delete_package', 'instantiate')]
        forbidden += [mock.patch.object(p4.Cluster, name, side_effect=AssertionError(name))
                      for name in ('restart_lcm', 'helm_rollback', 'delete_namespace')]
        forbidden += [mock.patch.object(NbiLifecycle, name, side_effect=AssertionError(name))
                      for name in ('kdu_action', 'publish')]
        forbidden += [mock.patch.object(lifecycle, name, side_effect=AssertionError(name))
                      for name in ('process_release', 'reconcile_update', 'adopt_lost_request', 'write_state')]
        mocks = [patch.start() for patch in forbidden]
        for patch in forbidden:
            self.addCleanup(patch.stop)
        _, failed = self.check()
        self.assertEqual(failed, {})
        for patched in mocks:
            patched.assert_not_called()

    def test_read_only_wrappers_refuse_every_mutation(self):
        request = p4.read_only_request(self.target.request)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method), self.assertRaisesRegex(p4.ExperimentError, 'Read-only'):
                request(method, '/nslcm/v1/ns_instances/' + self.ns_id + '/terminate', b'')
        cluster = p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', '/k', 'osm', run=self.target.run, read_only=True)
        k, h = cluster.kubectl, cluster.helm
        for argv in (k('delete', 'namespace', 'p4-kdu-upgrade'), k('rollout', 'restart', 'deployment/lcm', '-n', 'osm'),
                     k('apply', '-f', 'x'), k('scale', 'deployment/p4kdu-cu', '--replicas=0', '-n', 'p4-kdu-upgrade'),
                     k('exec', 'x', '-n', 'p4-kdu-upgrade'), k('patch', 'deployment', 'p4kdu-cu', '-n', 'p4-kdu-upgrade'),
                     h('rollback', 'p4kdu-cu', '-n', 'p4-kdu-upgrade'), h('upgrade', 'p4kdu-cu', 'x', '-n', 'p4-kdu-upgrade'),
                     h('install', 'x', 'y', '-n', 'p4-kdu-upgrade'), h('uninstall', 'p4kdu-cu', '-n', 'p4-kdu-upgrade')):
            with self.subTest(argv=argv[1:3]), self.assertRaises(p4.ClusterError):
                cluster._run(argv)
        self.assertEqual(self.target.mutations, self.before)

    def run_main(self, command='teardown-check'):
        path = self.work / 'p4.json'
        path.write_text(json.dumps(self.config))
        real = p4.Cluster
        with mock.patch.object(p4, 'connect', lambda config: self.target.request), \
                mock.patch.object(p4, 'resolve', lambda host: self.resolved[host]), \
                mock.patch.object(p4, 'Cluster', lambda *a, **k: real(*a, run=self.target.run, **k)), \
                mock.patch('sys.stdout', io.StringIO()):
            return p4.main([command, '--config', str(path)])

    def test_cli_runs_read_only_in_a_separate_evidence_directory(self):
        self.assertEqual(self.run_main(), 0)
        directories = sorted(p.name for p in (self.work / 'evidence').iterdir() if p.name.startswith('teardown-check-'))
        self.assertEqual(len(directories), 1)
        summary = json.loads((self.work / 'evidence' / directories[0] / 'summary.json').read_text())
        self.assertEqual((summary['failed'], summary['read_only']), (0, True))
        self.assertEqual(self.target.mutations, self.before)
        self.assertEqual(self.first.state_path.read_bytes(), self.state)

    def test_cli_enforces_read_only_access(self):
        attempts = []
        def mutate(exp):
            for attempt in (lambda: exp.osm.terminate(exp.osm.experiment_ns()['_id']),
                            lambda: exp.osm.delete_package('ns', 'p4kdu_ns'), exp.cluster.delete_namespace,
                            exp.cluster.restart_lcm, lambda: exp.cluster.helm_rollback('p4kdu-cu')):
                try:
                    attempt()
                    attempts.append('allowed')
                except p4.ExperimentError as error:
                    attempts.append(type(error).__name__)
            return exp.evidence.summary({'read_only': True})
        with mock.patch.object(p4.Experiment, 'teardown_check', mutate):
            self.run_main()
        self.assertEqual(attempts, ['ExperimentError', 'ExperimentError', 'ClusterError', 'ClusterError', 'ClusterError'])
        self.assertEqual(self.target.mutations, self.before)
        self.assertIn('teardown-check', p4.READ_ONLY_COMMANDS)
        self.assertNotIn('teardown', p4.READ_ONLY_COMMANDS)

    def test_identity_and_namespace_problems_are_reported_without_changes(self):
        ns = self.target.instances[self.ns_id]
        cases = {
            'NS nsd-id is the p4kdu_ns package': lambda: ns.update({'nsd-id': '00000000-0000-4000-8000-000000000001'}),
            'every VNFR vnfd-id is the p4kdu_knf package': lambda: next(
                v for v in self.target.vnfrs.values() if v['nsr-id-ref'] == self.ns_id).update(
                {'vnfd-id': '00000000-0000-4000-8000-000000000002'}),
            'namespace holds only experiment objects': lambda: self.target.extra_objects.update(
                {'p4-kdu-upgrade': [('Role', 'p4kdu-cu')]}),
        }
        for check, change in cases.items():
            with self.subTest(check):
                saved = copy.deepcopy((self.target.instances, self.target.vnfrs))
                change()
                _, failed = self.check()
                self.assertIn(check, failed)
                if check != 'namespace holds only experiment objects':
                    self.assertIn('NS identity proven from OSM records', failed)
                self.target.instances, self.target.vnfrs = saved
                ns = self.target.instances[self.ns_id]
                self.target.extra_objects.clear()

    def test_busy_ns_is_reported(self):
        original = self.target._refresh
        def refresh(ns_id):
            original(ns_id)
            if ns_id == self.ns_id:
                self.target.instances[ns_id]['currentOperation'] = 'ACTION'
        self.target._refresh = refresh
        self.target.instances[self.ns_id]['currentOperation'] = 'ACTION'
        _, failed = self.check()
        self.assertEqual(failed, {'NS idle (no operation in progress)': 'ACTION'})

    def test_other_ns_using_the_nsd_is_reported(self):
        self.target.instances[self.target.other_ns]['nsd-id'] = self.target.packages[('ns', 'p4kdu_ns')]['_id']
        _, failed = self.check()
        self.assertEqual(failed['no other NS uses the experiment NSD'], [self.target.other_ns])

    def test_terminated_ns_records_are_observed(self):
        self.target.instances['old'] = {'_id': 'old', 'name': 'earlier-test', 'nsState': 'NOT_INSTANTIATED',
                                        'currentOperation': 'IDLE', 'operational-status': 'terminated',
                                        'nsd-id': 'n', '_admin': {'deployed': {'K8s': [{'kdu-name': 'a'}, {'kdu-name': 'b'}]}}}
        summary, failed = self.check()
        self.assertEqual(failed, {})
        observed = next(c['observed'] for c in summary['checks']
                        if c['check'] == 'terminated NS records and their listed deployed KDUs')
        self.assertEqual(observed, [{'id': 'old', 'name': 'earlier-test', 'operational-status': 'terminated',
                                     'nsd_id_present': True, 'deployed_k8s': 2}])

    def test_hp_target_is_refused_before_reading(self):
        self.resolved['nbi.172.30.0.20.nip.io'] = {'172.30.18.32'}
        summary, failed = self.check()
        self.assertIn('TargetError: OSM endpoint does not resolve only', failed['teardown-check readable'])
        self.assertNotIn('NS identity proven from OSM records', [c['check'] for c in summary['checks']])


class PostTerminationTests(Harness):
    """OSM v19 terminate keeps _admin.deployed.K8s and nsd-id (osm_lcm/ns.py). Teardown deletes
    the NS record only after every post-termination condition holds, and packages only after
    the record is confirmed gone; any failure stops before further removal."""
    def setUp(self):
        super().setUp()
        self.target.terminate_keeps_deployed = True
        self.first = self.experiment()
        self.first.run(('preflight', 'onboard', 'instantiate'))
        self.ns_id = self.first.ns_id
        self.start = len(self.target.mutations)
        self.original_terminate = self.target._terminate

    def teardown(self):
        summary = self.experiment().teardown()
        failed = {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}
        return summary, failed

    def removals(self):
        return [m for m in self.target.mutations[self.start:]
                if m[0] == 'DELETE' or (m[0] == 'kubectl' and m[1].startswith('delete'))]

    def on_terminate(self, change):
        def terminate(ns_id):
            op = self.original_terminate(ns_id)
            change(ns_id, op)
            return op
        self.target._terminate = terminate

    def assert_stopped(self, failed, reason):
        self.assertIn('teardown completed', failed)
        self.assertIn(reason, failed['teardown completed'])
        self.assertEqual(self.removals(), [])
        self.assertIn(self.ns_id, self.target.instances)
        self.assertIn(('ns', 'p4kdu_ns'), self.target.packages)
        self.assertIn(('knf', 'p4kdu_knf'), self.target.packages)

    # positive ------------------------------------------------------------------------------------------
    def test_termination_without_historical_entries(self):
        self.target.terminate_keeps_deployed = False
        summary, failed = self.teardown()
        self.assertEqual(failed, {})
        self.assert_clean_order()

    def test_termination_retaining_exactly_the_cu_du_entries(self):
        summary, failed = self.teardown()
        self.assertEqual(failed, {})
        after = json.loads((self.work / 'evidence/teardown/after-termination.json').read_text())
        self.assertEqual(len(after['deployed']), 2)
        self.assertEqual((after['releases'], after['helm_left'], after['residual'], after['running']), ([], [], [], []))
        self.assertEqual(after['termination']['state'], 'COMPLETED')
        self.assert_clean_order()

    def assert_clean_order(self):
        removals = self.removals()
        paths = [m[1] for m in removals]
        self.assertEqual(len(removals), 4)
        self.assertTrue(paths[0].startswith('/nslcm/v1/ns_instances/' + self.ns_id))     # NS record first
        self.assertIn('/nsd/v1/ns_descriptors/', paths[1])                               # then the packages
        self.assertIn('/vnfpkgm/v1/vnf_packages/', paths[2])
        self.assertEqual(paths[3], 'delete namespace p4-kdu-upgrade --wait=true --timeout=300s')
        self.assertNotIn(self.ns_id, self.target.instances)
        self.assertNotIn('p4-kdu-upgrade', self.target.namespaces)
        operations = json.loads((self.work / 'evidence/teardown/ns-operations.json').read_text())
        self.assertEqual(operations[0]['request']['nsName'], 'p4-kdu-upgrade-test')   # kept before deletion

    def test_already_terminated_ns_is_cleaned_without_a_new_terminate(self):
        self.target._terminate(self.ns_id)
        self.start = len(self.target.mutations)
        _, failed = self.teardown()
        self.assertEqual(failed, {})
        self.assertFalse([m for m in self.target.mutations[self.start:] if m[1].endswith('/terminate')])
        self.assertNotIn(self.ns_id, self.target.instances)

    # negative: termination -----------------------------------------------------------------------------
    def test_failed_termination(self):
        self.target._terminate = lambda ns_id: self.target._new_op(ns_id, 'terminate', {}, state='FAILED')
        _, failed = self.teardown()
        self.assert_stopped(failed, 'not COMPLETED')

    def test_termination_still_processing(self):
        def terminate(ns_id):
            op = self.target._new_op(ns_id, 'terminate', {})
            self.target.operations[op]['stuck'] = True
            return op
        self.target._terminate = terminate
        _, failed = self.teardown()
        self.assertEqual(failed['experiment NS terminated']['state'], 'PROCESSING')
        self.assert_stopped(failed, 'not COMPLETED')

    def test_ns_remains_instantiated(self):
        def terminate(ns_id):
            for kdu in self.target.instances[ns_id]['_admin']['deployed']['K8s']:
                self.target.releases.pop((kdu['namespace'], kdu['kdu-instance']), None)
            return self.target._new_op(ns_id, 'terminate', {}, state='COMPLETED')
        self.target._terminate = terminate
        _, failed = self.teardown()
        self.assert_stopped(failed, 'not in a safe state')

    def test_ns_not_idle(self):
        original = self.target._refresh
        def refresh(ns_id):
            original(ns_id)
            if ns_id == self.ns_id and self.target.instances[ns_id]['nsState'] == 'NOT_INSTANTIATED':
                self.target.instances[ns_id]['currentOperation'] = 'TERMINATING'
        self.target._refresh = refresh
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['currentOperation'],
                         'TERMINATING')
        self.assert_stopped(failed, 'not in a safe state')

    def test_pending_operation_after_termination(self):
        def change(ns_id, op):
            self.target.operations['late'] = dict(self.target.operations[op], _id='late', lcmOperationType='action',
                                                  operationState='PROCESSING', stuck=True)
        self.on_terminate(change)
        self.target._refresh = lambda ns_id: self.target.instances[ns_id].update(currentOperation='IDLE')
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['running'], ['late'])
        self.assert_stopped(failed, 'not in a safe state')

    # negative: Helm and workloads ----------------------------------------------------------------------
    def keep_release(self, release):
        saved = dict(self.target.releases)
        self.on_terminate(lambda ns_id, op: self.target.releases.update(
            {('p4-kdu-upgrade', release): saved[('p4-kdu-upgrade', release)]}))

    def test_cu_release_remains(self):
        self.keep_release('p4kdu-cu')
        _, failed = self.teardown()
        detail = failed['terminated NS idle with nothing deployed or running']
        self.assertEqual((detail['releases'], detail['helm_left']), (['p4kdu-cu'], ['p4kdu-cu']))
        self.assert_stopped(failed, 'not in a safe state')

    def test_du_release_remains(self):
        self.keep_release('p4kdu-du')
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['helm_left'], ['p4kdu-du'])
        self.assert_stopped(failed, 'not in a safe state')

    def test_helm_status_without_list_entry_is_inconsistent(self):
        saved = dict(self.target.releases)
        original = self.target._helm
        def helm(args):
            if args[0] == 'status' and args[1] == 'p4kdu-cu' and not self.target.releases:
                self.target.releases[('p4-kdu-upgrade', 'p4kdu-cu')] = saved[('p4-kdu-upgrade', 'p4kdu-cu')]
                try:
                    return original(args)
                finally:
                    self.target.releases.clear()
            return original(args)
        self.target._helm = helm
        _, failed = self.teardown()
        self.assertTrue(failed['terminated NS idle with nothing deployed or running']['helm_left'][0]
                        .startswith('inconsistent'))
        self.assert_stopped(failed, 'not in a safe state')

    def test_workload_remains_without_a_release(self):
        for kind, name in (('Deployment', 'p4kdu-cu'), ('Pod', 'p4kdu-du-67647b8b96-krkqf'),
                           ('ReplicaSet', 'p4kdu-cu-584b65f7bc'), ('StatefulSet', 'p4kdu-du')):
            with self.subTest(kind=kind):
                self.setUp()
                self.on_terminate(lambda ns_id, op: self.target.extra_objects.update(
                    {'p4-kdu-upgrade': [(kind, name)]}))
                _, failed = self.teardown()
                self.assertEqual(failed['terminated NS idle with nothing deployed or running']['residual'],
                                 [(kind, name)])
                self.assert_stopped(failed, 'not in a safe state')

    def test_unexpected_resources_appear_after_termination(self):
        self.on_terminate(lambda ns_id, op: self.target.extra_objects.update(
            {'p4-kdu-upgrade': [('Role', 'p4kdu-cu'), ('ConfigMap', 'other')]}))
        _, failed = self.teardown()
        self.assertEqual(sorted(failed['terminated NS idle with nothing deployed or running']['residual']),
                         [('ConfigMap', 'other'), ('Role', 'p4kdu-cu')])
        self.assert_stopped(failed, 'not in a safe state')

    # negative: retained KDU entries --------------------------------------------------------------------
    def retained(self, change):
        self.on_terminate(lambda ns_id, op: change(self.target.instances[ns_id]['_admin']['deployed']))

    def test_retained_entry_in_another_namespace(self):
        self.retained(lambda d: d['K8s'][0].update(namespace='c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c'))
        _, failed = self.teardown()
        self.assert_stopped(failed, 'terminated NS lists unexpected KDUs')

    def test_retained_entry_for_another_release_or_kdu(self):
        for change in ({'kdu-instance': 'du-0010984531'}, {'kdu-name': 'core'}, {'member-vnf-index': 'core'}):
            with self.subTest(change=change):
                self.setUp()
                self.retained(lambda d: d['K8s'][1].update(change))
                _, failed = self.teardown()
                self.assert_stopped(failed, 'terminated NS lists unexpected KDUs')

    def test_duplicate_or_malformed_retained_entries(self):
        cases = {'duplicate': lambda d: d['K8s'].append(dict(d['K8s'][0])),
                 'duplicate replacing': lambda d: d['K8s'].__setitem__(1, dict(d['K8s'][0])),
                 'only one': lambda d: d['K8s'].pop(),
                 'entry not a mapping': lambda d: d['K8s'].append('p4kdu-cu'),
                 'list not a list': lambda d: d.update(K8s={'cu': 'p4kdu-cu'})}
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                self.retained(change)
                _, failed = self.teardown()
                self.assert_stopped(failed, 'not provably the experiment NS')

    # negative: other NS and OSM consistency -----------------------------------------------------------
    def test_another_ns_uses_the_experiment_knf(self):
        knf = self.target.packages[('knf', 'p4kdu_knf')]['_id']
        self.target.instances[self.target.other_ns]['vnfd-id'] = [knf]
        _, failed = self.teardown()
        self.assertIn('still referenced', failed['teardown completed'])
        self.assertFalse([m for m in self.removals() if '/ns_descriptors/' in m[1] or '/vnf_packages/' in m[1]])

    def test_unrelated_kubernetes_change_is_reported(self):
        self.on_terminate(lambda ns_id, op: self.target.extra_workloads.append(
            {'kind': 'Deployment', 'metadata': {'namespace': 'osm', 'name': 'new', 'uid': 'u', 'generation': 1}}))
        _, failed = self.teardown()
        self.assertIn('unrelated Kubernetes namespaces and workloads unchanged', failed)

    def test_state_fields_disagree(self):
        self.on_terminate(lambda ns_id, op: self.target.instances[ns_id]['_admin'].update(nsState='INSTANTIATED'))
        _, failed = self.teardown()
        self.assert_stopped(failed, 'NS state and _admin.nsState disagree')

    def test_terminate_record_inconsistent(self):
        cases = {
            'operation names another NS': lambda ns_id, op: self.target.operations[op].update(
                operationParams={'nsInstanceId': self.target.other_ns}),
            'operation without start time': lambda ns_id, op: self.target.operations[op].pop('startTime'),
            'terminate before the instantiation': lambda ns_id, op: self.target.operations[op].update(startTime=0),
            'retained nsd-id of another package': lambda ns_id, op: self.target.instances[ns_id].update(
                {'nsd-id': '00000000-0000-4000-8000-000000000001'}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                self.on_terminate(change)
                _, failed = self.teardown()
                self.assertEqual(self.removals(), [])
                self.assertIn(self.ns_id, self.target.instances)
                self.assertIn('teardown completed', failed)

    def test_already_terminated_ns_with_a_later_failed_terminate(self):
        op = self.target._terminate(self.ns_id)
        self.target.operations['failed-retry'] = dict(self.target.operations[op], _id='failed-retry',
                                                      operationState='FAILED',
                                                      startTime=self.target.operations[op]['startTime'] + 5)
        self.start = len(self.target.mutations)
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['termination']['state'], 'FAILED')
        self.assert_stopped(failed, 'not in a safe state')

    def test_operation_with_unreadable_start_time(self):
        def change(ns_id, op):
            self.target.operations['odd'] = dict(self.target.operations[op], _id='odd', lcmOperationType='action',
                                                 operationState='COMPLETED', startTime=None)
        self.on_terminate(change)
        _, failed = self.teardown()
        self.assertIn('without start times',
                      failed['terminated NS idle with nothing deployed or running']['termination']['problem'])
        self.assert_stopped(failed, 'not in a safe state')

    def test_helm_lists_another_release_in_the_namespace(self):
        original = self.target._helm
        def helm(args):
            value = original(args)
            if args[0] == 'list' and not self.target.releases:
                value = value + [{'name': 'leftover', 'namespace': 'p4-kdu-upgrade', 'revision': '1',
                                  'status': 'uninstalling', 'chart': 'leftover-0.1.0', 'app_version': ''}]
            return value
        self.target._helm = helm
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['releases'], ['leftover'])
        self.assert_stopped(failed, 'not in a safe state')

    def test_tampered_default_object_remains(self):
        original = self.target._kubectl
        def kubectl(args):
            value = original(args)
            if args[0] == 'get' and 'pods' in args[1].split(',') and not self.target.releases:
                for item in value['items']:
                    if item['metadata']['name'] == 'kube-root-ca.crt':
                        item['metadata']['labels'] = {'app': 'someone-else'}
            return value
        self.target._kubectl = kubectl
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['residual'],
                         [('ConfigMap', 'kube-root-ca.crt')])
        self.assert_stopped(failed, 'not in a safe state')

    def test_ns_record_surviving_deletion_under_another_name(self):
        original = self.target.request
        def request(method, path, data=None, content_type='application/yaml'):
            if method == 'DELETE' and path == '/nslcm/v1/ns_instances/' + self.ns_id:
                self.target.mutations.append((method, path))
                self.target.instances[self.ns_id]['name'] = 'renamed'
                return b''
            return original(method, path, data, content_type)
        self.target.request = request
        summary = self.experiment(request=request).teardown()
        failed = {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}
        self.assertIn('still present', failed['teardown completed'])
        self.assertFalse([m for m in self.removals() if '/ns_descriptors/' in m[1] or '/vnf_packages/' in m[1]])

    def test_issued_terminate_must_be_the_latest(self):
        def change(ns_id, op):
            self.target.operations['newer'] = dict(self.target.operations[op], _id='newer',
                                                   startTime=self.target.operations[op]['startTime'] + 1)
        self.on_terminate(change)
        _, failed = self.teardown()
        self.assertEqual(failed['terminated NS idle with nothing deployed or running']['termination']['operation'],
                         'newer')
        self.assert_stopped(failed, 'not in a safe state')

    # negative: deletion --------------------------------------------------------------------------------
    def failing_delete(self, error):
        original = self.target.request
        def request(method, path, data=None, content_type='application/yaml'):
            if method == 'DELETE' and path.startswith('/nslcm/v1/ns_instances/'):
                raise error
            return original(method, path, data, content_type)
        self.target.request = request
        summary = self.experiment(request=request).teardown()
        return {c['check']: c.get('detail') for c in summary['checks'] if c['passed'] is False}

    def test_ns_deletion_fails_or_is_uncertain(self):
        from osm_catalog import OSMHTTPError
        for error in (OSMHTTPError(500, 'HTTP request'), TimeoutError('read timed out'), ConnectionResetError()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                failed = self.failing_delete(error)
                self.assertIn('NS deletion failed or uncertain', failed['teardown completed'])
                self.assertFalse([m for m in self.removals() if '/ns_descriptors/' in m[1] or '/vnf_packages/' in m[1]])
                self.assertIn(('ns', 'p4kdu_ns'), self.target.packages)
                record = json.loads((self.work / 'evidence/teardown/ns-delete-error.json').read_text())
                self.assertTrue(record['record_present'])


class EvidenceIntegrityTests(unittest.TestCase):
    def test_exclusive_directory_is_never_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            p4.Evidence(Path(directory) / 'run-1', exclusive=True).record('s', 'x', 1)
            with self.assertRaisesRegex(p4.ExperimentError, 'already exists'):
                p4.Evidence(Path(directory) / 'run-1', exclusive=True)
            self.assertEqual(json.loads((Path(directory) / 'run-1/s/x.json').read_text()), 1)

    def test_recording_twice_keeps_both(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = p4.Evidence(Path(directory) / 'e')
            first, second, third = (evidence.record('s', 'x', value) for value in (1, 2, 3))
            self.assertEqual([p.name for p in (first, second, third)], ['x.json', 'x.2.json', 'x.3.json'])
            self.assertEqual([json.loads(p.read_text()) for p in (first, second, third)], [1, 2, 3])


class AdoptLostRequestTests(unittest.TestCase):
    """lifecycle.adopt_lost_request against recorded OSM operations (read-only)."""
    def setUp(self):
        import test_lifecycle_coordination as harness
        self.h = harness.LifecycleHarness('setUp')
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.knf = self.h.sim.package_id('knf', 'cran_oai_knf')

    def pending(self, upgraded=(), operations=None):
        state = self.h.state()
        state['osm']['pending_update'] = {
            'scenario': 'cran-oai', 'instance_id': self.h.active_id, 'commit': 'a' * 40, 'knf_revision': 2,
            'stage': 'uncertain', 'current_kdu': 'du' if upgraded else 'cu', 'operations': operations or {},
            'upgraded': list(upgraded), 'requested_at': 1000.0, 'kdu_instances': {'cu': 'cu-1', 'du': 'du-2'},
            'previous_release': {'scenario': 'cran-oai', 'instance_id': self.h.active_id, 'commit': None,
                                 'knf_revision': 1, 'helm': {'cu': 1, 'du': 1}}}
        self.h.write(state)

    def op(self, op_id, kdu, start, state='PROCESSING', entered=None, revision=2):
        return {'_id': op_id, 'nsInstanceId': self.h.active_id, 'lcmOperationType': 'action', 'operationState': state,
                'startTime': start, 'statusEnteredTime': entered if entered is not None else start,
                'operationParams': {'primitive': 'upgrade', 'kdu_name': kdu, 'primitive_params': {
                    'kdu_model': '/app/storage/{}:{}/cran_oai_knf/helm-chart-v3s/{}'.format(self.knf, revision, kdu)}}}

    def adopt(self, operations):
        osm = mock.Mock()
        osm.ns_operations.return_value = operations
        return lifecycle.adopt_lost_request(osm, harness_registry(), self.h.state_path,
                                            __import__('test_lifecycle_coordination').CONTEXT, {'clock_skew': 10})

    def test_request_that_reached_osm_is_adopted(self):
        self.pending(upgraded=['cu'], operations={'cu': 'op-cu'})
        result = self.adopt([self.op('op-cu', 'cu', 1001, 'COMPLETED'), self.op('op-du', 'du', 1005)])
        self.assertEqual(result, {'action': 'adopted', 'kdu': 'du', 'operation': 'op-du'})
        pending = self.h.state()['osm']['pending_update']
        self.assertEqual((pending['stage'], pending['operations']), ('submitted', {'cu': 'op-cu', 'du': 'op-du'}))

    def test_request_that_never_reached_osm(self):
        self.pending()
        stale = [self.op('old', 'cu', 900, 'COMPLETED', entered=950),           # earlier attempt, ended before
                 self.op('recent-ended', 'cu', 995, 'FAILED', entered=998),     # inside the skew window, but over
                 self.op('other-rev', 'cu', 1005, revision=3), self.op('other-kdu', 'du', 1005),
                 self.op('too-early', 'cu', 980)]
        self.assertEqual(self.adopt(stale)['action'], 'confirmed-previous-release')
        self.assertNotIn('pending_update', self.h.state()['osm'])

    def test_lost_after_earlier_kdus_upgraded_is_a_partial_failure(self):
        self.pending(upgraded=['cu'], operations={'cu': 'op-cu'})
        self.assertEqual(self.adopt([self.op('op-cu', 'cu', 1001, 'COMPLETED')])['action'], 'failed-partial')
        self.assertEqual(self.h.state()['osm']['pending_update']['stage'], 'failed-partial')

    def encoded(self, op):
        """The record as OSM v19 NBI stores it: non-empty primitive_params JSON-encoded."""
        op['operationParams']['primitive_params'] = json.dumps(op['operationParams']['primitive_params'])
        return op

    def test_json_encoded_request_is_adopted(self):
        self.pending(upgraded=['cu'], operations={'cu': 'op-cu'})
        result = self.adopt([self.encoded(self.op('op-cu', 'cu', 1001, 'COMPLETED')),
                             self.encoded(self.op('op-du', 'du', 1005))])
        self.assertEqual(result, {'action': 'adopted', 'kdu': 'du', 'operation': 'op-du'})

    def test_json_encoded_other_revision_is_not_adopted(self):
        self.pending()
        self.assertEqual(self.adopt([self.encoded(self.op('other-rev', 'cu', 1005, revision=3))])['action'],
                         'confirmed-previous-release')

    def test_unreadable_matching_request_is_not_guessed(self):
        for value in ('{"kdu_model": ', '["x"]', '{"kdu_model": "a", "kdu_model": "b"}', json.dumps('{}'), 7):
            with self.subTest(value=value):
                self.pending()
                before = self.h.state_path.read_bytes()
                op = self.op('bad', 'cu', 1005)
                op['operationParams']['primitive_params'] = value
                with self.assertRaisesRegex(lifecycle.UpdateUncertain, 'Unreadable upgrade request bad'):
                    self.adopt([op])
                self.assertEqual(self.h.state_path.read_bytes(), before)

    def test_unreadable_unrelated_operations_are_ignored(self):
        self.pending()
        other = self.op('du-bad', 'du', 1005)
        other['operationParams']['primitive_params'] = '{'
        rollback = self.op('rb', 'cu', 1005)
        rollback['operationParams'].update(primitive='rollback', primitive_params='{')
        self.assertEqual(self.adopt([other, rollback])['action'], 'confirmed-previous-release')

    def test_ambiguous_requests_are_not_guessed(self):
        self.pending()
        before = self.h.state_path.read_bytes()
        with self.assertRaises(lifecycle.UpdateUncertain):
            self.adopt([self.op('a', 'cu', 1001), self.op('b', 'cu', 1002)])
        self.assertEqual(self.h.state_path.read_bytes(), before)


def harness_registry():
    return __import__('test_lifecycle_coordination').REGISTRY


class LifecycleExtensionTests(unittest.TestCase):
    def test_instance_name_convention_is_unchanged_by_default(self):
        self.assertEqual(lifecycle.instance_name({'key': 'cran-oai'}), 'ran-cran-oai')
        self.assertEqual(lifecycle.instance_name({'key': 'p4kdu', 'ns_name': 'p4-kdu-upgrade-test'}),
                         'p4-kdu-upgrade-test')

    def test_kdu_action_timeout_is_optional_and_validated(self):
        NS, OP = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
        sent = []
        def request(method, path, data=None, content_type=None):
            sent.append(yaml.safe_load(data))
            return yaml.safe_dump({'id': OP}).encode()
        osm = NbiLifecycle(request)
        approval = {'p4_report_sha256': '4' * 64, 'osm_lcm_digest': 'sha256:' + '2' * 64, 'approved_commit': 'c' * 40}
        with mock.patch.object(lifecycle, 'P4_KDU_UPGRADE_APPROVAL', approval):
            osm.kdu_action(NS, '1', 'cu', 'rollback', {})
            osm.kdu_action(NS, '1', 'cu', 'rollback', {}, 120)
            for bad in (0, -1, '120', 1.5, True):
                with self.subTest(bad=bad), self.assertRaises(Exception):
                    osm.kdu_action(NS, '1', 'cu', 'rollback', {}, bad)
        self.assertNotIn('timeout_ns_action', sent[0])
        self.assertEqual(sent[1]['timeout_ns_action'], 120)
        self.assertEqual(len(sent), 2)

    def test_committed_gate_is_still_closed(self):
        self.assertIn('\nP4_KDU_UPGRADE_APPROVAL = None\n', (ROOT / 'scripts/lifecycle.py').read_text())


if __name__ == '__main__':
    unittest.main()
