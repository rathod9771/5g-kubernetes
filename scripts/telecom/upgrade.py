"""Approval-controlled running-instance update/rollback adapter (P9).

Every guardrail below is real and offline-testable: independent approval,
exact target/deployment-identity binding, a shared lifecycle lock, pending-
operation conflict checks, pre/post image-digest verification against the
live namespace, rollback restricted to the immediately prior deployed
release, durable bounded history and no automatic retry after an uncertain
outcome. Only the final OSM submission is additionally gated behind
`upgrade_rollback_certified`, a per-tenant deployment-target flag that no
shipped configuration ever sets: a human sets it only after a separately
approved disposable OSM v19 update/rollback test (see P9-LIVE-TEST.md)
confirms the exact NBI update operation shape on this installed backend.
Until then this adapter can be fully exercised offline but never submits a
live request, exactly like InitialDeployment's own pre-certification state.
"""
import time
import yaml
from osm_catalog import identifier, parse_response
from assurance.model import PROTECTED_NS
from .pipeline import exclusive, Pipeline, scoped_catalog
from .initial import InitialDeployment
from .contract import identity
from .security import Denied


def _images_match(pods, image, namespace):
    if not pods:
        return False
    expected = image['repository']+'@'+image['digest']
    healthy = True
    for pod in pods:
        containers = pod.get('spec', {}).get('containers', [])
        statuses = pod.get('status', {}).get('containerStatuses', [])
        healthy = (healthy and pod.get('metadata', {}).get('namespace') == namespace
                   and not pod.get('metadata', {}).get('deletionTimestamp')
                   and bool(containers) and len(containers) == len(statuses)
                   and all(c.get('image') == expected for c in containers)
                   and all(s.get('ready') is True and s.get('imageID', '').split('@')[-1] == image['digest'] for s in statuses))
    return healthy


