"""Offline OSM v19 NBI simulation for lifecycle tests. Never opens a socket.

Serves the request(method, path, data, content_type) interface of Catalog.request
with the behaviour read from OSM v19 NBI/LCM source: content PUT adds a package
revision under the same descriptor id, KDU models are revision-pinned storage
paths, and KDU upgrade/rollback are NS actions that run helm --atomic.
"""
import re
import threading
import uuid

import yaml

from osm_catalog import CatalogError, OSMHTTPError

STORAGE = '/app/storage'


class FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start
        self.lock = threading.Lock()

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += seconds


class OsmNbiSim:
    def __init__(self, registry, clock):
        self.registry, self.clock = registry, clock
        self.entries = {s['key']: s for s in registry['scenarios'] + registry.get('installer_components', [])}
        self.packages = {}      # (kind, name) -> {'_id', 'revisions': {rev: bytes}}
        self.instances = {}     # ns id -> record
        self.vnfrs = {}
        self.operations = {}    # op id -> {'state', 'ns', ...}
        self.helm = {}          # kdu-instance -> [ (helm revision, package revision) ... ]
        self.mutations = []     # (method, path, source)
        self.outcomes = {}      # (primitive, kdu) -> COMPLETED | FAILED | TIMEOUT | LOST
        self.fail_upload = set()
        self.source = 'test'
        self.lock = threading.RLock()
        self.counter = 0

    # -- helpers used by tests ------------------------------------------------
    def onboard(self, key, tag):
        entry = self.entries[key]
        for kind, name in (('knf', entry['knf_package']), ('ns', entry['nsd_package'])):
            self._store(kind, name, content(name, tag))

    def package_id(self, kind, name):
        return self.packages[(kind, name)]['_id']

    def revision(self, kind, name):
        return max(self.packages[(kind, name)]['revisions'])

    def running_revision(self, ns_id, kdu):
        instance = next(k for k in self.instances[ns_id]['_admin']['deployed']['K8s'] if k['kdu-name'] == kdu)
        return self.helm[instance['kdu-instance']][-1][1]

    def primaries(self):
        names = {'ran-' + s['key'] for s in self.registry['scenarios'] if not s.get('additive')}
        return [i for i in self.instances.values() if i['name'] in names]

    def lcm(self, kind):
        return [m for m in self.mutations if kind in m[1] or m[0] == kind]

    # -- NBI ------------------------------------------------------------------
    def request(self, method, path, data=None, content_type='application/yaml'):
        with self.lock:
            if method != 'GET':
                self.mutations.append((method, path, self.source))
            return self._dispatch(method, path, data)

    def _dispatch(self, method, path, data):
        listing = {'/vnfpkgm/v1/vnf_packages': 'knf', '/nsd/v1/ns_descriptors': 'ns'}
        if method == 'GET' and path in listing:
            return dump([self._descriptor(kind, name, p) for (kind, name), p in self.packages.items()
                         if kind == listing[path]])
        match = re.fullmatch(r'/(vnfpkgm/v1/vnf_packages|nsd/v1/ns_descriptors)/([^/]+)/(package_content|nsd_content)', path)
        if match:
            kind = 'knf' if match[1].startswith('vnfpkgm') else 'ns'
            (key, package), = [(k, p) for k, p in self.packages.items() if k[0] == kind and p['_id'] == match[2]]
            if method == 'GET':
                return package['revisions'][max(package['revisions'])]
            if method == 'PUT':
                return self._upload(kind, key[1], data)
        if method == 'POST' and path in ('/vnfpkgm/v1/vnf_packages_content', '/nsd/v1/ns_descriptors_content'):
            name = data.split(b':')[1].decode()
            return self._upload('knf' if 'vnfpkgm' in path else 'ns', name, data)
        if path == '/nslcm/v1/ns_instances' and method == 'GET':
            return dump(list(self.instances.values()))
        if path == '/nslcm/v1/vnf_instances' and method == 'GET':
            return dump(list(self.vnfrs.values()))
        if path == '/nslcm/v1/ns_instances_content' and method == 'POST':
            body = yaml.safe_load(data)
            ns_id, op = self.instantiate(body['nsdId'], body['nsName'])
            return dump({'id': ns_id, 'nslcmop_id': op})
        match = re.fullmatch(r'/nslcm/v1/ns_instances/([^/]+)(?:/(terminate|action))?', path)
        if match:
            ns_id, verb = match[1], match[2]
            if ns_id not in self.instances:
                raise OSMHTTPError(404, 'HTTP request')
            if method == 'GET' and not verb:
                return dump(self.instances[ns_id])
            if method == 'DELETE' and not verb:
                self.instances.pop(ns_id)
                for vnfr in [v for v in self.vnfrs.values() if v['nsr-id-ref'] == ns_id]:
                    self.vnfrs.pop(vnfr['_id'])
                return b''
            if method == 'POST' and verb == 'terminate':
                return dump({'id': self.terminate(ns_id)})
            if method == 'POST' and verb == 'action':
                return self._action(ns_id, yaml.safe_load(data))
        match = re.fullmatch(r'/nslcm/v1/ns_lcm_op_occs/([^/]+)', path)
        if match and method == 'GET':
            op = self.operations[match[1]]
            return dump({'_id': match[1], 'nsInstanceId': op['ns'], 'operationState': op['state'],
                         'lcmOperationType': op['type']})
        raise AssertionError('Unexpected simulated NBI request ' + method + ' ' + path)

    def _descriptor(self, kind, name, package):
        record = {'_id': package['_id'], 'id': name, '_admin': {'revision': max(package['revisions'])}}
        if kind == 'knf':
            record['product-name'] = name
        return record

    def _store(self, kind, name, data):
        package = self.packages.setdefault((kind, name), {'_id': str(uuid.uuid4()), 'revisions': {}})
        package['revisions'][max(package['revisions'], default=0) + 1] = data

    def _upload(self, kind, name, data):
        if name in self.fail_upload:
            raise OSMHTTPError(422, 'HTTP request', '; synthetic descriptor rejection')
        self._store(kind, name, data)
        return b''

    def _op(self, ns_id, kind, state='COMPLETED'):
        self.counter += 1
        op = str(uuid.UUID(int=self.counter))
        self.operations[op] = {'state': state, 'ns': ns_id, 'type': kind}
        return op

    def instantiate(self, nsd_id, name):
        (nsd_name,) = [n for (k, n), p in self.packages.items() if k == 'ns' and p['_id'] == nsd_id]
        entry = next(e for e in self.entries.values() if e.get('nsd_package') == nsd_name)
        knf = entry['knf_package']
        knf_id, revision = self.package_id('knf', knf), self.revision('knf', knf)
        ns_id, vnfr_id = str(uuid.uuid4()), str(uuid.uuid4())
        kdus = []
        for chart in entry['charts']:
            self.counter += 1
            instance = '{}-{:010d}'.format(chart['kdu'], self.counter)
            self.helm[instance] = [(1, revision)]
            kdus.append({'kdu-name': chart['kdu'], 'member-vnf-index': '1', 'kdu-instance': instance,
                         'k8scluster-type': 'helm-chart-v3',
                         'kdu-model': '{}/{}:{}/{}/helm-chart-v3s/{}'.format(STORAGE, knf_id, revision, knf, chart['kdu'])})
        self.instances[ns_id] = {'_id': ns_id, 'name': name, 'nsd-id': nsd_id, 'nsd-name-ref': nsd_name,
                                 'nsState': 'READY', 'operational-status': 'running', 'currentOperation': 'IDLE',
                                 'constituent-vnfr-ref': [vnfr_id], '_admin': {'deployed': {'K8s': kdus}}}
        self.vnfrs[vnfr_id] = {'_id': vnfr_id, 'nsr-id-ref': ns_id, 'vnfd-id': knf_id, 'vnfd-ref': knf,
                               'revision': revision, 'member-vnf-index-ref': '1'}
        return ns_id, self._op(ns_id, 'instantiate')

    def terminate(self, ns_id):
        record = self.instances[ns_id]
        for kdu in record['_admin']['deployed']['K8s']:
            self.helm.pop(kdu['kdu-instance'], None)
        record['_admin']['deployed']['K8s'] = []
        record.update(nsState='NOT_INSTANTIATED', **{'operational-status': 'terminated'})
        return self._op(ns_id, 'terminate')

    def _action(self, ns_id, body):
        primitive, kdu_name = body['primitive'], body['kdu_name']
        record = self.instances[ns_id]
        kdu = next(k for k in record['_admin']['deployed']['K8s'] if k['kdu-name'] == kdu_name)
        outcome = self.outcomes.get((primitive, kdu_name), 'COMPLETED')
        op = self._op(ns_id, 'action', 'PROCESSING' if outcome == 'TIMEOUT' else
                      'FAILED' if outcome == 'FAILED' else 'COMPLETED')
        self.operations[op].update(primitive=primitive, kdu=kdu_name)
        history = self.helm[kdu['kdu-instance']]
        if primitive == 'upgrade' and outcome in ('COMPLETED', 'LOST', 'TIMEOUT'):
            match = re.fullmatch(STORAGE + r'/([0-9a-f-]{36}):(\d+)/[^/]+/helm-chart-v3s/' + kdu_name,
                                 body['primitive_params']['kdu_model'])
            vnfr = next(v for v in self.vnfrs.values() if v['nsr-id-ref'] == ns_id)
            package = next(p for (k, _), p in self.packages.items() if k == 'knf' and p['_id'] == vnfr['vnfd-id'])
            if not match or match[1] != vnfr['vnfd-id'] or int(match[2]) not in package['revisions']:
                self.operations[op]['state'] = 'FAILED'
            elif outcome != 'TIMEOUT':
                history.append((history[-1][0] + 1, int(match[2])))
            else:
                self.operations[op]['pending'] = (history[-1][0] + 1, int(match[2]))
        if primitive == 'rollback' and outcome == 'COMPLETED' and len(history) > 1:
            history.append((history[-1][0] + 1, history[-2][1]))
        if outcome == 'LOST':
            raise CatalogError('OSM HTTP request transport request failed (private details suppressed)')
        return dump({'id': op})

    def finish_operation(self, op, state='COMPLETED'):
        """Late completion of a TIMEOUT operation, for reconciliation tests."""
        record = self.operations[op]
        record['state'] = state
        if state == 'COMPLETED' and 'pending' in record:
            kdu = next(k for k in self.instances[record['ns']]['_admin']['deployed']['K8s']
                       if k['kdu-name'] == record['kdu'])
            self.helm[kdu['kdu-instance']].append(record.pop('pending'))


def content(name, tag):
    return ('pkg:' + name + ':' + tag).encode()


def dump(value):
    return yaml.safe_dump(value).encode()
