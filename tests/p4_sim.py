"""Offline target for the P4 experiment runner: OSM v19 NBI/LCM, Helm and kubectl.

Never opens a socket or runs a process. Behaviour follows the OSM v19 sources read
during the P4 assessment: packages gain revisions under one descriptor id, KDUs are
installed as revision-pinned storage paths into the requested k8s-namespace under
their kdu-deployment-name, KDU upgrade/rollback are NS actions running
helm --atomic / helm rollback, operations are asynchronous, NS identity is kept.
"""
import hashlib
import io
import json
import re
import subprocess
import tarfile
import uuid

import yaml

from osm_catalog import OSMHTTPError

STORAGE = '/app/storage'
LCM_DIGEST = 'sha256:' + '2a401465ba1eb09a4b2de96c11028de7860b8a66dc763b2f0f245962b6771fbd'
UPDATED = '2026-10-22T09:14:03.513+00:00'


def _uid(*parts):
    return str(uuid.UUID(hashlib.sha256('/'.join(parts).encode()).hexdigest()[:32]))


def kube_root_ca():
    return {'kind': 'ConfigMap', 'metadata': {
        'name': 'kube-root-ca.crt', 'uid': _uid('kube-root-ca.crt'),
        'annotations': {'kubernetes.io/description': 'Contains a CA bundle ...'}}, 'data': {'ca.crt': 'PEM'}}


def helm_release_objects(namespace, release, revisions, template_hash, pod_suffix):
    """Objects of one P4 Helm release as Kubernetes/Helm 3 create them (kubectl get -o json)."""
    chart = {'app.kubernetes.io/name': 'p4kdu', 'app.kubernetes.io/component': release.rpartition('-')[2],
             'app.kubernetes.io/instance': release, 'app.kubernetes.io/part-of': 'p4-kdu-upgrade'}
    helm = {'meta.helm.sh/release-name': release, 'meta.helm.sh/release-namespace': namespace}
    managed = dict(chart, **{'app.kubernetes.io/managed-by': 'Helm'})
    pod_labels = {'app.kubernetes.io/name': 'p4kdu', 'app.kubernetes.io/component': chart['app.kubernetes.io/component'],
                  'app.kubernetes.io/instance': release, 'app.kubernetes.io/part-of': 'p4-kdu-upgrade',
                  'pod-template-hash': template_hash}
    deployment, rs = _uid(namespace, release, 'deployment'), _uid(namespace, release, template_hash)
    replicaset = release + '-' + template_hash
    items = [
        {'kind': 'Deployment', 'metadata': {'name': release, 'uid': deployment, 'labels': dict(managed),
                                            'annotations': dict(helm, **{'deployment.kubernetes.io/revision': '1'})}},
        {'kind': 'ConfigMap', 'metadata': {'name': release + '-release', 'uid': _uid(namespace, release, 'cm'),
                                           'labels': dict(managed), 'annotations': dict(helm)}, 'data': {'release': 'rX'}},
        {'kind': 'ReplicaSet', 'metadata': {
            'name': replicaset, 'uid': rs, 'labels': dict(pod_labels),
            'annotations': {'deployment.kubernetes.io/revision': '1', 'meta.helm.sh/release-name': release,
                            'meta.helm.sh/release-namespace': namespace},
            'ownerReferences': [{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': release, 'uid': deployment,
                                 'controller': True, 'blockOwnerDeletion': True}]}},
        {'kind': 'Pod', 'metadata': {
            'name': replicaset + '-' + pod_suffix, 'uid': _uid(namespace, release, pod_suffix), 'labels': dict(pod_labels),
            'annotations': {'p4kdu/release': 'rX', 'checksum/config': '0' * 64},
            'ownerReferences': [{'apiVersion': 'apps/v1', 'kind': 'ReplicaSet', 'name': replicaset, 'uid': rs,
                                 'controller': True, 'blockOwnerDeletion': True}]}},
    ]
    for revision in revisions:
        items.append({'kind': 'Secret', 'type': 'helm.sh/release.v1', 'metadata': {
            'name': 'sh.helm.release.v1.{}.v{}'.format(release, revision), 'uid': _uid(namespace, release, str(revision)),
            'labels': {'modifiedAt': '1792660443', 'name': release, 'owner': 'helm', 'status': 'deployed',
                       'version': str(revision)}}, 'data': {'release': 'H4sIAAAAAAAA/release-payload'}})
    return items


