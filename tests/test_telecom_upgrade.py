"""Offline instance-update/rollback adapter tests (P9); no live OSM/K8s access."""
import hashlib
import io
import json
import tarfile
import time
import unittest
from pathlib import Path
from unittest import mock
import test_telecom_execution as fixtures
from telecom.contract import identity
from telecom.execution import command
from telecom.initial import InitialDeployment
from telecom.security import Denied, Principal
from telecom.service import SimulatedCatalog
from telecom.upgrade import InstanceUpdate

NS = '11111111-2222-4333-8444-555555555555'
OP = '22222222-2222-4333-8444-555555555555'
OP2 = '22222222-2222-4333-8444-666666666666'
UID = '33333333-2222-4333-8444-555555555555'


def alt_fixture_image():
    """A distinct deterministic OCI fixture (different digest from fixtures.fixture_image)."""
    layer_tar = io.BytesIO()
    with tarfile.open(fileobj=layer_tar, mode='w') as archive:
        content = b'offline fixture layer v2'
        entry = tarfile.TarInfo('research-fixture-v2.txt'); entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))
    layer_bytes = layer_tar.getvalue()
    layer_digest = 'sha256:'+hashlib.sha256(layer_bytes).hexdigest()
    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'rootfs': {'type': 'layers', 'diff_ids': [layer_digest]}}).encode()
    config_digest = 'sha256:'+hashlib.sha256(config).hexdigest()
    manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                'config': {'digest': config_digest, 'size': len(config), 'mediaType': 'application/vnd.oci.image.config.v1+json'},
                'layers': [{'digest': layer_digest, 'size': len(layer_bytes), 'mediaType': 'application/vnd.oci.image.layer.v1.tar'}]}
    raw = json.dumps(manifest).encode(); digest = 'sha256:'+hashlib.sha256(raw).hexdigest()
    files = {'blobs/sha256/'+layer_digest[7:]: layer_bytes, 'blobs/sha256/'+config_digest[7:]: config,
             'blobs/sha256/'+digest[7:]: raw,
             'index.json': json.dumps({'schemaVersion': 2, 'manifests': [{'digest': digest, 'size': len(raw), 'mediaType': manifest['mediaType']}]}).encode(),
             'oci-layout': b'{"imageLayoutVersion":"1.0.0"}'}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    return out.getvalue(), digest, files, raw