class InstanceUpdate:
    def __init__(self, service, policy):
        self.service, self.journal, self.policy = service, service.journal, policy
        self.initial = InitialDeployment(service, policy)

    def request(self, principal, deployment_id, publication_id, action, event_id):
        principal.require('operator')
        if action not in ('update', 'rollback'):
            raise ValueError('Unsupported instance operation')
        deployment = self.service.owned(principal, 'deployments', deployment_id)
        if deployment['state'] != 'WORKLOAD_READY':
            raise Denied('Only a verified ready instance may be targeted')
        publication = self.service.owned(principal, 'publication-requests', publication_id)
        release = self.service.owned(principal, 'releases', publication['release_id'])
        if release.get('schema_version') == 2:
            # _images_match below assumes a single release['image']; a
            # multi-image release has no such field and live update/rollback
            # execution is explicitly out of scope for schema_version=2.
            raise Denied('Multi-image instance update/rollback is not supported')
        target, catalog = self.initial.target(principal)
        if (publication.get('state') != 'PUBLISHED_CONTENT_VERIFIED' or
                publication.get('target_hash') != identity(catalog) or
                release.get('pipeline_version') != 2 or release.get('state') != 'VERIFIED_PACKAGE' or
                release.get('package', {}).get('namespace_portable') is not True):
            raise Denied('Verified catalog publication required')
        current_release = self.journal.get(principal, 'releases', deployment['release_id'])
        if release['workload_id'] != current_release['workload_id']:
            raise Denied('Release must belong to the same workload as the running instance')
        history = deployment.get('release_history', [])
        if action == 'update':
            if release['id'] == deployment['release_id']:
                raise Denied('Update target must differ from the currently running release')
            if release['history'][0]['at'] < current_release['history'][0]['at']:
                raise Denied('Stale source revision cannot be requested as an update; it predates the running release')
        else:
            if not history or history[-1]['release_id'] != release['id']:
                raise Denied('Rollback is only eligible to the immediately prior deployed release')
        Pipeline(self.service, self.policy).registry_gate(principal, release)
        key = identity({'tenant': principal.tenant, 'deployment': deployment_id, 'publication': publication_id,
                         'action': action, 'event': event_id})
        value = {'id': key, 'owner': principal.subject, 'deployment_id': deployment_id, 'publication_id': publication_id,
                 'action': action, 'from_release_id': deployment['release_id'], 'to_release_id': release['id'],
                 'from_image': current_release['image'], 'to_image': release['image'],
                 'to_catalog_ids': publication['diagnostic']['catalog_ids'],
                 'target_hash': identity([target, catalog]), 'deployment_hash': identity(deployment),
                 'state': 'AWAITING_APPROVAL', 'history': [{'state': 'AWAITING_APPROVAL', 'at': time.time()}]}
        return self.journal.event(principal, event_id, {'instance_update_request': value},
            lambda db: self.journal.put(db, principal, 'instance-update-requests', key, value, immutable=True))

    def approve(self, principal, request_id, event_id):
        principal.require('reviewer')
        item = self.journal.get(principal, 'instance-update-requests', request_id)
        if item['state'] != 'AWAITING_APPROVAL' or item['owner'] == principal.subject:
            raise Denied('Independent update/rollback approval required')
        value = {'id': identity({'request': request_id, 'event': event_id}), 'request_id': request_id,
                 'request_hash': identity(item), 'reviewer': principal.subject, 'expires': time.time()+300}
        return self.journal.event(principal, event_id, {'instance_update_approval': request_id},
            lambda db: self.journal.put(db, principal, 'instance-update-approvals', value['id'], value, immutable=True))

    def cancel(self, principal, request_id, event_id):
        principal.require('operator')
        def write(db):
            item = self.journal.get(principal, 'instance-update-requests', request_id, db)
            if item['owner'] != principal.subject or item['state'] != 'AWAITING_APPROVAL':
                raise Denied('Only an unapproved owned request may be safely cancelled; nothing has been sent to OSM')
            item = dict(item, state='CANCELLED')
            item['history'] = item['history']+[{'state': 'CANCELLED', 'at': time.time()}]
            return self.journal.put(db, principal, 'instance-update-requests', request_id, item)
        return self.journal.event(principal, event_id, {'action': 'cancel-instance-update', 'request': request_id}, write)

    def save(self, principal, item, event):
        history = list(item.get('history', []))
        if len(history) >= 64:
            raise Denied('Instance-update history retention review required')
        history.append({'state': item['state'], 'at': time.time(), 'actor': principal.subject})
        item = dict(item, history=history)
        return self.journal.event(principal, event, {'instance_update': item},
            lambda db: self.journal.put(db, principal, 'instance-update-requests', item['id'], item))

    def submit(self, principal, request_id, approval_id):
        principal.require('operator')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            item = self.service.owned(principal, 'instance-update-requests', request_id)
            approval = self.journal.get(principal, 'instance-update-approvals', approval_id)
            deployment = self.service.owned(principal, 'deployments', item['deployment_id'])
            target, catalog = self.initial.target(principal)
            if (item['state'] != 'AWAITING_APPROVAL' or approval['request_id'] != request_id or
                    approval['request_hash'] != identity(item) or approval['expires'] < time.time() or
                    identity([target, catalog]) != item['target_hash'] or identity(deployment) != item['deployment_hash'] or
                    deployment['state'] != 'WORKLOAD_READY' or target.get('tenant') != principal.tenant or
                    target.get('enabled') is not True or catalog.get('enabled') is not True):
                raise Denied('Fresh exact approval and an unchanged ready instance required')
            # Hard safety gate: real execution stays off until a disposable OSM v19
            # update/rollback test certifies the exact NBI shape on this backend.
            if target.get('upgrade_rollback_certified') is not True:
                raise Denied('NOT_CERTIFIED: a separately approved disposable instance test is required before live execution')
            ns_id = identifier(deployment['instance_id'])
            if ns_id in PROTECTED_NS:
                raise Denied('Protected instance forbidden')
            client = scoped_catalog(self.policy, principal, catalog)
            to_release = self.journal.get(principal, 'releases', item['to_release_id'])
            ids = client.verify(to_release['package']['scenario'], {k: bytes.fromhex(v) for k, v in to_release['package']['artifacts'].items()})
            if ids != item['to_catalog_ids']:
                raise Denied('Catalog identity drift')
            operations = parse_response(client.request('GET', '/nslcm/v1/ns_lcm_op_occs'))
            if (not isinstance(operations, list) or
                    any(not isinstance(op, dict) or op.get('operationState') not in ('COMPLETED', 'FAILED', 'ROLLED_BACK') for op in operations)):
                raise Denied('Lifecycle conflict or unavailable operation state')
            # Refuse to mutate an instance whose observed image no longer matches
            # the recorded running release: never guess at unknown live state.
            if not _images_match(self.initial.snapshot(target), item['from_image'], target['namespace']):
                raise Denied('Current instance image does not match the recorded running release')
            item = dict(item, state='SUBMITTING', approval_id=approval_id, submitted_at=time.time())
            item = self.save(principal, item, 'instance-update-submit-'+request_id)
            payload = {'updateType': 'CHANGE_VNFPKG',
                       'changeVnfPackageData': [{'member-vnf-index': 'nf', 'vnfdId': item['to_catalog_ids']['knf']}]}
            try:
                response = parse_response(client.request('POST', '/nslcm/v1/ns_instances/'+ns_id+'/update', yaml.safe_dump(payload).encode()))
                op = identifier(response['nslcmop_id'])
                item = dict(item, state='SUBMITTED', operation_id=op)
            except Exception as error:
                item = dict(item, state='UNKNOWN_OUTCOME', diagnostic={'class': type(error).__name__,
                            'details': 'SUPPRESSED', 'automatic_retry': False})
            return self.save(principal, item, 'instance-update-outcome-'+request_id)

    def refresh(self, principal, request_id, event_id):
        principal.require('operator')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            item = self.service.owned(principal, 'instance-update-requests', request_id)
            target, catalog = self.initial.target(principal)
            if (item['state'] not in ('SUBMITTED', 'VERIFYING', 'WORKLOAD_READY', 'UNAVAILABLE', 'FAILED') or
                    identity([target, catalog]) != item['target_hash']):
                raise Denied('Known submitted instance-update operation required')
            deployment = self.service.owned(principal, 'deployments', item['deployment_id'])
            ns_id = identifier(deployment['instance_id']); op_id = identifier(item['operation_id'])
            if ns_id in PROTECTED_NS:
                raise Denied('Protected instance forbidden')
            try:
                client = scoped_catalog(self.policy, principal, catalog)
                op = parse_response(client.request('GET', '/nslcm/v1/ns_lcm_op_occs/'+op_id))
                if op.get('nsInstanceId') != ns_id:
                    raise Denied('OSM operation identity mismatch')
                to_release = self.journal.get(principal, 'releases', item['to_release_id'])
                pods = self.initial.snapshot(target)
                healthy = _images_match(pods, to_release['image'], target['namespace'])
                state = 'FAILED' if op.get('operationState') == 'FAILED' else 'VERIFYING'
                if op.get('operationState') == 'COMPLETED' and healthy:
                    state = 'WORKLOAD_READY'
                observation = {'provenance': 'LIVE', 'at': time.time(), 'pod_count': len(pods),
                               'image_digest_verified': healthy, 'application_health': 'UNVERIFIED', 'telecom_protocols': 'NOT_RUN'}
            except Exception as error:
                state = 'UNAVAILABLE'; observation = {'provenance': 'UNAVAILABLE', 'at': time.time(), 'error_class': type(error).__name__}
            item = self.save(principal, dict(item, state=state, observation=observation), event_id)
            if state == 'WORKLOAD_READY':
                self._commit(principal, item)
            return item

    def _commit(self, principal, item):
        def write(db):
            deployment = self.journal.get(principal, 'deployments', item['deployment_id'], db)
            if deployment['release_id'] == item['to_release_id']:
                return deployment  # Already committed; a repeated refresh must not duplicate history.
            history = list(deployment.get('release_history', []))
            if len(history) >= 64:
                raise Denied('Deployment release-history retention review required')
            history.append({'release_id': deployment['release_id'], 'at': time.time(), 'action': item['action'], 'request_id': item['id']})
            instance_history = list(deployment.get('history', []))
            if len(instance_history) >= 64:
                raise Denied('Deployment history retention review required')
            instance_history.append({'state': 'WORKLOAD_READY', 'at': time.time(), 'actor': principal.subject, 'reason': item['action']})
            deployment = dict(deployment, release_id=item['to_release_id'], release_history=history, history=instance_history)
            return self.journal.put(db, principal, 'deployments', deployment['id'], deployment)
        return self.journal.event(principal, 'instance-update-commit-'+item['id'], {'deployment_commit': item['id']}, write)