def dump(value):
    return yaml.safe_dump(value).encode()


def parse_package(data):
    """(kind, descriptor id, {kdu: chart info}) from an uploaded archive."""
    charts, descriptor = {}, None
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            text = archive.extractfile(member).read().decode()
            parts = member.name.split('/')
            if member.name.endswith(('_vnfd.yaml', '_nsd.yaml')):
                descriptor = yaml.safe_load(text)
            elif len(parts) == 4 and parts[1] == 'helm-chart-v3s' and parts[3] in ('Chart.yaml', 'values.yaml'):
                charts.setdefault(parts[2], {}).update(yaml.safe_load(text))
    if 'vnfd' in descriptor:
        info = {kdu: {'chart': c['name'] + '-' + c['version'], 'version': c['version'], 'marker': c['marker'],
                      'broken': bool(c.get('broken')), 'delay': c.get('readyDelaySeconds', 0)}
                for kdu, c in charts.items()}
        return 'knf', descriptor['vnfd']['id'], info
    return 'ns', descriptor['nsd']['nsd'][0]['id'], {}


class P4Target:
    def __init__(self, clock, processing_polls=1, lcm_restart_mode='resumes', other_ns=True):
        self.clock, self.processing_polls, self.lcm_restart_mode = clock, processing_polls, lcm_restart_mode
        self.project = {'_id': str(uuid.uuid4()), 'name': 'admin'}
        self.vim = {'_id': str(uuid.uuid4()), 'name': 'dell-dummy-vim', 'vim_type': 'dummy'}
        self.cluster = {'_id': str(uuid.uuid4()), 'name': 'dell-cluster', 'vim_account': self.vim['_id'],
                        'deployment_methods': {'helm-chart-v3': True}, '_admin': {'operationalState': 'ENABLED'}}
        self.packages = {}          # (kind, id) -> {'_id', 'revisions': {rev: bytes}, 'charts': {rev: info}}
        self.instances, self.vnfrs, self.operations = {}, {}, {}
        self.releases = {}          # (namespace, name) -> [revision dicts]
        self.namespaces = {'kube-system': str(uuid.uuid4()), 'osm': str(uuid.uuid4())}
        self.extra_objects = {}     # namespace -> [(kind, name)] not created by the experiment
        self.mutations = []
        self.lcm_generation = 1
        self.atomic = True          # set False to model a broken helm --atomic
        self.rollback_works = True
        if other_ns:
            # An unrelated NS (e.g. left by the old installer) that the experiment must never touch.
            self.other_ns = str(uuid.uuid4())
            self.instances[self.other_ns] = {'_id': self.other_ns, 'name': 'core-persistent', 'nsState': 'READY',
                                             'currentOperation': 'IDLE', '_admin': {'deployed': {'K8s': []}}}

    # -- NBI ------------------------------------------------------------------------------------------
    def request(self, method, path, data=None, content_type='application/yaml'):
        if method != 'GET':
            self.mutations.append((method, path))
        path, _, query = path.partition('?')
        listing = {'/vnfpkgm/v1/vnf_packages': 'knf', '/nsd/v1/ns_descriptors': 'ns'}
        if method == 'GET' and path == '/admin/v1/projects':
            return dump([self.project])
        if method == 'GET' and path == '/admin/v1/vim_accounts':
            return dump([self.vim])
        if method == 'GET' and path == '/admin/v1/k8sclusters':
            return dump([self.cluster])
        if method == 'GET' and path in listing:
            return dump([self._descriptor(name, p) for (kind, name), p in self.packages.items() if kind == listing[path]])
        match = re.fullmatch(r'/(vnfpkgm/v1/vnf_packages|nsd/v1/ns_descriptors)/([^/]+)(?:/(package_content|nsd_content))?', path)
        if match:
            kind = 'knf' if match[1].startswith('vnfpkgm') else 'ns'
            found = [(k, p) for k, p in self.packages.items() if k[0] == kind and p['_id'] == match[2]]
            if not found:
                raise OSMHTTPError(404, 'HTTP request')
            (key, package), = found
            if match[3] and method == 'GET':
                return package['revisions'][max(package['revisions'])]
            if match[3] and method == 'PUT':
                return self._upload(data, expected=key)
            if not match[3] and method == 'DELETE':
                if self._in_use(kind, package['_id']):
                    raise OSMHTTPError(409, 'HTTP request', '; descriptor in use')
                self.packages.pop(key)
                return b''
        if method == 'POST' and path in ('/vnfpkgm/v1/vnf_packages_content', '/nsd/v1/ns_descriptors_content'):
            return self._upload(data)
        if path == '/nslcm/v1/ns_instances' and method == 'GET':
            for ns_id in self.instances:
                self._refresh(ns_id)
            return dump(list(self.instances.values()))
        if path == '/nslcm/v1/vnf_instances' and method == 'GET':
            return dump(list(self.vnfrs.values()))
        if path == '/nslcm/v1/ns_instances_content' and method == 'POST':
            return self._instantiate(yaml.safe_load(data))
        match = re.fullmatch(r'/nslcm/v1/ns_instances/([^/]+)(?:/(terminate|action))?', path)
        if match:
            ns_id, verb = match[1], match[2]
            if ns_id not in self.instances:
                raise OSMHTTPError(404, 'HTTP request')
            if method == 'GET' and not verb:
                self._refresh(ns_id)
                return dump(self.instances[ns_id])
            if method == 'DELETE' and not verb:
                if self.instances[ns_id]['nsState'] != 'NOT_INSTANTIATED':
                    raise OSMHTTPError(409, 'HTTP request', '; instantiated')
                self.instances.pop(ns_id)
                for vnfr in [v for v in self.vnfrs.values() if v['nsr-id-ref'] == ns_id]:
                    self.vnfrs.pop(vnfr['_id'])
                return b''
            if method == 'POST' and verb == 'terminate':
                return dump({'id': self._terminate(ns_id)})
            if method == 'POST' and verb == 'action':
                return self._action(ns_id, yaml.safe_load(data))
        if path == '/nslcm/v1/ns_lcm_op_occs' and method == 'GET':
            wanted = dict(p.split('=', 1) for p in query.split('&') if p)
            for op in list(self.operations):
                self._poll(op)
            return dump([self._op_record(o) for o in self.operations.values()
                         if all(o.get(k) == v for k, v in wanted.items())])
        match = re.fullmatch(r'/nslcm/v1/ns_lcm_op_occs/([^/]+)', path)
        if match and method == 'GET':
            self._poll(match[1])
            return dump(self._op_record(self.operations[match[1]]))
        raise AssertionError('Unexpected NBI request ' + method + ' ' + path)

    def _descriptor(self, name, package):
        record = {'_id': package['_id'], 'id': name, '_admin': {'revision': max(package['revisions']),
                                                                'usageState': 'IN_USE' if self._in_use(None, package['_id'])
                                                                else 'NOT_IN_USE'}}
        record['product-name'] = name
        return record

    def _in_use(self, kind, package_id):
        return any(n.get('nsd-id') == package_id or package_id in n.get('vnfd-ids', [])
                   for n in self.instances.values())

    def _upload(self, data, expected=None):
        kind, name, charts = parse_package(data)
        if expected and expected != (kind, name):
            raise OSMHTTPError(422, 'HTTP request', '; descriptor id changed')
        package = self.packages.setdefault((kind, name), {'_id': str(uuid.uuid4()), 'revisions': {}, 'charts': {}})
        revision = max(package['revisions'], default=0) + 1
        package['revisions'][revision], package['charts'][revision] = data, charts
        return b'' if expected else dump({'id': package['_id']})

    def _new_op(self, ns_id, kind, params, state='PROCESSING'):
        op = str(uuid.uuid4())
        now = self.clock.time()
        self.operations[op] = {'_id': op, 'nsInstanceId': ns_id, 'lcmOperationType': kind, 'operationState': state,
                               'operationParams': params, 'startTime': now, 'statusEnteredTime': now,
                               'polls': 0, 'detailed-status': None}
        return op

    def _op_record(self, op):
        return {k: v for k, v in op.items() if k not in ('polls', 'effect')}

    def _refresh(self, ns_id):
        busy = any(o['nsInstanceId'] == ns_id and o['operationState'] == 'PROCESSING' for o in self.operations.values())
        self.instances[ns_id]['currentOperation'] = 'ACTION' if busy else 'IDLE'

    def _finish(self, op, state, detail):
        op.update(operationState=state, statusEnteredTime=self.clock.time())
        op['detailed-status'] = detail

    def _poll(self, op_id):
        op = self.operations[op_id]
        if op['operationState'] != 'PROCESSING' or op.get('stuck'):
            return
        op['polls'] += 1
        if op['polls'] > self.processing_polls:
            op.pop('effect')()

    def _knf(self, ns_id):
        vnfr = next(v for v in self.vnfrs.values() if v['nsr-id-ref'] == ns_id)
        package = next(p for (k, _), p in self.packages.items() if k == 'knf' and p['_id'] == vnfr['vnfd-id'])
        return vnfr, package

    def _instantiate(self, body):
        nsd = next(((k, p) for k, p in self.packages.items() if k[0] == 'ns' and p['_id'] == body['nsdId']), None)
        if not nsd or body['vimAccountId'] != self.vim['_id']:
            raise OSMHTTPError(422, 'HTTP request')
        knf_key, knf = next((k, p) for k, p in self.packages.items() if k[0] == 'knf')
        revision = max(knf['revisions'])
        ns_id, vnfr_id = str(uuid.uuid4()), str(uuid.uuid4())
        (vnf,) = body['additionalParamsForVnf']
        kdus = []
        for params in vnf['additionalParamsForKdu']:
            kdu, namespace, release = params['kdu_name'], params['k8s-namespace'], params['kdu-deployment-name']
            self.namespaces.setdefault(namespace, str(uuid.uuid4()))
            chart = knf['charts'][revision][kdu]
            self.releases[(namespace, release)] = [dict(chart, revision=1, status='deployed', description='Install complete')]
            kdus.append({'kdu-name': kdu, 'kdu-instance': release, 'namespace': namespace,
                         'member-vnf-index': vnf['member-vnf-index'], 'k8scluster-type': 'helm-chart-v3',
                         'kdu-model': '{}/{}:{}/{}/helm-chart-v3s/{}'.format(STORAGE, knf['_id'], revision, knf_key[1], kdu)})
        self.instances[ns_id] = {'_id': ns_id, 'name': body['nsName'], 'nsd-id': body['nsdId'], 'vnfd-ids': [knf['_id']],
                                 'nsState': 'READY', 'operational-status': 'running', 'currentOperation': 'IDLE',
                                 '_admin': {'deployed': {'K8s': kdus}}}
        self.vnfrs[vnfr_id] = {'_id': vnfr_id, 'nsr-id-ref': ns_id, 'vnfd-id': knf['_id'], 'revision': revision}
        op = self._new_op(ns_id, 'instantiate', {'nsName': body['nsName']}, state='COMPLETED')
        return dump({'id': ns_id, 'nslcmop_id': op})

    def _terminate(self, ns_id):
        record = self.instances[ns_id]
        for kdu in record['_admin']['deployed']['K8s']:
            self.releases.pop((kdu['namespace'], kdu['kdu-instance']), None)
        record['_admin']['deployed']['K8s'] = []
        record.update(nsState='NOT_INSTANTIATED', **{'operational-status': 'terminated'})
        record['vnfd-ids'], record['nsd-id'] = [], None
        return self._new_op(ns_id, 'terminate', {}, state='COMPLETED')

    def _action(self, ns_id, body):
        allowed = {'member_vnf_index', 'kdu_name', 'primitive', 'primitive_params', 'timeout_ns_action'}
        if set(body) - allowed or 'primitive_params' not in body:
            raise OSMHTTPError(422, 'HTTP request', '; schema')
        record = self.instances[ns_id]
        kdu = next(k for k in record['_admin']['deployed']['K8s'] if k['kdu-name'] == body['kdu_name'])
        if body.get('member_vnf_index') != kdu['member-vnf-index']:
            raise OSMHTTPError(422, 'HTTP request', '; member index')
        op_id = self._new_op(ns_id, 'action', body)
        op = self.operations[op_id]
        history = self.releases[(kdu['namespace'], kdu['kdu-instance'])]
        if body['primitive'] == 'upgrade':
            op['effect'] = lambda: self._upgrade(op, ns_id, kdu, history, body['primitive_params']['kdu_model'])
        elif body['primitive'] == 'rollback':
            op['effect'] = lambda: self._rollback(op, history)
        else:
            raise OSMHTTPError(422, 'HTTP request', '; primitive')
        if self.processing_polls == 0:
            op.pop('effect')()
        self._refresh(ns_id)
        return dump({'id': op_id})

    def _upgrade(self, op, ns_id, kdu, history, model):
        _, package = self._knf(ns_id)
        match = re.fullmatch(STORAGE + r'/([0-9a-f-]{36}):(\d+)/([^/]+)/helm-chart-v3s/' + kdu['kdu-name'], model)
        if not match or match[1] != package['_id'] or int(match[2]) not in package['revisions']:
            return self._finish(op, 'FAILED', 'Error: chart not found')
        chart = package['charts'][int(match[2])][kdu['kdu-name']]
        current = history[-1]['revision']
        for entry in history:
            if entry['status'] == 'deployed':
                entry['status'] = 'superseded'
        if chart['broken']:
            history.append(dict(chart, revision=current + 1, status='failed', description='Upgrade "x" failed'))
            if self.atomic:
                previous = next(h for h in reversed(history) if h['status'] == 'superseded')
                history.append(dict(previous, revision=current + 2, status='deployed',
                                    description='Rollback to ' + str(previous['revision'])))
            return self._finish(op, 'FAILED', 'Error: release failed, and has been rolled back due to atomic being set')
        history.append(dict(chart, revision=current + 1, status='deployed', description='Upgrade complete'))
        self._finish(op, 'COMPLETED', current + 1)

    def _rollback(self, op, history):
        current = history[-1]['revision']
        target = next(h for h in history if h['revision'] == current - 1)
        if self.rollback_works:
            for entry in history:
                if entry['status'] == 'deployed':
                    entry['status'] = 'superseded'
            history.append(dict(target, revision=current + 1, status='deployed', description='Rollback to ' + str(current - 1)))
        self._finish(op, 'COMPLETED', current + 1)

    def restart_lcm(self):
        self.lcm_generation += 1
        for op in self.operations.values():
            if op['operationState'] != 'PROCESSING' or 'effect' not in op:
                continue
            if self.lcm_restart_mode == 'resumes':
                continue
            params = op['operationParams']
            ns = self.instances[op['nsInstanceId']]
            kdu = next(k for k in ns['_admin']['deployed']['K8s'] if k['kdu-name'] == params['kdu_name'])
            history = self.releases[(kdu['namespace'], kdu['kdu-instance'])]
            _, package = self._knf(op['nsInstanceId'])
            revision = int(re.search(r':(\d+)/', params['primitive_params']['kdu_model'])[1])
            chart = package['charts'][revision][kdu['kdu-name']]
            history.append(dict(chart, revision=history[-1]['revision'] + 1, status='pending-upgrade',
                                description='Preparing upgrade'))
            op.pop('effect')
            if self.lcm_restart_mode == 'fails':
                self._finish(op, 'FAILED', 'Operation interrupted by LCM restart')
            else:
                op['stuck'] = True

    # -- kubectl / helm executor ---------------------------------------------------------------------------
    def run(self, argv, capture_output=True, env=None, timeout=None, check=False):
        tool, args = argv[0].rsplit('/', 1)[-1], argv[1:]
        try:
            value = self._kubectl(args) if tool == 'kubectl' else self._helm(args)
        except LookupError as missing:
            return subprocess.CompletedProcess(argv, 1, b'', ('Error: ' + str(missing) + ' not found (NotFound)').encode())
        if isinstance(value, (dict, list)):
            value = json.dumps(value).encode()
        return subprocess.CompletedProcess(argv, 0, value, b'')

    @staticmethod
    def _ns(args):
        return args[args.index('-n') + 1] if '-n' in args else None

    def _live(self, namespace, release):
        history = self.releases.get((namespace, release))
        if not history:
            raise LookupError(release)
        return history[-1]

    def _kubectl(self, args):
        verb, namespace = args[0], self._ns(args)
        if args[:2] == ['get', 'nodes']:
            return {'items': [{'metadata': {'name': 'precision-3680'},
                               'status': {'addresses': [{'type': 'InternalIP', 'address': '172.30.0.20'}]}}]}
        if verb == 'version':
            return {'serverVersion': {'gitVersion': 'v1.29.15'}}
        if args[:2] == ['get', 'namespace']:
            if args[2] not in self.namespaces:
                raise LookupError('namespaces ' + args[2])
            return {'metadata': {'name': args[2], 'uid': self.namespaces[args[2]]}}
        if args[:2] == ['get', 'pods'] and namespace == 'osm':
            return {'items': [{'metadata': {'name': 'lcm-{}-x'.format(self.lcm_generation)},
                               'status': {'containerStatuses': [{'imageID': 'docker.io/opensourcemano/lcm@' + LCM_DIGEST}]}}]}
        if args[:2] == ['get', 'configmap']:
            live = self._live(namespace, args[2][:-len('-release')])
            return {'data': {'release': live['marker'], 'chart': live['version']}}
        if args[:2] == ['get', 'deployment']:
            live = self._live(namespace, args[2])
            ready = 1 if live['status'] == 'deployed' and not live['broken'] else 0
            return {'metadata': {'name': args[2], 'generation': 3},
                    'spec': {'replicas': 1}, 'status': {'observedGeneration': 3, 'replicas': 1, 'updatedReplicas': 1,
                                                        'readyReplicas': ready, 'availableReplicas': ready}}
        if verb == 'get' and args[1].startswith('deployments,'):
            items = [kube_root_ca()]
            for (ns, release), history in self.releases.items():
                if ns == namespace:
                    template = hashlib.sha256((release + history[-1]['marker']).encode()).hexdigest()[:10]
                    items += helm_release_objects(namespace, release, [h['revision'] for h in history],
                                                  template, 'p' + template[:4])
            items += [{'kind': kind, 'metadata': {'name': name, 'uid': str(uuid.uuid4())}}
                      for kind, name in self.extra_objects.get(namespace, [])]
            return {'items': items}
        if args[:2] == ['rollout', 'restart']:
            self.mutations.append(('kubectl', ' '.join(args)))
            self.restart_lcm()
            return b''
        if args[:2] == ['rollout', 'status']:
            return b''
        if args[:2] == ['delete', 'namespace']:
            self.mutations.append(('kubectl', ' '.join(args)))
            self.namespaces.pop(args[2])
            return b''
        raise AssertionError('Unexpected kubectl ' + ' '.join(args))

    def _helm(self, args):
        verb, namespace = args[0], self._ns(args)
        if verb == 'version':
            return b'v3.21.0+g000000'
        # Output shapes of helm 3 -o json as observed on the Dell: status carries no chart
        # object; list/history give the chart as '<name>-<version>' plus app_version.
        if verb == 'status':
            live = self._live(namespace, args[1])
            return {'name': args[1], 'version': live['revision'], 'namespace': namespace, 'config': {},
                    'info': {'first_deployed': UPDATED, 'last_deployed': UPDATED, 'deleted': '',
                             'description': live['description'], 'status': live['status']}}
        if verb == 'history':
            history = self.releases.get((namespace, args[1]))
            if not history:
                raise LookupError(args[1])
            return [{'revision': h['revision'], 'updated': UPDATED, 'status': h['status'], 'chart': h['chart'],
                     'app_version': h['marker'], 'description': h['description']} for h in history]
        if verb == 'list':
            return [{'name': release, 'namespace': ns, 'revision': str(h[-1]['revision']),
                     'updated': '2026-10-22 09:14:03.513 +0000 UTC', 'status': h[-1]['status'],
                     'chart': h[-1]['chart'], 'app_version': h[-1]['marker']}
                    for (ns, release), h in self.releases.items() if ns == namespace]
        if verb == 'rollback':
            self.mutations.append(('helm', ' '.join(args)))
            history = self.releases[(namespace, args[1])]
            current = history[-1]
            target = next(h for h in reversed(history[:-1]) if h['status'] in ('deployed', 'superseded'))
            current['status'] = 'failed'
            history.append(dict(target, revision=current['revision'] + 1, status='deployed',
                                description='Rollback to ' + str(target['revision'])))
            return b''
        raise AssertionError('Unexpected helm ' + ' '.join(args))