class InstanceUpdateTests(unittest.TestCase):
    tearDown = fixtures.ExecutionTests.tearDown
    registry = fixtures.ExecutionTests.registry
    complete = fixtures.PipelineTests.complete

    def publish_release(self, release_id, prefix):
        item = self.pipeline.request(self.principal, release_id, 'catalog', prefix+'-request')
        approval = self.pipeline.approve(self.reviewer, item['id'], prefix+'-approve')
        release = self.service.journal.get(self.principal, 'releases', release_id)
        scenario = release['package']['scenario']
        outer = self
        class Client(SimulatedCatalog):
            def request(self, method, path, data=None, content_type='application/yaml'):
                if method == 'POST':
                    self.pending = ('knf', scenario['knf_package']) if 'vnfpkgm' in path else ('ns', scenario['nsd_package'])
                return super().request(method, path, data, content_type)
        publication = self.pipeline.publish(self.principal, item['id'], approval['id'], Client)
        self.assertEqual(publication['state'], 'PUBLISHED_CONTENT_VERIFIED', publication)
        self.ids_by_key[scenario['key']] = publication['diagnostic']['catalog_ids']
        return publication

    def build_second_release(self):
        image_data, image_digest, _, _ = alt_fixture_image()
        worker = {'tenant': 'alice', 'isolation': 'DEDICATED_OFF_HOST_QUOTA_ENFORCED', 'quota_evidence': 'offline-fixture',
                  'address': 'tcp://builder.example:1234', 'max_seconds': 30, 'lease_seconds': 120,
                  'cpu_millicores': 1000, 'memory_mib': 1024, 'pids': 128, 'disk_mib': 2048}
        for field in ('ca', 'cert', 'key'):
            path = self.root/('worker-'+field); path.write_text('fixture'); path.chmod(0o600); worker[field] = str(path)
        self.policy.value['build_worker'] = worker
        job = self.pipeline.queue(self.principal, self.workload['id'], 'chart', 'docker-update-job', image_mode='dockerfile')
        def fixture_buildctl(argv, **kw):
            if argv[0] != 'buildctl':
                return command(argv, **kw)
            destination = Path(next(v.split('dest=', 1)[1] for v in argv if v.startswith('type=oci,dest=')))
            destination.write_bytes(image_data); return b''
        with mock.patch('telecom.execution.command', side_effect=fixture_buildctl), \
             mock.patch('telecom.execution.verify_worker', return_value={'pinned_address': 'tcp://192.0.2.20:1234', 'server_name': 'builder.example'}), \
             mock.patch('telecom.registry.Publisher.configuration', return_value=({}, {})), \
             mock.patch('telecom.registry.Publisher.publish', return_value={'id': 'offline-receipt', 'repository': 'registry.example/nf', 'digest': image_digest}):
            result = self.pipeline.run(self.principal, job['id'])
        self.assertEqual(result['state'], 'COMPLETED', result)
        return result['release_id'], image_digest

    def setUp(self):
        fixtures.PipelineTests.setUp(self)
        # Registry-publication gating for a second (dockerfile-built) release is
        # exhaustively covered by test_telecom_registry.py; this module exercises
        # the instance-update/rollback adapter itself, not that separate gate.
        self.registry_gate_patch = mock.patch('telecom.pipeline.Pipeline.registry_gate', return_value=None)
        self.registry_gate_patch.start(); self.addCleanup(self.registry_gate_patch.stop)
        self.ids_by_key = {}
        self.policy.value['catalog_targets'] = {'alice': {'scope': 'DISPOSABLE_RESEARCH_PROJECT', 'enabled': True,
            'project': 'research-alice', 'host': 'https://osm.example', 'secret_ref': 'osm'}}
        with mock.patch('telecom.pipeline.time.time', return_value=1_000_000_000.0):
            job = self.complete()
        self.original_release_id = job['release_id']
        self.original_publication = self.publish_release(self.original_release_id, 'original')

        self.target = {'scope': 'DISPOSABLE_RESEARCH_NAMESPACE', 'namespace': 'research-alice-demo',
                        'namespace_uid': UID, 'cluster_uid': NS, 'vim_id': OP, 'tenant': 'alice', 'enabled': True,
                        'kubeconfig': '/not-a-real-kubeconfig'}
        self.policy.value['deployment_targets'] = {'alice': self.target}
        self.adapter = InitialDeployment(self.service, self.policy)
        request = self.adapter.request(self.principal, self.original_publication['id'], 'initial-request')
        approval = self.adapter.approve(self.reviewer, request['id'], 'initial-approval')

        self.pending_operations = [{'operationState': 'COMPLETED'}]
        self.op2_state = 'COMPLETED'
        self.update_post_error = None
        self.calls = []
        self.pods = []
        original_release = self.service.journal.get(self.principal, 'releases', self.original_release_id)

        def nbi(method, path, data=None, *args):
            self.calls.append((method, path, data))
            if method == 'GET' and path.endswith('/ns_lcm_op_occs'):
                return json.dumps(self.pending_operations).encode()
            if method == 'POST' and path.endswith('/ns_instances_content'):
                return json.dumps({'id': NS, 'nslcmop_id': OP}).encode()
            if method == 'POST' and path.endswith('/update'):
                if self.update_post_error is not None:
                    raise self.update_post_error
                return json.dumps({'nslcmop_id': OP2}).encode()
            if method == 'GET' and path.endswith('/ns_instances/'+NS):
                return json.dumps({'_id': NS, 'name': request['ns_name'],
                    'nsd-id': self.original_publication['diagnostic']['catalog_ids']['ns'],
                    'nsState': 'READY', 'operational-status': 'running', 'config-status': 'configured'}).encode()
            if method == 'GET' and path.endswith('/ns_lcm_op_occs/'+OP):
                return json.dumps({'nsInstanceId': NS, 'lcmOperationType': 'INSTANTIATE', 'operationState': 'COMPLETED'}).encode()
            if method == 'GET' and path.endswith('/ns_lcm_op_occs/'+OP2):
                return json.dumps({'nsInstanceId': NS, 'operationState': self.op2_state}).encode()
            raise AssertionError('Unexpected NBI call: '+method+' '+path)

        self.client = mock.Mock()
        self.client.verify.side_effect = lambda scenario, artifacts: self.ids_by_key[scenario['key']]
        self.client.request.side_effect = nbi
        self.initial_patch = mock.patch('telecom.initial.scoped_catalog', return_value=self.client)
        self.upgrade_patch = mock.patch('telecom.upgrade.scoped_catalog', return_value=self.client)
        self.initial_patch.start(); self.addCleanup(self.initial_patch.stop)
        self.upgrade_patch.start(); self.addCleanup(self.upgrade_patch.stop)

        def snapshot(target, *, empty=False):
            return [] if empty else self.pods
        self.snapshot_patch = mock.patch.object(InitialDeployment, 'snapshot', side_effect=snapshot)
        self.snapshot_patch.start(); self.addCleanup(self.snapshot_patch.stop)

        submitted = self.adapter.submit(self.principal, request['id'], approval['id'])
        self.assertEqual(submitted['state'], 'SUBMITTED', submitted)
        self.pods = [{'metadata': {'namespace': self.target['namespace']},
                      'spec': {'containers': [{'image': original_release['image']['repository']+'@'+original_release['image']['digest']}]},
                      'status': {'containerStatuses': [{'ready': True, 'imageID': 'docker-pullable://x@'+original_release['image']['digest']}]}}]
        observed = self.adapter.refresh(self.principal, request['id'], 'observe-initial')
        self.assertEqual(observed['state'], 'WORKLOAD_READY', observed)
        self.deployment_id = request['id']
        self.target['upgrade_rollback_certified'] = True
        self.update = InstanceUpdate(self.service, self.policy)

    def deployment(self):
        return self.service.journal.get(self.principal, 'deployments', self.deployment_id)

    def request_update(self, publication, action='update', event='req'):
        return self.update.request(self.principal, self.deployment_id, publication['id'], action, event)

    def approve_and_submit(self, item, pods_after):
        approval = self.update.approve(self.reviewer, item['id'], item['id']+'-approve')
        result = self.update.submit(self.principal, item['id'], approval['id'])
        if pods_after is not None:
            self.pods = pods_after
        return result, approval

    def test_full_update_success(self):
        publication, new_digest = self.setup_second_release_once()
        item = self.request_update(publication)
        new_pods = [{'metadata': {'namespace': self.target['namespace']},
                     'spec': {'containers': [{'image': 'registry.example/nf@'+new_digest}]},
                     'status': {'containerStatuses': [{'ready': True, 'imageID': 'x@'+new_digest}]}}]
        result, _ = self.approve_and_submit(item, new_pods)
        self.assertEqual(result['state'], 'SUBMITTED', result)
        observed = self.update.refresh(self.principal, item['id'], 'observe-update')
        self.assertEqual(observed['state'], 'WORKLOAD_READY', observed)
        deployment = self.deployment()
        self.assertEqual(deployment['release_id'], self._second_release_id)
        self.assertEqual(deployment['release_history'][-1]['release_id'], self.original_release_id)
        self.assertTrue(self.service.journal.verify_audit())

    def setup_second_release_once(self):
        if not hasattr(self, '_second_release_id'):
            self._second_release_id, self._second_digest = self.build_second_release()
            self._second_publication = self.publish_release(self._second_release_id, 'second')
        return self._second_publication, self._second_digest

    def test_full_rollback_success(self):
        publication, new_digest = self.setup_second_release_once()
        item = self.request_update(publication)
        new_pods = [{'metadata': {'namespace': self.target['namespace']},
                     'spec': {'containers': [{'image': 'registry.example/nf@'+new_digest}]},
                     'status': {'containerStatuses': [{'ready': True, 'imageID': 'x@'+new_digest}]}}]
        result, _ = self.approve_and_submit(item, new_pods)
        self.update.refresh(self.principal, item['id'], 'observe-update')
        self.assertEqual(self.deployment()['release_id'], self._second_release_id)
        rollback = self.request_update(self.original_publication, action='rollback', event='req-rollback')
        original_release = self.service.journal.get(self.principal, 'releases', self.original_release_id)
        old_pods = [{'metadata': {'namespace': self.target['namespace']},
                     'spec': {'containers': [{'image': original_release['image']['repository']+'@'+original_release['image']['digest']}]},
                     'status': {'containerStatuses': [{'ready': True, 'imageID': 'x@'+original_release['image']['digest']}]}}]
        self.approve_and_submit(rollback, old_pods)
        observed = self.update.refresh(self.principal, rollback['id'], 'observe-rollback')
        self.assertEqual(observed['state'], 'WORKLOAD_READY', observed)
        deployment = self.deployment()
        self.assertEqual(deployment['release_id'], self.original_release_id)
        self.assertEqual(len(deployment['release_history']), 2)

    def test_rollback_ineligible_without_prior_update(self):
        with self.assertRaises(Denied):
            self.request_update(self.original_publication, action='rollback', event='premature-rollback')

    def test_rollback_ineligible_to_non_immediate_release(self):
        publication, _ = self.setup_second_release_once()
        self.request_update(publication)  # update requested but not yet applied/committed
        with self.assertRaises(Denied):
            self.request_update(publication, action='rollback', event='wrong-rollback-target')

    def test_stale_source_revision_rejected_as_update(self):
        publication, new_digest = self.setup_second_release_once()
        item = self.request_update(publication)
        new_pods = [{'metadata': {'namespace': self.target['namespace']},
                     'spec': {'containers': [{'image': 'registry.example/nf@'+new_digest}]},
                     'status': {'containerStatuses': [{'ready': True, 'imageID': 'x@'+new_digest}]}}]
        self.approve_and_submit(item, new_pods)
        self.update.refresh(self.principal, item['id'], 'observe-update')
        with self.assertRaises(Denied):
            self.request_update(self.original_publication, action='update', event='stale-update')

    def test_wrong_image_digest_keeps_verifying(self):
        publication, new_digest = self.setup_second_release_once()
        item = self.request_update(publication)
        result, _ = self.approve_and_submit(item, None)  # pods left showing the OLD digest
        self.assertEqual(result['state'], 'SUBMITTED', result)
        observed = self.update.refresh(self.principal, item['id'], 'observe-stuck')
        self.assertEqual(observed['state'], 'VERIFYING')
        self.assertFalse(self.deployment()['release_id'] == self._second_release_id)

    def test_duplicate_update_rejected(self):
        publication, new_digest = self.setup_second_release_once()
        item = self.request_update(publication)
        new_pods = [{'metadata': {'namespace': self.target['namespace']},
                     'spec': {'containers': [{'image': 'registry.example/nf@'+new_digest}]},
                     'status': {'containerStatuses': [{'ready': True, 'imageID': 'x@'+new_digest}]}}]
        approval = self.update.approve(self.reviewer, item['id'], 'dup-approve')
        self.update.submit(self.principal, item['id'], approval['id'])
        self.pods = new_pods
        with self.assertRaises(Denied):
            self.update.submit(self.principal, item['id'], approval['id'])

    def test_osm_lifecycle_conflict_blocks_submit(self):
        publication, _ = self.setup_second_release_once()
        item = self.request_update(publication)
        approval = self.update.approve(self.reviewer, item['id'], 'conflict-approve')
        self.pending_operations = [{'operationState': 'PROCESSING'}]
        with self.assertRaises(Denied):
            self.update.submit(self.principal, item['id'], approval['id'])

    def test_upgrade_failure_recorded(self):
        publication, new_digest = self.setup_second_release_once()
        item = self.request_update(publication)
        self.approve_and_submit(item, [])
        self.op2_state = 'FAILED'
        observed = self.update.refresh(self.principal, item['id'], 'observe-failed')
        self.assertEqual(observed['state'], 'FAILED')
        self.assertNotEqual(self.deployment()['release_id'], self._second_release_id)

    def test_unknown_operation_outcome_no_retry(self):
        publication, _ = self.setup_second_release_once()
        item = self.request_update(publication)
        approval = self.update.approve(self.reviewer, item['id'], 'unknown-approve')
        self.update_post_error = RuntimeError('private-detail')
        result = self.update.submit(self.principal, item['id'], approval['id'])
        self.assertEqual(result['state'], 'UNKNOWN_OUTCOME')
        self.assertNotIn('private-detail', json.dumps(result))
        with self.assertRaises(Denied):
            self.update.submit(self.principal, item['id'], approval['id'])

    def test_unavailable_monitoring_on_refresh_error(self):
        publication, _ = self.setup_second_release_once()
        item = self.request_update(publication)
        self.approve_and_submit(item, [])
        original_side_effect = self.client.request.side_effect
        def failing(method, path, data=None, *args):
            if method == 'GET' and path.endswith('/ns_lcm_op_occs/'+OP2):
                raise RuntimeError('private-monitoring-detail')
            return original_side_effect(method, path, data, *args)
        self.client.request.side_effect = failing
        observed = self.update.refresh(self.principal, item['id'], 'observe-unavailable')
        self.assertEqual(observed['state'], 'UNAVAILABLE')
        self.assertNotIn('private-monitoring-detail', json.dumps(observed))

    def test_safe_cancellation_before_approval(self):
        baseline = list(self.calls)
        publication, _ = self.setup_second_release_once()
        item = self.request_update(publication)
        cancelled = self.update.cancel(self.principal, item['id'], 'cancel-event')
        self.assertEqual(cancelled['state'], 'CANCELLED')
        with self.assertRaises(Denied):
            self.update.approve(self.reviewer, item['id'], 'approve-after-cancel')
        self.assertEqual(self.calls, baseline)  # nothing new was ever sent to OSM

    def test_missing_or_mismatched_approval(self):
        publication, _ = self.setup_second_release_once()
        item = self.request_update(publication)
        with self.assertRaises(Denied):
            self.update.submit(self.principal, item['id'], 'missing-approval')
        unrelated = self.request_update(publication, action='update', event='unrelated-request')
        unrelated_approval = self.update.approve(self.reviewer, unrelated['id'], 'unrelated-approve')
        with self.assertRaises(Denied):
            self.update.submit(self.principal, item['id'], unrelated_approval['id'])  # approval bound to a different request

    def test_cross_tenant_denied(self):
        publication, _ = self.setup_second_release_once()
        stranger = Principal('researcher', 'other', self.principal.roles)
        with self.assertRaises(Denied):
            self.update.request(stranger, self.deployment_id, publication['id'], 'update', 'cross-request')
        item = self.request_update(publication)
        with self.assertRaises(Denied):
            self.update.approve(self.principal, item['id'], 'self-approve')  # owner cannot approve own request

    def test_new_routes_require_auth_and_schema(self):
        from flask import Flask
        from telecom.api import register
        app = Flask('p9-boundary'); register(app); client = app.test_client()
        for route in ('instance-update-requests', 'instance-update-approvals', 'instance-update-cancellations', 'instance-updates', 'instance-update-observations'):
            self.assertEqual(client.post('/api/telecom/'+route, json={}).status_code, 403)
        self.assertEqual(client.get('/api/telecom/instance-update-requests').status_code, 403)

    def test_execution_blocked_without_certification(self):
        publication, _ = self.setup_second_release_once()
        self.target['upgrade_rollback_certified'] = False
        item = self.request_update(publication)
        approval = self.update.approve(self.reviewer, item['id'], 'not-certified-approve')
        baseline = list(self.calls)
        with self.assertRaises(Denied):
            self.update.submit(self.principal, item['id'], approval['id'])
        self.assertEqual(self.calls, baseline)  # the certification gate blocks before any new OSM call


if __name__ == '__main__':
    unittest.main()
