"""P4 experiment artifacts: packages, charts, target guards and the full runner offline.

The runner executes against tests/p4_sim.py (OSM v19 NBI/LCM, Helm and kubectl
behaviour); no network, Kubernetes, OSM or process is used.
"""
import copy
import hashlib
import io
import json
import os
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
        self.assertEqual({o['operationParams']['primitive_params']['kdu_model'].split(':')[1].split('/')[0]
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
            if args[1].startswith('deployments,') and not self.target.releases and lingering[0]:
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
        with self.assertRaisesRegex(p4.ExperimentError, 'non-experiment'):
            self.experiment().teardown()
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

    def test_cluster_inventory_queries_workload_kinds_and_drops_data(self):
        cluster = p4.Cluster('/usr/bin/kubectl', '/usr/local/bin/helm', '/k', 'osm', run=lambda argv, **_:
                             subprocess.CompletedProcess(argv, 0, json.dumps({'items': dell_items()}).encode(), b''))
        inventory = cluster.inventory()
        self.assertEqual([(i['kind'], i['name']) for i in inventory], DELL_INVENTORY_NAMES)
        self.assertNotIn('H4sI', json.dumps(inventory))
        kinds = cluster.commands[0][2].split(',')
        for kind in ('pods', 'secrets', 'configmaps', 'services', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs',
                     'persistentvolumeclaims'):
            self.assertIn(kind, kinds)
        self.assertEqual(cluster.commands[0][3:], ['-n', 'p4-kdu-upgrade', '-o', 'json'])

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
