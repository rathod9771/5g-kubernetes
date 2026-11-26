#!/usr/bin/env python3
"""Dell current-cluster P5 OSM observer: read-only, no deployment capability.

--preflight: bounded kubectl GETs after exact host/context/server checks.
--inventory: same preflight, then existing private token + owned loopback NBI
            port-forward, GET-only inventory. Never logs raw OSM responses.
Old P4 identity, UUIDs, helm revisions and OSM endpoint are deliberately NOT used.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import sys
from urllib import request as http_request, error as http_error
import uuid

import yaml
import p5_readiness

SCHEMA = '5g-kubernetes/p5-dell-current-osm-observation'
HOST = 'Precision-3680'
NODE = 'precision-3680'
CONTEXT = 'kubernetes-admin@kubernetes'
SERVER = 'https://172.30.0.79:6443'
KUBE_SYSTEM_UID = '6087b451-69c5-4ea2-b657-372d2d87787c'
OSM_NAMESPACE = 'osm'
NBI_HOST = 'nbi.172.30.0.79.nip.io'
MAX_KUBE = 2 << 20
MAX_NBI = 2 << 20
MAX_ITEMS = 128
SAFE_NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z')
TOKEN_RE = re.compile(r'[A-Za-z0-9._~-]{16,4096}\Z')
FORWARD_LINE = re.compile(r'Forwarding from 127\.0\.0\.1:([0-9]{1,5}) -> 9999\Z')
ALLOWED_NBI = (
    '/admin/v1/projects',
    '/admin/v1/vim_accounts',
    '/admin/v1/k8sclusters',
    '/nslcm/v1/ns_instances',
)

class Refused(ValueError):
    pass


def strict_json(blob):
    def pairs(rows):
        keys = [x[0] for x in rows]
        if len(keys) != len(set(keys)):
            raise Refused('DUPLICATE_JSON_KEY')
        return dict(rows)
    def bad_constant(_):
        raise Refused('INVALID_JSON')
    try:
        return json.loads(blob, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError):
        raise Refused('INVALID_JSON') from None


def command(argv):
    """No shell, fixed argv, bounded output; errors never include raw content."""
    try:
        r = subprocess.run(argv, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('KUBECTL_READ_FAILED') from None
    if r.returncode != 0 or len(r.stdout) > MAX_KUBE:
        raise Refused('KUBECTL_READ_FAILED')
    try:
        return r.stdout.decode('utf-8').strip()
    except UnicodeError:
        raise Refused('KUBECTL_READ_FAILED') from None


def _name(value):
    return isinstance(value, str) and bool(SAFE_NAME.fullmatch(value))


def _uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _resource(data, name=None):
    if not isinstance(data, dict) or not isinstance(data.get('metadata'), dict):
        raise Refused('MALFORMED_KUBERNETES_RESPONSE')
    if name is not None and data['metadata'].get('name') != name:
        raise Refused('KUBERNETES_IDENTITY_MISMATCH')
    return data


def _items(data):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) > 256:
        raise Refused('MALFORMED_KUBERNETES_RESPONSE')
    if any(not isinstance(x, dict) for x in data['items']):
        raise Refused('MALFORMED_KUBERNETES_RESPONSE')
    return data['items']


def collect(run=command, hostname=socket.gethostname):
    """Check non-network local config first, then precisely targeted Kubernetes GETs."""
    if hostname() != HOST:
        raise Refused('WRONG_HOST')
    if any(os.environ.get(x) for x in ('HELM_KUBEAPISERVER', 'HELM_KUBECONTEXT')):
        raise Refused('KUBERNETES_OVERRIDE_PRESENT')
    ctx = run(['kubectl', 'config', 'current-context'])
    if ctx != CONTEXT:
        raise Refused('WRONG_CONTEXT')
    server = run(['kubectl', 'config', 'view', '--minify', '-o',
                  'jsonpath={.clusters[0].cluster.server}'])
    if server != SERVER:
        raise Refused('WRONG_API_SERVER')

    def get(resource, namespace=None, label=None):
        argv = ['kubectl', '--context', CONTEXT, '--request-timeout=15s']
        if namespace:
            argv += ['--namespace', namespace]
        argv += ['get', resource]
        if label:
            argv += ['--selector', label]
        argv += ['-o', 'json']
        return strict_json(run(argv))

    system = _resource(get('namespace/kube-system'), 'kube-system')
    if system['metadata'].get('uid') != KUBE_SYSTEM_UID or system.get('status', {}).get('phase') != 'Active':
        raise Refused('WRONG_KUBERNETES_CLUSTER_UID')
    node = _resource(get('node/' + NODE), NODE)
    conditions = node.get('status', {}).get('conditions', [])
    if not isinstance(conditions, list) or not any(isinstance(c, dict) and c.get('type') == 'Ready' and
                                                   c.get('status') == 'True' for c in conditions):
        raise Refused('DELL_NODE_NOT_READY')
    osm = _resource(get('namespace/' + OSM_NAMESPACE), OSM_NAMESPACE)
    if osm.get('status', {}).get('phase') != 'Active':
        raise Refused('OSM_NAMESPACE_INACTIVE')
    service = _resource(get('service/nbi', OSM_NAMESPACE), 'nbi')
    slices = _items(get('endpointslices', OSM_NAMESPACE, 'kubernetes.io/service-name=nbi'))
    ingresses = _items(get('ingress', OSM_NAMESPACE))
    pods = _items(get('pods', OSM_NAMESPACE))
    return {'service': service, 'slices': slices, 'ingresses': ingresses, 'pods': pods,
            'namespace_uid': osm['metadata'].get('uid')}


def evaluate(snapshot):
    """Never returns raw Kubernetes data or a deployment authorization."""
    if not isinstance(snapshot, dict):
        raise Refused('MALFORMED_KUBERNETES_RESPONSE')
    svc = _resource(snapshot.get('service'), 'nbi')
    spec = svc.get('spec')
    if not isinstance(spec, dict) or spec.get('type') != 'ClusterIP':
        raise Refused('NBI_SERVICE_MISMATCH')
    ports = spec.get('ports', [])
    if not isinstance(ports, list) or not any(isinstance(p, dict) and p.get('port') == 9999 and
                                            p.get('protocol', 'TCP') == 'TCP' for p in ports):
        raise Refused('NBI_SERVICE_PORT_MISMATCH')
    if not _uuid(snapshot.get('namespace_uid')):
        raise Refused('OSM_NAMESPACE_UID_INVALID')
    endpoints = set()
    for row in snapshot.get('slices', []):
        if row.get('metadata', {}).get('labels', {}).get('kubernetes.io/service-name') != 'nbi':
            raise Refused('NBI_ENDPOINTSLICE_MISMATCH')
        es_ports = row.get('ports') or []
        if not any(isinstance(p, dict) and p.get('port') == 9999 and p.get('protocol', 'TCP') == 'TCP'
                   for p in es_ports):
            continue
        for endpoint in row.get('endpoints', []):
            if not isinstance(endpoint, dict):
                raise Refused('NBI_ENDPOINTSLICE_MISMATCH')
            if endpoint.get('conditions', {}).get('ready') is True:
                for address in endpoint.get('addresses', []):
                    if isinstance(address, str) and 0 < len(address) <= 64:
                        endpoints.add(address)
    if not endpoints:
        raise Refused('NBI_NO_READY_ENDPOINT')
    matching_rules = 0
    for ingress in snapshot.get('ingresses', []):
        spec_ing = ingress.get('spec', {})
        if not isinstance(spec_ing, dict):
            raise Refused('NBI_INGRESS_MALFORMED')
        for rule in spec_ing.get('rules', []):
            if not isinstance(rule, dict) or rule.get('host') != NBI_HOST:
                continue
            for path in (rule.get('http') or {}).get('paths', []):
                backend = path.get('backend', {}).get('service', {})
                target_port = backend.get('port', {})
                if backend.get('name') == 'nbi' and (target_port.get('number') == 9999 or
                   (isinstance(target_port.get('name'), str) and any(
                    p.get('name') == target_port['name'] and p.get('port') == 9999 for p in ports))):
                    matching_rules += 1
    if matching_rules != 1:
        raise Refused('NBI_INGRESS_BINDING_MISMATCH')
    pods = snapshot.get('pods', [])
    if not isinstance(pods, list) or not pods:
        raise Refused('OSM_PODS_MISSING')
    ready = 0
    for pod in pods:
        if not isinstance(pod, dict):
            raise Refused('MALFORMED_KUBERNETES_RESPONSE')
        statuses = pod.get('status', {}).get('containerStatuses', [])
        containers = pod.get('spec', {}).get('containers', [])
        if (pod.get('status', {}).get('phase') == 'Running' and containers and
            isinstance(containers, list) and isinstance(statuses, list) and
            len(containers) == len(statuses) and all(isinstance(s, dict) and s.get('ready') is True
                                                     for s in statuses)):
            ready += 1
    if ready != len(pods):
        raise Refused('OSM_PODS_NOT_ALL_READY')
    return {'nbi_ready_endpoints': len(endpoints), 'osm_pods': len(pods), 'osm_ready_pods': ready,
            'kubernetes_identity_verified': True, 'nbi_route_verified': True}


def private_token(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077
                    or st.st_size < 16 or st.st_size > 4096):
                raise Refused('TOKEN_NOT_PRIVATE')
            raw = os.read(fd, 4097).decode('ascii')
        finally:
            os.close(fd)
    except (OSError, UnicodeError):
        raise Refused('TOKEN_NOT_AVAILABLE') from None
    token = raw.strip()
    if not TOKEN_RE.fullmatch(token):
        raise Refused('TOKEN_FORMAT_INVALID')
    return token


@contextmanager
def tunnel():
    """Port-forward is temporary loopback transport; no resource is modified."""
    argv = ['kubectl', '--context', CONTEXT, '--namespace', OSM_NAMESPACE,
            'port-forward', '--address', '127.0.0.1', 'service/nbi', ':9999']
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except OSError:
        raise Refused('PORT_FORWARD_FAILED') from None
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 15)
        if not ready:
            raise Refused('PORT_FORWARD_FAILED')
        line = proc.stdout.readline().strip()
        match = FORWARD_LINE.fullmatch(line)
        if not match or proc.poll() is not None or not (1024 <= int(match.group(1)) <= 65535):
            raise Refused('PORT_FORWARD_FAILED')
        yield int(match.group(1))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()


class NoDupSafeLoader(yaml.SafeLoader):
    pass


def _unique_yaml_mapping(loader, node):
    if not isinstance(node, yaml.MappingNode):
        raise Refused('NBI_RESPONSE_INVALID')
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            if key in result:
                raise Refused('NBI_DUPLICATE_YAML_KEY')
        except TypeError:
            raise Refused('NBI_RESPONSE_INVALID') from None
        result[key] = loader.construct_object(value_node, deep=True)
    return result


NoDupSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                                _unique_yaml_mapping)


class NoRedirect(http_request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def getter(token, port, opener_factory=http_request.build_opener):
    if type(port) is not int or not (1024 <= port <= 65535):
        raise Refused('PORT_FORWARD_FAILED')
    origin = 'http://127.0.0.1:' + str(port) + '/osm'
    opener = opener_factory(http_request.ProxyHandler({}), http_request.HTTPHandler(), NoRedirect())

    def get(path):
        if path not in ALLOWED_NBI:
            raise Refused('NBI_PATH_NOT_ALLOWED')
        request = http_request.Request(origin + path, method='GET', headers={
            'Authorization': 'Bearer ' + token, 'Accept': 'application/yaml',
            'User-Agent': 'dell-p5-observe-only',
        })
        try:
            with opener.open(request, timeout=15) as response:
                if response.status != 200 or (response.headers.get('Content-Length') and
                    int(response.headers['Content-Length']) > MAX_NBI):
                    raise Refused('NBI_READ_FAILED')
                content = response.read(MAX_NBI + 1)
        except (OSError, http_error.HTTPError, http_error.URLError, TimeoutError, ValueError):
            raise Refused('NBI_READ_FAILED') from None
        if len(content) > MAX_NBI:
            raise Refused('NBI_RESPONSE_TOO_LARGE')
        try:
            decoded = yaml.load(content, Loader=NoDupSafeLoader)
        except Refused:
            raise
        except (yaml.YAMLError, ValueError, UnicodeError):
            raise Refused('NBI_RESPONSE_INVALID') from None
        if not isinstance(decoded, list) or len(decoded) > MAX_ITEMS or any(
                not isinstance(item, dict) for item in decoded):
            raise Refused('NBI_INVENTORY_INVALID')
        return decoded
    return get


def sanitized_inventory(get):
    """Only exact UUID/name fields are copied; raw OSM config never leaves memory."""
    observed = {path: get(path) for path in ALLOWED_NBI}
    def limited(rows, name_field):
        output = []
        for row in rows:
            uid = row.get('_id', row.get('id'))
            label = row.get(name_field)
            if not _uuid(uid) or not _name(label):
                raise Refused('NBI_INVENTORY_IDENTITY_INVALID')
            output.append({'id': uid, 'name': label})
        if len({r['id'] for r in output}) != len(output):
            raise Refused('NBI_DUPLICATE_IDENTITY')
        return sorted(output, key=lambda x: x['id'])
    projects = limited(observed['/admin/v1/projects'], 'name')
    vims = limited(observed['/admin/v1/vim_accounts'], 'name')
    clusters = limited(observed['/admin/v1/k8sclusters'], 'name')
    ns_rows = []
    for row in observed['/nslcm/v1/ns_instances']:
        uid = row.get('_id', row.get('id'))
        name = row.get('name', row.get('nsName'))
        state = row.get('nsState')
        if not _uuid(uid) or not _name(name):
            raise Refused('NBI_INVENTORY_IDENTITY_INVALID')
        if not isinstance(state, str) or not _name(state):
            state = 'UNKNOWN'
        ns_rows.append({'id': uid, 'name': name, 'state': state})
    if len({r['id'] for r in ns_rows}) != len(ns_rows):
        raise Refused('NBI_DUPLICATE_IDENTITY')
    return {'projects_count': len(projects), 'vims': vims, 'k8sclusters': clusters,
            'ns_instances': sorted(ns_rows, key=lambda x: x['id'])}


def observation(inventory=False, token_file=None, collect_fn=collect, evaluate_fn=evaluate,
                token_fn=private_token, tunnel_fn=tunnel, getter_fn=getter,
                policy_fn=p5_readiness.assess):
    """No NBI token opened until local HP gate and live Dell cluster pass checks."""
    policy = policy_fn()
    if policy.get('existing_policy_locked') is not True or policy.get('deployment_authorized') is not False:
        raise Refused('P5_POLICY_NOT_LOCKED')
    baseline = evaluate_fn(collect_fn())
    result = {
        'schema': SCHEMA, 'schema_version': 1,
        'status': 'OBSERVED', 'target': 'dell-precision-3680-research',
        'api_server': SERVER, 'nbi_ingress_host': NBI_HOST,
        **baseline, 'nbi_authenticated': False, 'osm_ns_vim_binding_verified': False,
        'live_vim_health_verified': False,
        'catalog_publication_authorized': False, 'deployment_authorized': False,
        'remediation_authorized': False, 'mutations_performed': False,
        'blockers': ['NO_DELL_SIGNED_RELEASE', 'NO_OPERATOR_DEPLOYMENT_APPROVAL',
                     'NO_ROLLBACK_QUALIFICATION', 'P4_APPROVAL_GATE_CLOSED',
                     'NO_AUTHENTICATED_OSM_NS_VIM_BINDING'],
    }
    if inventory:
        if token_file is None:
            raise Refused('TOKEN_REQUIRED')
        # Token is read only after the baseline check; the tunnel is owned and closed.
        token = token_fn(token_file)
        with tunnel_fn() as port:
            record = sanitized_inventory(getter_fn(token, port))
        result['nbi_authenticated'] = True
        result['inventory'] = record
        result['blockers'] = [b for b in result['blockers'] if b != 'NO_AUTHENTICATED_OSM_NS_VIM_BINDING']
        result['blockers'].append('NS_VIM_BINDING_SELECTION_AND_VERIFICATION_PENDING')
    return result


def save_private(path, record):
    output = Path(path)
    if output.is_symlink() or not output.parent.is_dir():
        raise Refused('INVALID_OUTPUT_LOCATION')
    data = (json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output, flags, 0o600)
    except OSError:
        raise Refused('INVALID_OUTPUT_LOCATION') from None
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    except OSError:
        try:
            output.unlink()
        except OSError:
            pass
        raise Refused('EVIDENCE_WRITE_FAILED') from None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight', action='store_true', help='Live read-only Kubernetes/NBI route check; no OSM auth')
    modes.add_argument('--inventory', action='store_true', help='Also query read-only OSM NBI inventory using private token')
    p.add_argument('--token-file', type=Path, help='Existing approved owner-only OSM token (inventory mode only)')
    p.add_argument('--output', type=Path, help='New private JSON evidence path (must not already exist)')
    args = p.parse_args(argv)
    if args.preflight and args.token_file:
        p.error('--token-file is only valid with --inventory')
    try:
        result = observation(inventory=args.inventory, token_file=args.token_file)
        if args.output:
            save_private(args.output, result)
    except (Refused, OSError, ValueError, TypeError, AttributeError) as exc:
        code = str(exc) if type(exc) is Refused else 'OBSERVER_ERROR'
        result = {'schema': SCHEMA, 'status': 'REJECTED', 'reason_code': code,
                  'deployment_authorized': False, 'mutations_performed': False}
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
