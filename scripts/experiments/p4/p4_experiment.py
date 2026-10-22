"""P4 experiment: real OSM v19 in-place Helm KDU upgrades on an isolated target.

Runs the P1-P3 lifecycle controller (scripts/lifecycle.py) against a disposable NS
of dummy CU/DU KDUs and records evidence for: revision-pinned chart paths, NS
identity preservation, multi-KDU sequencing, helm --atomic failure and automatic
rollback, explicit rollback, lost-response reconciliation and LCM restart recovery.

Safety model
- Only the second Dell is a valid target. Before every step the runner checks the
  OSM and Kubernetes endpoints, node names/addresses and OSM identities against the
  configuration and an explicit denylist of the HP production system.
- Every OSM mutation names the experiment NS (p4-kdu-upgrade-test) or the
  experiment descriptors (p4kdu_knf, p4kdu_ns); every Kubernetes/Helm mutation is
  limited to namespace p4-kdu-upgrade, except the opt-in LCM restart.
- The committed P4 gate (lifecycle.P4_KDU_UPGRADE_APPROVAL) stays None. This process
  opens it in memory only, around steps that act on the verified experiment NS.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.parse

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(HERE)]

import lifecycle  # noqa: E402
from lifecycle_osm import NbiLifecycle  # noqa: E402
from osm_catalog import Catalog, CatalogError, identifier, parse_response  # noqa: E402
import p4_packages  # noqa: E402

NAMESPACE = 'p4-kdu-upgrade'
VNFD, NSD = p4_packages.KNF, p4_packages.NSD
NS_NAME = 'p4-kdu-upgrade-test'
SCENARIO = 'p4kdu'
RELEASES = {'cu': 'p4kdu-cu', 'du': 'p4kdu-du'}
# Helm chart name of each release (p4_packages.chart_files names every KDU chart p4kdu-<kdu>).
CHART_NAMES = {release: 'p4kdu-' + kdu for kdu, release in RELEASES.items()}
MEMBER_INDEX = p4_packages.VNF_PROFILE
TERMINAL = lifecycle.TERMINAL
EXPECTED_CHART = {1: '0.1.0', 2: '0.2.0', 3: '0.3.0'}
# Production system that this runner must never reach (HP: original orchestrator).
HP_ADDRESSES = frozenset({'172.30.18.32'})
HP_NODE_NAMES = frozenset({'rclab-hp-406-g1-mt'})
HP_OSM_IDS = frozenset({
    'c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c',     # OSM project / KDU namespace
    '84be33a2-4d00-4392-83f4-39407e92e6fc',     # OSM K8s cluster lab-cluster
    'b0481f03-f5eb-47a2-9a20-bd72430b3b13',     # OSM VIM dummyvim
    '1b1cb300-b571-43f2-a567-6540895d7d0f',     # core-persistent NS
    '2c128db3-e27e-4dbe-8854-17751ba17680',     # ran-fran-oai NS
})
REGISTRY = {'schema_version': 1, 'aliases': {}, 'installer_components': [], 'scenarios': [{
    'key': SCENARIO, 'additive': False, 'generation_status': 'ready', 'deployment_mechanism': 'osm',
    'knf_package': VNFD, 'nsd_package': NSD, 'ns_name': NS_NAME,
    'charts': [{'kdu': kdu} for kdu in p4_packages.KDUS]}]}
RELEASE_BRANCH, RELEASE_TARGET = 'p4-experiment', 'p4-dell'


class ExperimentError(RuntimeError):
    pass


class TargetError(ExperimentError):
    """The configured endpoints are not provably the isolated experiment target."""


# --- configuration -------------------------------------------------------------------------------
def load_config(path):
    config = json.loads(Path(path).read_text())
    required = ('target_address', 'osm_url', 'osm_user', 'osm_password_file', 'osm_project', 'vim_account',
                'k8s_cluster', 'kubeconfig', 'allowed_api_addresses', 'allowed_node_addresses', 'work_dir')
    missing = [k for k in required if not config.get(k)]
    if missing:
        raise ExperimentError('Missing configuration: ' + ', '.join(missing))
    config.setdefault('osm_namespace', 'osm')
    config.setdefault('osm_ca_file', None)
    config.setdefault('kubectl', '/usr/bin/kubectl')
    config.setdefault('helm', '/usr/local/bin/helm')
    config.setdefault('timeouts', {})
    for key, default in (('kdu_timeout', 150), ('op_timeout', 900), ('health_timeout', 300), ('poll', 5),
                         ('clock_skew', 10)):
        config['timeouts'].setdefault(key, default)
    for key in ('osm_password_file', 'kubeconfig', 'work_dir'):
        config[key] = str(Path(config[key]).expanduser())
    return config


def read_secret(path):
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise ExperimentError('Password file must be a private regular file owned by the caller')
    return Path(path).read_text().strip()


def resolve(host):
    try:
        return {a[4][0] for a in socket.getaddrinfo(host, None)}
    except OSError:
        raise TargetError('Cannot resolve ' + host) from None


# --- OSM ---------------------------------------------------------------------------------------------
class Osm:
    """NBI calls for the experiment. Mutations are restricted to experiment identities."""
    def __init__(self, request):
        self.request = request
        self.lifecycle = NbiLifecycle(request)

    def _get(self, path):
        return parse_response(self.request('GET', path))

    def _list(self, path):
        value = self._get(path)
        if not isinstance(value, list):
            raise CatalogError('Invalid OSM listing: ' + path)
        return value

    def projects(self):
        return self._list('/admin/v1/projects')

    def vims(self):
        return self._list('/admin/v1/vim_accounts')

    def k8sclusters(self):
        return self._list('/admin/v1/k8sclusters')

    def ns_instances(self):
        return self.lifecycle.list_ns()

    def experiment_ns(self):
        matches = [n for n in self.ns_instances() if n.get('name', n.get('nsName')) == NS_NAME]
        if len(matches) > 1:
            raise ExperimentError('More than one experiment NS exists; resolve manually')
        return matches[0] if matches else None

    def operation(self, operation):
        value = self._get('/nslcm/v1/ns_lcm_op_occs/' + identifier(operation))
        if not isinstance(value, dict) or value.get('_id', value.get('id')) != operation:
            raise CatalogError('OSM returned a different operation')
        return value

    def instantiate(self, nsd_id, vim_id):
        body = {'nsdId': identifier(nsd_id), 'nsName': NS_NAME, 'vimAccountId': identifier(vim_id),
                'nsDescription': 'P4 isolated KDU upgrade experiment (disposable)',
                'additionalParamsForVnf': [{'member-vnf-index': MEMBER_INDEX, 'additionalParamsForKdu': [
                    {'kdu_name': kdu, 'k8s-namespace': NAMESPACE, 'kdu-deployment-name': release}
                    for kdu, release in RELEASES.items()]}]}
        response = parse_response(self.request('POST', '/nslcm/v1/ns_instances_content', yaml.safe_dump(body).encode()))
        if not isinstance(response, dict):
            raise CatalogError('Invalid instantiate response')
        return identifier(response.get('id')), identifier(response.get('nslcmop_id'))

    def _experiment_ns_id(self, ns_id):
        ns = self.lifecycle.get_ns(ns_id)
        if ns.get('name', ns.get('nsName')) != NS_NAME:
            raise ExperimentError('Refusing to act on a non-experiment NS')
        return identifier(ns_id)

    def terminate(self, ns_id):
        ns_id = self._experiment_ns_id(ns_id)
        response = parse_response(self.request('POST', '/nslcm/v1/ns_instances/' + ns_id + '/terminate', b''))
        return identifier((response or {}).get('id'))

    def delete_ns(self, ns_id):
        ns_id = self._experiment_ns_id(ns_id)
        self.request('DELETE', '/nslcm/v1/ns_instances/' + ns_id)

    def delete_package(self, kind, name):
        if name not in (VNFD, NSD):
            raise ExperimentError('Refusing to delete a non-experiment descriptor')
        package = self.lifecycle.package(kind, name)
        if package:
            base = '/vnfpkgm/v1/vnf_packages/' if kind == 'knf' else '/nsd/v1/ns_descriptors/'
            self.request('DELETE', base + package['id'])
        return package


class TargetLockedOsm:
    """What the P1 controller sees: KDU actions only on the verified experiment NS."""
    def __init__(self, osm, ns_id):
        self._osm, self._ns_id = osm, ns_id

    def __getattr__(self, name):
        return getattr(self._osm, name)

    def kdu_action(self, ns_id, *args):
        if ns_id != self._ns_id:
            raise ExperimentError('Refusing a KDU action outside the experiment NS')
        return self._osm.kdu_action(ns_id, *args)


class InterruptedResponse:
    """Request wrapper that loses one action exchange, before or after it reaches OSM."""
    def __init__(self, request, when, primitive, kdu):
        self.inner, self.when, self.primitive, self.kdu, self.fired = request, when, primitive, kdu, False

    def __call__(self, method, path, data=None, content_type='application/yaml'):
        if (not self.fired and method == 'POST' and path.endswith('/action') and data and
                (yaml.safe_load(data) or {}).get('primitive') == self.primitive and
                yaml.safe_load(data).get('kdu_name') == self.kdu):
            self.fired = True
            if self.when == 'before-send':
                raise CatalogError('P4 simulated transport failure before the request was sent')
            self.inner(method, path, data, content_type)
            raise CatalogError('P4 simulated lost response after OSM accepted the request')
        return self.inner(method, path, data, content_type)


class RestartLcmWhileProcessing:
    """Lifecycle adapter that restarts OSM LCM the first time an upgrade of `kdu` is PROCESSING."""
    def __init__(self, osm, kdu, restart, record):
        self._osm, self.kdu, self.restart, self.record = osm, kdu, restart, record
        self.operations, self.fired = {}, False

    def __getattr__(self, name):
        return getattr(self._osm, name)

    def kdu_action(self, ns_id, member_index, kdu_name, primitive, *rest):
        operation = self._osm.kdu_action(ns_id, member_index, kdu_name, primitive, *rest)
        self.operations[operation] = (primitive, kdu_name)
        return operation

    def operation_state(self, operation):
        state = self._osm.operation_state(operation)
        if not self.fired and state == 'PROCESSING' and self.operations.get(operation) == ('upgrade', self.kdu):
            self.fired = True
            self.record({'operation': operation, 'state_before_restart': state})
            self.restart()
        return state


# --- Kubernetes / Helm ---------------------------------------------------------------------------------
class ClusterError(ExperimentError):
    pass


class HelmMetadataError(ClusterError):
    """Helm release metadata is missing, ambiguous or inconsistent; never guessed."""


def chart_version(release, chart):
    """Chart version from Helm's '<chart name>-<version>' field (helm list/history).

    Only the release's own chart name and a plain X.Y.Z version (all P4 charts) are
    accepted, so the split between name and version is unambiguous.
    """
    match = re.fullmatch(re.escape(CHART_NAMES[release]) + r'-(\d+\.\d+\.\d+)', chart) \
        if isinstance(chart, str) and release in CHART_NAMES else None
    if not match:
        raise HelmMetadataError(release + ': unexpected Helm chart field ' + json.dumps(chart))
    return match[1]


def _revision(value, release, source):
    """Helm revisions: integers in status/history, decimal strings in helm list."""
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value):
        value = int(value)
    if type(value) is not int or value < 1:
        raise HelmMetadataError(release + ': invalid revision in helm ' + source + ': ' + json.dumps(value))
    return value


def release_view(release, status, history, listing):
    """Cross-checked view of one release from helm status, history and list (-o json).

    helm status supplies the revision (`version`) and state (`info.status`); its
    `chart` object is not relied on (absent in the output observed on the Dell) and is
    only cross-checked when present. The chart and app version come from the latest
    helm history entry and must equal the release's single helm list entry. Returns
    None only when no source knows the release.
    """
    entries = [r for r in listing if isinstance(r, dict) and r.get('name') == release] \
        if isinstance(listing, list) else None
    if entries is None or not isinstance(history, list):
        raise HelmMetadataError(release + ': helm list/history output is not a JSON list')
    if status is None and not history and not entries:
        return None
    if status is None or not history or not entries:
        raise HelmMetadataError(release + ': release known to only some of helm status/history/list')
    if len(entries) != 1:
        raise HelmMetadataError(release + ': ambiguous helm list (' + str(len(entries)) + ' entries)')
    listed = entries[0]
    if not isinstance(status, dict) or status.get('name') != release or status.get('namespace') != NAMESPACE \
            or listed.get('namespace') != NAMESPACE:
        raise HelmMetadataError(release + ': helm status/list name or namespace mismatch')
    state = (status.get('info') or {}).get('status')
    if not isinstance(state, str) or not state:
        raise HelmMetadataError(release + ': helm status has no info.status')
    revisions_seen = []
    for item in history:
        if not isinstance(item, dict) or not isinstance(item.get('status'), str) or \
                not isinstance(item.get('app_version'), str):
            raise HelmMetadataError(release + ': malformed helm history entry ' + json.dumps(item, default=str))
        revisions_seen.append({'revision': _revision(item.get('revision'), release, 'history'), 'status': item['status'],
                        'chart': item.get('chart'), 'chart_version': chart_version(release, item.get('chart')),
                        'app_version': item['app_version'], 'description': item.get('description'),
                        'updated': item.get('updated')})
    revisions = [e['revision'] for e in revisions_seen]
    if len(set(revisions)) != len(revisions) or revisions != sorted(revisions):
        raise HelmMetadataError(release + ': helm history revisions duplicated or out of order')
    latest, version = revisions_seen[-1], _revision(status.get('version'), release, 'status')
    current = {'version': version, 'state': state, 'chart': latest['chart_version'],
               'chart_ref': latest['chart'], 'app_version': latest['app_version']}
    if latest['revision'] != version or latest['status'] != state:
        raise HelmMetadataError(release + ': helm status and latest history entry disagree')
    if _revision(listed.get('revision'), release, 'list') != version or listed.get('status') != state or \
            listed.get('chart') != latest['chart'] or listed.get('app_version') != latest['app_version']:
        raise HelmMetadataError(release + ': helm list and helm history disagree')
    metadata = (status.get('chart') or {}).get('metadata') if isinstance(status.get('chart'), dict) else None
    if status.get('chart') is not None and (
            not isinstance(metadata, dict) or metadata.get('name') != CHART_NAMES[release] or
            metadata.get('version') != current['chart'] or metadata.get('appVersion', '') != current['app_version']):
        raise HelmMetadataError(release + ': chart metadata in helm status contradicts helm history')
    return dict(current, history=revisions_seen)


class Cluster:
    """kubectl/helm on the experiment target. Mutations are limited by _allowed()."""
    def __init__(self, kubectl, helm, kubeconfig, osm_namespace, run=subprocess.run, timeout=180):
        self.kubectl_bin, self.helm_bin, self.kubeconfig = kubectl, helm, kubeconfig
        self.osm_namespace, self.run, self.timeout = osm_namespace, run, timeout
        self.commands = []

    def _allowed(self, argv):
        tool, args = argv[0], argv[1:]
        verb = args[0]
        scoped = '-n' in args and args[args.index('-n') + 1]
        if tool == self.kubectl_bin:
            if verb in ('get', 'version'):
                return True
            if verb == 'delete':
                return args[1:3] == ['namespace', NAMESPACE]
            if verb == 'rollout':
                return args[2] == 'deployment/lcm' and scoped == self.osm_namespace
            return False
        if verb in ('status', 'history', 'list', 'version', 'get'):
            return verb == 'version' or scoped == NAMESPACE
        if verb == 'rollback':
            return args[1] in RELEASES.values() and scoped == NAMESPACE
        return False

    def _run(self, argv, timeout=None):
        if not self._allowed(argv):
            raise ClusterError('Command outside the experiment scope refused: ' + ' '.join(argv[:4]))
        self.commands.append(list(argv))
        env = {'KUBECONFIG': self.kubeconfig, 'PATH': '/usr/local/bin:/usr/bin:/bin',
               'HOME': os.environ.get('HOME', '/tmp')}
        result = self.run(argv, capture_output=True, env=env, timeout=timeout or self.timeout, check=False)
        return result

    def _json(self, argv, missing_ok=False):
        result = self._run(argv)
        if result.returncode:
            stderr = result.stderr.decode('utf-8', 'replace') if isinstance(result.stderr, bytes) else str(result.stderr)
            if missing_ok and ('NotFound' in stderr or 'not found' in stderr):
                return None
            raise ClusterError(' '.join(argv[1:3]) + ' failed: ' + stderr.strip()[:300])
        return json.loads(result.stdout)

    def kubectl(self, *args):
        return [self.kubectl_bin, *args]

    def helm(self, *args):
        return [self.helm_bin, *args]

    # read-only
    def api_server(self):
        config = yaml.safe_load(Path(self.kubeconfig).read_text())
        return [c['cluster']['server'] for c in config.get('clusters', [])]

    def nodes(self):
        return self._json(self.kubectl('get', 'nodes', '-o', 'json'))['items']

    def namespace(self, name=NAMESPACE):
        return self._json(self.kubectl('get', 'namespace', name, '-o', 'json'), missing_ok=True)

    def lcm_pods(self):
        pods = self._json(self.kubectl('get', 'pods', '-n', self.osm_namespace, '-o', 'json'))['items']
        return [p for p in pods if p['metadata']['name'].startswith('lcm-')]

    def lcm_image_digest(self):
        digests = {s.get('imageID', '').rpartition('@')[2] for p in self.lcm_pods()
                   for s in p.get('status', {}).get('containerStatuses', [])}
        if len(digests) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', next(iter(digests))):
            raise ClusterError('OSM LCM image digest cannot be determined')
        return next(iter(digests))

    def release_status(self, release):
        return self._json(self.helm('status', release, '-n', NAMESPACE, '-o', 'json'), missing_ok=True)

    def release_history(self, release):
        return self._json(self.helm('history', release, '-n', NAMESPACE, '-o', 'json'), missing_ok=True) or []

    def releases(self):
        return self._json(self.helm('list', '-n', NAMESPACE, '-a', '-o', 'json'))

    def release_view(self, release):
        """release_view() of live helm output; read twice before declaring it inconsistent
        (a release can change between the three helm calls while an operation finishes)."""
        try:
            return release_view(release, self.release_status(release), self.release_history(release), self.releases())
        except HelmMetadataError:
            return release_view(release, self.release_status(release), self.release_history(release), self.releases())

    def configmap_marker(self, release):
        value = self._json(self.kubectl('get', 'configmap', release + '-release', '-n', NAMESPACE, '-o', 'json'),
                           missing_ok=True)
        return value and value.get('data', {}).get('release')

    def deployment(self, release):
        return self._json(self.kubectl('get', 'deployment', release, '-n', NAMESPACE, '-o', 'json'), missing_ok=True)

    def inventory(self):
        """Namespace objects reduced to identity metadata (inventory_item); Secret data and
        object specs are dropped here and never reach the evidence."""
        value = self._json(self.kubectl('get', INVENTORY_KINDS, '-n', NAMESPACE, '-o', 'json'))
        return sorted((inventory_item(i) for i in value['items']), key=lambda i: (i['kind'], i['name']))

    # mutations (scope-checked)
    def restart_lcm(self):
        self._run(self.kubectl('rollout', 'restart', 'deployment/lcm', '-n', self.osm_namespace))
        result = self._run(self.kubectl('rollout', 'status', 'deployment/lcm', '-n', self.osm_namespace,
                                        '--timeout=600s'), timeout=660)
        if result.returncode:
            raise ClusterError('OSM LCM did not become ready after restart')

    def helm_rollback(self, release):
        result = self._run(self.helm('rollback', release, '-n', NAMESPACE, '--wait', '--timeout', '300s'), timeout=360)
        if result.returncode:
            raise ClusterError('helm rollback of ' + release + ' failed')

    def delete_namespace(self):
        result = self._run(self.kubectl('delete', 'namespace', NAMESPACE, '--wait=true', '--timeout=300s'), timeout=360)
        if result.returncode:
            raise ClusterError('Namespace deletion failed')


def deployment_ready(deployment):
    if not deployment:
        return False, 'missing'
    spec, status = deployment.get('spec', {}), deployment.get('status', {})
    replicas = spec.get('replicas', 1)
    ready = (status.get('observedGeneration', 0) >= deployment['metadata'].get('generation', 1) and
             status.get('updatedReplicas', 0) == replicas and status.get('readyReplicas', 0) == replicas and
             status.get('availableReplicas', 0) == replicas and status.get('replicas', 0) == replicas)
    return ready, 'ready' if ready else 'not ready: ' + json.dumps(
        {k: status.get(k) for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')})


# Namespaced kinds a workload or leftover could take; all but the expected ones are foreign.
INVENTORY_KINDS = ('deployments,replicasets,pods,services,configmaps,secrets,statefulsets,daemonsets,jobs,'
                   'cronjobs,persistentvolumeclaims,serviceaccounts,roles,rolebindings,networkpolicies,ingresses,'
                   'horizontalpodautoscalers,poddisruptionbudgets,endpoints,endpointslices,limitranges,'
                   'resourcequotas,replicationcontrollers')
HELM_SECRET = re.compile(r'sh\.helm\.release\.v1\.(?P<release>[a-z0-9-]+)\.v(?P<revision>[1-9][0-9]*)')
CHART_LABELS = {'app.kubernetes.io/name': 'p4kdu', 'app.kubernetes.io/part-of': 'p4-kdu-upgrade'}


def inventory_item(item):
    """Identity metadata of one namespace object: never data, spec or other annotations."""
    metadata = item.get('metadata') or {}
    annotations = metadata.get('annotations') or {}
    return {'kind': item.get('kind'), 'name': metadata.get('name'), 'uid': metadata.get('uid'),
            'type': item.get('type'), 'labels': dict(metadata.get('labels') or {}),
            'annotations': {k: v for k, v in annotations.items() if k.startswith('meta.helm.sh/')},
            'owners': [{k: o.get(k) for k in ('kind', 'name', 'uid', 'controller')}
                       for o in metadata.get('ownerReferences') or []]}


def _controller(item):
    owners = item['owners']
    return owners[0] if len(owners) == 1 and owners[0].get('controller') is True else None


def foreign_objects(inventory):
    """Objects in the experiment namespace not provably created for the P4 releases.

    Accepted, each by exact identity rather than name prefix:
    - ConfigMap kube-root-ca.crt and ServiceAccount default (created in every namespace by
      Kubernetes; no owner, no labels)
    - per release R: Deployment R and ConfigMap R-release rendered by the P4 chart and
      owned by Helm release R in this namespace (chart labels + meta.helm.sh annotations)
    - ReplicaSets controlled by such a Deployment (matched by uid, name R-<pod-template-hash>)
    - Pods controlled by such a ReplicaSet (uid, same pod-template-hash and instance label)
    - Helm storage Secrets sh.helm.release.v1.R.vN of type helm.sh/release.v1 with
      owner=helm, name=R, version=N labels
    Returns [(kind, name, reason)] for everything else.
    """
    def helm_owned(item, release):
        labels = item['labels']
        return (not item['owners'] and labels.get('app.kubernetes.io/managed-by') == 'Helm' and
                labels.get('app.kubernetes.io/instance') == release and
                all(labels.get(k) == v for k, v in CHART_LABELS.items()) and
                item['annotations'] == {'meta.helm.sh/release-name': release,
                                        'meta.helm.sh/release-namespace': NAMESPACE})

    releases = set(RELEASES.values())
    deployments = {i['uid']: i['name'] for i in inventory if i['kind'] == 'Deployment' and i['uid'] and
                   i['name'] in releases and helm_owned(i, i['name'])}
    replicasets = {}
    for i in inventory:
        owner, template = (_controller(i), i['labels'].get('pod-template-hash')) if i['kind'] == 'ReplicaSet'             else (None, None)
        if owner and owner.get('kind') == 'Deployment' and deployments.get(owner.get('uid')) == owner.get('name')                 and template and i['name'] == owner['name'] + '-' + template and i['uid'] and                 i['labels'].get('app.kubernetes.io/instance') == owner['name']:
            replicasets[i['uid']] = (i['name'], owner['name'], template)
    foreign = []
    for i in inventory:
        kind, name, labels = i['kind'], i['name'], i['labels']
        if kind == 'ConfigMap' and name == 'kube-root-ca.crt' or kind == 'ServiceAccount' and name == 'default':
            ok = not i['owners'] and not labels     # created in every namespace by Kubernetes
        elif kind == 'ConfigMap':
            ok = name[:-len('-release')] in releases and name.endswith('-release') and \
                helm_owned(i, name[:-len('-release')])
        elif kind == 'Deployment':
            ok = i['uid'] in deployments
        elif kind == 'ReplicaSet':
            ok = i['uid'] in replicasets
        elif kind == 'Pod':
            owner = _controller(i)
            parent = owner and owner.get('kind') == 'ReplicaSet' and replicasets.get(owner.get('uid'))
            ok = bool(parent) and parent[0] == owner.get('name') and labels.get('pod-template-hash') == parent[2] \
                and labels.get('app.kubernetes.io/instance') == parent[1] and name.startswith(parent[0] + '-')
        elif kind == 'Secret':
            match = HELM_SECRET.fullmatch(name or '')
            ok = bool(match) and match['release'] in releases and i['type'] == 'helm.sh/release.v1' and \
                not i['owners'] and labels.get('owner') == 'helm' and labels.get('name') == match['release'] and \
                labels.get('version') == match['revision']
        else:
            ok = False
        if not ok:
            foreign.append((kind, name, 'not an object of the P4 Helm releases'))
    return foreign


class Observer:
    """Read-only health/revision view used by the P1 controller."""
    def __init__(self, cluster):
        self.cluster = cluster

    def helm_revision(self, instance):
        view = self.cluster.release_view(instance)
        if not view:
            raise ClusterError('Helm release missing: ' + instance)
        return view['version']

    def health(self, entry, kdus, since):
        details = []
        for kdu in kdus:
            release = RELEASES[kdu['name']]
            try:
                view = self.cluster.release_view(release) or {}
            except HelmMetadataError as error:
                details.append(str(error))
                continue
            if view.get('state') != 'deployed':
                details.append(release + ' helm ' + str(view.get('state')))
                continue
            ready, detail = deployment_ready(self.cluster.deployment(release))
            if not ready:
                details.append(release + ' ' + detail)
        return not details, '; '.join(details) or 'all KDUs deployed and ready'


# --- evidence ------------------------------------------------------------------------------------------
class Evidence:
    def __init__(self, directory, clock=time):
        self.directory, self.clock = Path(directory), clock
        self.directory.mkdir(parents=True, exist_ok=True)
        self.checks, self.steps = [], []

    def record(self, step, name, value):
        path = self.directory / step / (name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
        return path

    def check(self, step, name, passed, detail=None):
        self.checks.append({'step': step, 'check': name, 'passed': bool(passed), 'detail': detail})
        return bool(passed)

    def observe(self, step, name, value):
        """A recorded fact that the experiment documents rather than asserts."""
        self.checks.append({'step': step, 'check': name, 'passed': None, 'observed': value})

    def summary(self, extra=None):
        files = {str(p.relative_to(self.directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(self.directory.rglob('*.json')) if p.name != 'summary.json'}
        failed = [c for c in self.checks if c['passed'] is False]
        summary = dict({'steps': self.steps, 'checks': self.checks, 'failed': len(failed),
                        'passed': sum(1 for c in self.checks if c['passed'] is True),
                        'observations': sum(1 for c in self.checks if c['passed'] is None),
                        'evidence_sha256': files, 'generated_at': self.clock.time()}, **(extra or {}))
        data = json.dumps(summary, indent=2, sort_keys=True, default=str) + '\n'
        (self.directory / 'summary.json').write_text(data)
        summary['summary_sha256'] = hashlib.sha256(data.encode()).hexdigest()
        return summary


# --- experiment ---------------------------------------------------------------------------------------------
STEPS = ('preflight', 'onboard', 'instantiate', 'upgrade_rev2_lost_response', 'explicit_rollback',
         'failed_upgrade_auto_rollback', 'partial_failure_operator_resolution', 'lost_before_send',
         'lcm_restart_recovery')


class Experiment:
    def __init__(self, config, request, cluster, evidence, clock=time, commit=None, resolver=resolve):
        self.config, self.request, self.cluster, self.evidence, self.clock = config, request, cluster, evidence, clock
        self.osm = Osm(request)
        self.observer = Observer(cluster)
        self.resolver = resolver
        self.work = Path(config['work_dir'])
        self.state_path = self.work / 'state' / 'active-ran.yaml'
        self.commit = commit or git_commit()
        self.timeouts = config['timeouts']
        self.packages = {revision: p4_packages.build(revision) for revision in p4_packages.REVISIONS}
        self.ids, self.lcm_digest, self.context, self.ns_id, self.ns_count = {}, None, None, None, None

    # -- target verification (runs before every step) --------------------------------------------------
    def verify_target(self):
        target = self.config['target_address']
        if target in HP_ADDRESSES:
            raise TargetError('The configured target is the HP production system')
        osm_host = urllib.parse.urlsplit(self.config['osm_url']).hostname or ''
        osm_addresses = self.resolver(osm_host)
        if osm_addresses & HP_ADDRESSES or osm_addresses != {target}:
            raise TargetError('OSM endpoint does not resolve only to the experiment target')
        allowed_api = set(self.config['allowed_api_addresses'])
        if allowed_api & HP_ADDRESSES:
            raise TargetError('HP address listed as an allowed API server')
        for server in self.cluster.api_server():
            host = urllib.parse.urlsplit(server).hostname or ''
            resolved = self.resolver(host)
            if resolved & HP_ADDRESSES or not resolved <= allowed_api:
                raise TargetError('Kubernetes API server is not an allowed experiment address')
        nodes = self.cluster.nodes()
        allowed_nodes = set(self.config['allowed_node_addresses'])
        for node in nodes:
            addresses = {a['address'] for a in node['status'].get('addresses', []) if a['type'] == 'InternalIP'}
            if node['metadata']['name'] in HP_NODE_NAMES or addresses & HP_ADDRESSES or not addresses <= allowed_nodes:
                raise TargetError('Kubernetes node is not an experiment node: ' + node['metadata']['name'])
        seen = {str(x.get('_id')) for listing in (self.osm.projects(), self.osm.vims(), self.osm.k8sclusters(),
                                                  self.osm.ns_instances()) for x in listing}
        if seen & HP_OSM_IDS:
            raise TargetError('OSM contains HP production identities; refusing')
        return {'osm_addresses': sorted(osm_addresses), 'nodes': [n['metadata']['name'] for n in nodes]}

    def discover(self):
        projects = [p for p in self.osm.projects() if p.get('name') == self.config['osm_project']]
        vims = [v for v in self.osm.vims() if v.get('name') == self.config['vim_account']]
        clusters = [k for k in self.osm.k8sclusters() if k.get('name') == self.config['k8s_cluster']]
        if len(projects) != 1 or len(vims) != 1 or len(clusters) != 1:
            raise ExperimentError('OSM project, VIM or K8s cluster registration is missing or ambiguous')
        cluster = clusters[0]
        if cluster.get('vim_account') != vims[0]['_id'] or not (cluster.get('deployment_methods') or {}).get('helm-chart-v3'):
            raise ExperimentError('OSM K8s cluster is not a helm-chart-v3 cluster of the configured VIM')
        if (cluster.get('_admin') or {}).get('operationalState') not in ('ENABLED', None):
            raise ExperimentError('OSM K8s cluster is not ENABLED')
        self.ids = {'project': projects[0]['_id'], 'vim': vims[0]['_id'], 'k8scluster': cluster['_id']}
        return self.ids

    # -- helpers -----------------------------------------------------------------------------------------------
    def policy(self, auto_rollback):
        return {'branch': RELEASE_BRANCH, 'target': RELEASE_TARGET, 'upgrade_scenarios': {SCENARIO},
                'auto_rollback': auto_rollback, 'lock_timeout': 60, 'op_timeout': self.timeouts['op_timeout'],
                'health_timeout': self.timeouts['health_timeout'], 'health_interval': self.timeouts['poll'],
                'kdu_timeout': self.timeouts['kdu_timeout'], 'clock_skew': self.timeouts['clock_skew']}

    def release(self, revision):
        blobs = self.packages[revision]
        return ({'schema': 1, 'commit': self.commit, 'branch': RELEASE_BRANCH, 'target': RELEASE_TARGET,
                 'ci_status': 'success', 'scenarios': {SCENARIO: {
                     'artifacts': {n: hashlib.sha256(b).hexdigest() for n, b in blobs.items()}}}}, blobs)

    def wait_operation(self, operation, timeout=None):
        deadline = self.clock.monotonic() + (timeout or self.timeouts['op_timeout'])
        while True:
            state = self.osm.lifecycle.operation_state(operation)
            if state in TERMINAL or self.clock.monotonic() >= deadline:
                return state
            self.clock.sleep(self.timeouts['poll'])

    def experiment_ns(self):
        ns = self.osm.experiment_ns()
        if not ns:
            raise ExperimentError('Experiment NS does not exist')
        deployed = ns.get('_admin', {}).get('deployed', {}).get('K8s', [])
        if sorted((k.get('kdu-name'), k.get('kdu-instance'), k.get('namespace')) for k in deployed) != sorted(
                (kdu, release, NAMESPACE) for kdu, release in RELEASES.items()):
            raise ExperimentError('Experiment NS KDUs are not the isolated p4kdu releases')
        if self.ns_id and ns['_id'] != self.ns_id:
            raise ExperimentError('Experiment NS identity changed')
        return ns

    @contextlib.contextmanager
    def gate(self):
        """Open the P4 gate in this process only, for the verified experiment NS."""
        ns = self.experiment_ns()
        if lifecycle.P4_KDU_UPGRADE_APPROVAL is not None:
            raise ExperimentError('P4 gate is unexpectedly open in the committed code')
        approval = {'p4_report_sha256': hashlib.sha256(('p4-experiment-in-progress:' + ns['_id']).encode()).hexdigest(),
                    'osm_lcm_digest': self.lcm_digest, 'approved_commit': self.commit}
        lifecycle.P4_KDU_UPGRADE_APPROVAL = approval
        try:
            lifecycle.require_p4_approval()
            yield TargetLockedOsm(self.osm.lifecycle, ns['_id'])
        finally:
            lifecycle.P4_KDU_UPGRADE_APPROVAL = None

    def snapshot(self, step, label):
        ns = self.osm.experiment_ns()
        value = {'ns': None, 'vnfr': None, 'operations': [], 'helm': {}, 'markers': {}, 'deployments': {},
                 'ns_count': len(self.osm.ns_instances()), 'state': None}
        if ns:
            value['ns'] = {k: ns.get(k) for k in ('_id', 'name', 'nsState', 'currentOperation', 'nsd-id')}
            value['ns']['kdus'] = [{k: d.get(k) for k in ('kdu-name', 'kdu-instance', 'namespace', 'kdu-model',
                                                           'member-vnf-index')}
                                   for d in ns.get('_admin', {}).get('deployed', {}).get('K8s', [])]
            vnfrs = self.osm.lifecycle.vnf_instances(ns['_id'])
            value['vnfr'] = [{k: v.get(k) for k in ('_id', 'vnfd-id', 'revision')} for v in vnfrs]
            for operation in self.osm.lifecycle.ns_operations(ns['_id']):
                params = operation.get('operationParams') or {}
                value['operations'].append({
                    'id': operation.get('_id'), 'type': operation.get('lcmOperationType'),
                    'state': operation.get('operationState'), 'start': operation.get('startTime'),
                    'entered': operation.get('statusEnteredTime'), 'detail': str(operation.get('detailed-status'))[:200],
                    'primitive': params.get('primitive'), 'kdu': params.get('kdu_name'),
                    'kdu_model': (params.get('primitive_params') or {}).get('kdu_model'),
                    'timeout': params.get('timeout_ns_action')})
        for kdu, release in RELEASES.items():
            try:
                view = self.cluster.release_view(release)
                value['helm'][kdu] = {'status': view and {k: v for k, v in view.items() if k != 'history'},
                                      'history': view['history'] if view else [], 'error': None}
            except HelmMetadataError as error:   # recorded; every check on this release then fails
                value['helm'][kdu] = {'status': None, 'history': [], 'error': str(error)}
            value['markers'][kdu] = self.cluster.configmap_marker(release)
            value['deployments'][kdu] = deployment_ready(self.cluster.deployment(release))[1]
        if self.state_path.exists():
            value['state'] = yaml.safe_load(self.state_path.read_text())
        self.evidence.record(step, label, value)
        return value

    def expect_release(self, step, snap, kdu, chart, marker, label):
        helm = snap['helm'][kdu]['status'] or {}
        ok = (snap['helm'][kdu].get('error') is None and helm.get('state') == 'deployed' and
              helm.get('chart') == chart and helm.get('chart_ref') == CHART_NAMES[RELEASES[kdu]] + '-' + chart and
              helm.get('app_version') == marker and snap['markers'][kdu] == marker and
              snap['deployments'][kdu] == 'ready')
        return self.evidence.check(step, label + ': ' + kdu + ' serves ' + marker,
                                   ok, {'helm': helm, 'helm_error': snap['helm'][kdu].get('error'),
                                        'marker': snap['markers'][kdu], 'deployment': snap['deployments'][kdu]})

    def expect_identity(self, step, snap):
        self.evidence.check(step, 'NS identity preserved', snap['ns'] and snap['ns']['_id'] == self.ns_id,
                            snap['ns'] and snap['ns']['_id'])
        self.evidence.check(step, 'NS count unchanged', snap['ns_count'] == self.ns_count, snap['ns_count'])
        self.evidence.check(step, 'NS READY and idle', snap['ns'] and snap['ns']['nsState'] == 'READY' and
                            snap['ns']['currentOperation'] in ('IDLE', None), snap['ns'])

    def new_operations(self, before, after):
        known = {o['id'] for o in before['operations']}
        return [o for o in after['operations'] if o['id'] not in known]

    def runtime_context(self):
        """P1 runtime context of the experiment deployment (namespace created by OSM at instantiation)."""
        namespace = self.cluster.namespace()
        kube_system = self.cluster.namespace('kube-system')
        if not namespace or not kube_system:
            raise ExperimentError('Experiment or kube-system namespace missing')
        return {'project_id': self.ids['project'], 'vim_id': self.ids['vim'],
                'cluster_uid': kube_system['metadata']['uid'], 'namespace_uid': namespace['metadata']['uid'],
                'namespace': NAMESPACE, 'repo_root': str(ROOT), 'workload_kubeconfig': self.config['kubeconfig'],
                'deployment_profile': 'p4-experiment', 'osm_host': self.config['osm_url'],
                'k8s_cluster_id': self.ids['k8scluster']}

    def write_state(self, state):
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lifecycle.write_state(self.state_path, state, REGISTRY, self.context)

    def read_state(self):
        return lifecycle.read_state(self.state_path, REGISTRY, self.context)

    def operator_discard_pending(self, step, reason):
        """Operator decision after verifying OSM and Helm: drop the retained update record."""
        with lifecycle.lifecycle_lock(self.state_path, 'p4-operator', timeout=60):
            state = self.read_state()
            record = state['osm'].pop('pending_update', None)
            self.write_state(state)
        self.evidence.record(step, 'operator-resolution', {'reason': reason, 'discarded': record})
        return record

    def reconcile_until_settled(self, step, policy):
        results = []
        deadline = self.clock.monotonic() + self.timeouts['op_timeout']
        while True:
            result = lifecycle.reconcile_update(self.osm.lifecycle, self.observer, REGISTRY, self.state_path,
                                                self.context, policy, clock=self.clock)
            results.append(result)
            if result['action'] not in ('still-running', 'unhealthy') or self.clock.monotonic() >= deadline:
                self.evidence.record(step, 'reconcile', results)
                return result
            self.clock.sleep(self.timeouts['poll'])

    def run_release(self, step, revision, policy, osm):
        release, blobs = self.release(revision)
        try:
            result = lifecycle.process_release(osm, self.observer, REGISTRY, release, blobs, self.state_path,
                                               self.context, policy, clock=self.clock)
            outcome = {'result': result}
        except lifecycle.LifecycleError as error:
            outcome = {'error': type(error).__name__, 'message': str(error)}
        self.evidence.record(step, 'process-release', outcome)
        return outcome

    def restore_rev2(self, step, osm):
        """Bring every KDU back to revision-2 content through OSM rollback actions (bounded)."""
        for kdu, release in RELEASES.items():
            for _ in range(3):
                view = self.cluster.release_view(release) or {}
                if view.get('chart') == EXPECTED_CHART[2] and view.get('state') == 'deployed':
                    break
                operation = osm.kdu_action(self.ns_id, MEMBER_INDEX, kdu, 'rollback', {}, self.timeouts['kdu_timeout'])
                self.evidence.record(step, 'restore-' + kdu + '-' + operation[:8],
                                     {'operation': operation, 'state': self.wait_operation(operation)})

    # -- steps -----------------------------------------------------------------------------------------------
    def preflight(self, step):
        target = self.verify_target()
        ids = self.discover()
        self.lcm_digest = self.cluster.lcm_image_digest()
        versions = {'kubectl': self.cluster._json(self.cluster.kubectl('version', '-o', 'json')),
                    'helm': self.cluster._run(self.cluster.helm('version', '--short')).stdout}
        self.evidence.record(step, 'target', dict(target, ids=ids, lcm_digest=self.lcm_digest, versions=versions,
                                                  commit=self.commit, gate_committed=lifecycle.P4_KDU_UPGRADE_APPROVAL))
        clean = (self.osm.experiment_ns() is None and self.osm.lifecycle.package('knf', VNFD) is None and
                 self.osm.lifecycle.package('ns', NSD) is None and self.cluster.namespace() is None)
        self.evidence.check(step, 'no leftovers from an earlier run', clean)
        # A new run starts from no runner state; earlier state belongs to earlier evidence.
        state_dir = self.state_path.parent
        stale = sorted(p.name for p in state_dir.iterdir()) if state_dir.exists() else []
        self.evidence.check(step, 'no stale P1 runner state', not stale, {'state_dir': str(state_dir), 'files': stale})
        self.evidence.check(step, 'committed P4 gate is closed', lifecycle.P4_KDU_UPGRADE_APPROVAL is None)
        if not clean:
            raise ExperimentError('Experiment resources already exist; run teardown first')
        if stale:
            raise ExperimentError('P1 runner state from an earlier run exists in ' + str(state_dir) +
                                  '; archive it before a new run')

    def onboard(self, step):
        _, blobs = self.release(1)
        published = {kind: self.osm.lifecycle.publish(kind, name, blobs[name + '.tar.gz'])
                     for kind, name in (('knf', VNFD), ('ns', NSD))}
        self.evidence.record(step, 'published', published)
        self.evidence.check(step, 'revision 1 onboarded', all(p['revision'] == 1 for p in published.values()), published)

    def instantiate(self, step):
        nsd = self.osm.lifecycle.package('ns', NSD)
        self.ns_count = len(self.osm.ns_instances()) + 1
        submitted = self.clock.time()
        ns_id, operation = self.osm.instantiate(nsd['id'], self.ids['vim'])
        state = self.wait_operation(operation)
        self.ns_id = ns_id
        self.evidence.check(step, 'instantiation COMPLETED', state == 'COMPLETED', state)
        start = self.osm.operation(operation).get('startTime')
        # Lost-request adoption compares OSM operation times with this runner's clock.
        self.evidence.check(step, 'runner and OSM clocks agree within the adoption window',
                            type(start) in (int, float) and abs(start - submitted) <= self.timeouts['clock_skew'],
                            {'submitted': submitted, 'osm_start': start})
        ns = self.experiment_ns()
        self.evidence.check(step, 'KDUs deployed under member-vnf-index ' + MEMBER_INDEX, all(
            d.get('member-vnf-index') == MEMBER_INDEX for d in ns['_admin']['deployed']['K8s']))
        knf = self.osm.lifecycle.package('knf', VNFD)
        models = {d['kdu-name']: d.get('kdu-model') for d in ns['_admin']['deployed']['K8s']}
        self.evidence.check(step, 'KDU models are revision-pinned storage paths of revision 1', all(
            (m := lifecycle.KDU_MODEL.fullmatch(str(models[k]))) and m['id'] == knf['id'] and m['rev'] == '1'
            and m['package'] == VNFD and m['kdu'] == k for k in RELEASES), models)
        self.context = self.runtime_context()
        self.write_state({'active': SCENARIO, 'context': self.context,
                          'osm': {'active_instance_id': ns_id, 'active_scenario': SCENARIO,
                                  'active_operation_id': operation}})
        deadline = self.clock.monotonic() + self.timeouts['health_timeout']
        while not self.observer.health(REGISTRY['scenarios'][0], [{'name': k} for k in RELEASES], 0)[0] \
                and self.clock.monotonic() < deadline:
            self.clock.sleep(self.timeouts['poll'])
        snap = self.snapshot(step, 'after')
        for kdu in RELEASES:
            self.expect_release(step, snap, kdu, EXPECTED_CHART[1], 'r1', 'baseline')
            self.evidence.check(step, 'Helm release ' + RELEASES[kdu] + ' at revision 1',
                                (snap['helm'][kdu]['status'] or {}).get('version') == 1)
        self.expect_identity(step, snap)

    def upgrade_rev2_lost_response(self, step):
        """Revision 2 through P1: CU then DU; the DU response is lost after OSM accepted it."""
        before = self.snapshot(step, 'before')
        policy = self.policy(auto_rollback=False)
        lossy = Osm(InterruptedResponse(self.request, 'after-send', 'upgrade', 'du'))
        with self.gate():
            outcome = self.run_release(step, 2, policy, TargetLockedOsm(lossy.lifecycle, self.ns_id))
            self.evidence.check(step, 'lost DU response leaves the update uncertain',
                                outcome.get('error') == 'UpdateUncertain', outcome)
            pending = self.read_state()['osm'].get('pending_update') or {}
            self.evidence.check(step, 'pending update names the DU without an operation',
                                pending.get('current_kdu') == 'du' and 'du' not in pending.get('operations', {}), pending)
            adopted = lifecycle.adopt_lost_request(self.osm.lifecycle, REGISTRY, self.state_path, self.context, policy)
            self.evidence.record(step, 'adopt', adopted)
            self.evidence.check(step, 'lost DU request adopted from OSM operations', adopted['action'] == 'adopted', adopted)
            result = self.reconcile_until_settled(step, policy)
        self.evidence.check(step, 'reconciliation confirms the upgrade', result['action'] == 'confirmed-upgraded', result)
        after = self.snapshot(step, 'after')
        knf = self.osm.lifecycle.package('knf', VNFD)
        self.evidence.check(step, 'catalog revision 2 published under the same descriptor id',
                            knf['revision'] == 2, knf)
        for kdu in RELEASES:
            self.expect_release(step, after, kdu, EXPECTED_CHART[2], 'r2', 'in-place upgrade')
            history = after['helm'][kdu]['history']
            self.evidence.check(step, kdu + ' upgraded in place (same release, next revision)',
                                [h.get('revision') for h in history][-2:] == [1, 2], history)
        self.expect_identity(step, after)
        upgrades = [o for o in self.new_operations(before, after) if o['primitive'] == 'upgrade']
        by_kdu = {o['kdu']: o for o in upgrades}
        self.evidence.check(step, 'one upgrade operation per KDU, both COMPLETED',
                            sorted(by_kdu) == ['cu', 'du'] and len(upgrades) == 2 and
                            all(o['state'] == 'COMPLETED' for o in upgrades), upgrades)
        self.evidence.check(step, 'revision-pinned kdu_model used for each KDU', all(
            o['kdu_model'] == '/app/storage/{}:2/{}/helm-chart-v3s/{}'.format(knf['id'], VNFD, o['kdu'])
            for o in upgrades), [o['kdu_model'] for o in upgrades])
        if sorted(by_kdu) == ['cu', 'du']:
            self.evidence.check(step, 'DU upgrade submitted only after the CU upgrade completed',
                                by_kdu['du']['start'] is not None and by_kdu['cu']['entered'] is not None and
                                by_kdu['du']['start'] >= by_kdu['cu']['entered'],
                                {'cu_completed': by_kdu['cu']['entered'], 'du_started': by_kdu['du']['start']})
        self.evidence.check(step, 'action timeout passed to OSM', all(o['timeout'] == self.timeouts['kdu_timeout']
                                                                      for o in upgrades))
        state = after['state']['osm']
        self.evidence.check(step, 'P1 records revision 2 as the active release and keeps the previous one',
                            'pending_update' not in state and state.get('active_release', {}).get('knf_revision') == 2
                            and state.get('previous_release', {}).get('knf_revision') == 1, state)
        self.evidence.observe(step, 'NSR kdu-model after upgrade (expected stale: written only at instantiation)',
                              [k['kdu-model'] for k in after['ns']['kdus']])
        self.evidence.observe(step, 'VNFR revision after upgrade (expected unchanged)', after['vnfr'])

    def explicit_rollback(self, step):
        with self.gate() as osm:
            for expected_chart, marker in ((EXPECTED_CHART[1], 'r1'), (EXPECTED_CHART[2], 'r2')):
                before = self.snapshot(step, 'before-' + marker)
                operation = osm.kdu_action(self.ns_id, MEMBER_INDEX, 'du', 'rollback', {}, self.timeouts['kdu_timeout'])
                state = self.wait_operation(operation)
                after = self.snapshot(step, 'after-' + marker)
                self.evidence.check(step, 'rollback action COMPLETED (' + marker + ')', state == 'COMPLETED', state)
                self.expect_release(step, after, 'du', expected_chart, marker, 'explicit rollback')
                old, new = before['helm']['du']['status'] or {}, after['helm']['du']['status'] or {}
                self.evidence.check(step, 'rollback creates the next Helm revision (' + marker + ')',
                                    type(old.get('version')) is int and new.get('version') == old['version'] + 1,
                                    [old, new])
                self.expect_release(step, after, 'cu', EXPECTED_CHART[2], 'r2', 'CU untouched by DU rollback')
                self.expect_identity(step, after)

    def failed_upgrade_auto_rollback(self, step):
        before = self.snapshot(step, 'before')
        with self.gate() as osm:
            outcome = self.run_release(step, 3, self.policy(auto_rollback=True), osm)
        after = self.snapshot(step, 'after')
        self.evidence.check(step, 'failed DU upgrade reported, earlier KDUs rolled back',
                            outcome.get('error') == 'UpdateFailed' and 'rolled back' in outcome.get('message', ''), outcome)
        operations = self.new_operations(before, after)
        summary = [(o['primitive'], o['kdu'], o['state']) for o in operations]
        self.evidence.check(step, 'OSM operations: CU upgrade COMPLETED, DU upgrade FAILED, CU rollback COMPLETED',
                            summary == [('upgrade', 'cu', 'COMPLETED'), ('upgrade', 'du', 'FAILED'),
                                        ('rollback', 'cu', 'COMPLETED')], summary)
        du_history = after['helm']['du']['history']
        self.evidence.check(step, 'helm --atomic recorded the failed DU revision and rolled it back', any(
            h['status'] == 'failed' and h['chart_version'] == EXPECTED_CHART[3] for h in du_history) and
            du_history and du_history[-1].get('status') == 'deployed' and
            'Rollback' in str(du_history[-1].get('description')), du_history)
        for kdu in RELEASES:
            self.expect_release(step, after, kdu, EXPECTED_CHART[2], 'r2', 'previous release restored')
        state = after['state']['osm']
        self.evidence.check(step, 'P1 keeps revision 2 active and no pending update',
                            'pending_update' not in state and state['active_release']['knf_revision'] == 2, state)
        self.expect_identity(step, after)

    def partial_failure_operator_resolution(self, step):
        before = self.snapshot(step, 'before')
        policy = self.policy(auto_rollback=False)
        with self.gate() as osm:
            outcome = self.run_release(step, 3, policy, osm)
            mid = self.snapshot(step, 'after-failure')
            pending = mid['state']['osm'].get('pending_update') or {}
            self.evidence.check(step, 'partial failure retained as failed-partial',
                                outcome.get('error') == 'UpdateFailed' and pending.get('stage') == 'failed-partial'
                                and pending.get('upgraded') == ['cu'], {'outcome': outcome, 'pending': pending})
            self.expect_release(step, mid, 'cu', EXPECTED_CHART[3], 'r3', 'mixed release')
            self.expect_release(step, mid, 'du', EXPECTED_CHART[2], 'r2', 'mixed release')
            result = self.reconcile_until_settled(step, policy)
            self.evidence.check(step, 'reconciliation requires an operator decision',
                                result['action'] == 'operator-decision-required', result)
            operation = osm.kdu_action(self.ns_id, MEMBER_INDEX, 'cu', 'rollback', {}, self.timeouts['kdu_timeout'])
            self.evidence.check(step, 'operator rollback of the CU COMPLETED', self.wait_operation(operation) == 'COMPLETED')
        self.operator_discard_pending(step, 'CU rolled back to revision 2 content after a partial failure')
        after = self.snapshot(step, 'after')
        for kdu in RELEASES:
            self.expect_release(step, after, kdu, EXPECTED_CHART[2], 'r2', 'resolved')
        self.expect_identity(step, after)
        self.evidence.check(step, 'no new upgrade of the DU succeeded', not any(
            o['primitive'] == 'upgrade' and o['kdu'] == 'du' and o['state'] == 'COMPLETED'
            for o in self.new_operations(before, after)))

    def lost_before_send(self, step):
        before = self.snapshot(step, 'before')
        policy = self.policy(auto_rollback=False)
        lossy = Osm(InterruptedResponse(self.request, 'before-send', 'upgrade', 'cu'))
        with self.gate():
            outcome = self.run_release(step, 3, policy, TargetLockedOsm(lossy.lifecycle, self.ns_id))
            self.evidence.check(step, 'lost CU request leaves the update uncertain',
                                outcome.get('error') == 'UpdateUncertain', outcome)
            adopted = lifecycle.adopt_lost_request(self.osm.lifecycle, REGISTRY, self.state_path, self.context, policy)
        self.evidence.record(step, 'adopt', adopted)
        self.evidence.check(step, 'no OSM operation found: previous release confirmed',
                            adopted['action'] == 'confirmed-previous-release', adopted)
        after = self.snapshot(step, 'after')
        self.evidence.check(step, 'OSM received no new action', not [
            o for o in self.new_operations(before, after) if o['type'] == 'action'])
        for kdu in RELEASES:
            old, new = (before['helm'][kdu]['status'] or {}).get('version'), (after['helm'][kdu]['status'] or {}).get('version')
            self.evidence.check(step, kdu + ' Helm revision unchanged', old is not None and new == old, [old, new])
        self.evidence.check(step, 'no pending update remains', 'pending_update' not in after['state']['osm'])

    def lcm_restart_recovery(self, step):
        before = self.snapshot(step, 'before')
        pods_before = [p['metadata']['name'] for p in self.cluster.lcm_pods()]
        policy = self.policy(auto_rollback=False)
        restarts = []
        with self.gate() as osm:
            restarting = RestartLcmWhileProcessing(osm, 'cu', self.cluster.restart_lcm, restarts.append)
            outcome = self.run_release(step, 3, policy, restarting)
            pods_after = [p['metadata']['name'] for p in self.cluster.lcm_pods()]
            self.evidence.record(step, 'lcm', {'restarts': restarts, 'pods_before': pods_before, 'pods_after': pods_after})
            self.evidence.check(step, 'LCM restarted while the CU upgrade was PROCESSING',
                                bool(restarts) and set(pods_before) != set(pods_after), restarts)
            cu_operations = [o for o, kind in restarting.operations.items() if kind == ('upgrade', 'cu')]
            final = {o: self.wait_operation(o) for o in cu_operations}
            mid = self.snapshot(step, 'after-restart')
            self.evidence.observe(step, 'P1 outcome when LCM restarts mid-upgrade', outcome)
            self.evidence.observe(step, 'OSM state of the interrupted CU operation', final)
            self.evidence.observe(step, 'Helm state after the LCM restart',
                                  {k: v['status'] for k, v in mid['helm'].items()})
            recovery = []
            pending = mid['state']['osm'].get('pending_update') or {}
            if pending.get('current_kdu') and pending['current_kdu'] not in pending.get('operations', {}):
                recovery.append(('adopt', lifecycle.adopt_lost_request(self.osm.lifecycle, REGISTRY, self.state_path,
                                                                       self.context, policy)))
            if self.read_state()['osm'].get('pending_update'):
                with contextlib.suppress(lifecycle.LifecycleError):
                    recovery.append(('reconcile', self.reconcile_until_settled(step, policy)))
            for kdu, release in RELEASES.items():
                view = self.cluster.release_view(release) or {}
                if str(view.get('state', '')).startswith('pending-'):
                    self.cluster.helm_rollback(release)
                    recovery.append(('helm-rollback', release))
            self.restore_rev2(step, osm)
        if self.read_state()['osm'].get('pending_update'):
            recovery.append(('operator', self.operator_discard_pending(step, 'recovered after LCM restart')))
        self.evidence.record(step, 'recovery', recovery)
        after = self.snapshot(step, 'after')
        for kdu in RELEASES:
            self.expect_release(step, after, kdu, EXPECTED_CHART[2], 'r2', 'recovered after LCM restart')
            self.evidence.check(step, kdu + ' Helm release not left pending', after['helm'][kdu]['status'] is not None
                                and not str(after['helm'][kdu]['status']['state']).startswith('pending-'))
        self.expect_identity(step, after)
        self.evidence.check(step, 'no pending update remains', 'pending_update' not in after['state']['osm'])
        self.evidence.check(step, 'LCM ready again', bool(self.cluster.lcm_pods()))
        self.evidence.observe(step, 'new OSM operations during restart test',
                              [(o['primitive'], o['kdu'], o['state']) for o in self.new_operations(before, after)])

    # -- orchestration ---------------------------------------------------------------------------------------------
    def run(self, steps=STEPS, allow_lcm_restart=False):
        for name in steps:
            if name == 'lcm_restart_recovery' and not allow_lcm_restart:
                self.evidence.steps.append({'step': name, 'result': 'skipped (requires --allow-lcm-restart)'})
                continue
            started = self.clock.time()
            try:
                self.verify_target()
                if name != 'preflight' and self.lcm_digest is None:
                    raise ExperimentError('Preflight has not run')
                getattr(self, name)(name)
            except Exception as error:      # stop at the first broken step; evidence stays
                self.evidence.check(name, 'step completed', False, type(error).__name__ + ': ' + str(error)[:500])
                self.evidence.steps.append({'step': name, 'result': 'error', 'started': started, 'ended': self.clock.time()})
                break
            failed = [c for c in self.evidence.checks if c['step'] == name and c['passed'] is False]
            self.evidence.steps.append({'step': name, 'result': 'failed' if failed else 'passed',
                                        'started': started, 'ended': self.clock.time()})
            if failed:
                break
        return self.evidence.summary({'commit': self.commit, 'ns_id': self.ns_id, 'lcm_digest': self.lcm_digest,
                                      'target': self.config['target_address']})

    def validate_baseline(self):
        """Read-only check that the existing deployment is the clean revision-1 baseline
        `instantiate` produces, i.e. that a later run could continue from it.

        Reads OSM, Kubernetes, Helm and the P1 state file; writes only evidence files.
        """
        step = 'baseline'
        started = self.clock.time()
        try:
            self.verify_target()
            self.discover()
            self.lcm_digest = self.cluster.lcm_image_digest()
            self.evidence.record(step, 'target', {'ids': self.ids, 'lcm_digest': self.lcm_digest, 'commit': self.commit,
                                                  'gate_committed': lifecycle.P4_KDU_UPGRADE_APPROVAL})
            ns = self.experiment_ns()
            self.ns_id, self.ns_count = ns['_id'], len(self.osm.ns_instances())
            self.evidence.check(step, 'committed P4 gate is closed', lifecycle.P4_KDU_UPGRADE_APPROVAL is None)
            knf, nsd = self.osm.lifecycle.package('knf', VNFD), self.osm.lifecycle.package('ns', NSD)
            self.evidence.check(step, 'catalog holds revision 1 of the experiment KNF and NSD',
                                bool(knf and nsd) and knf['revision'] == 1 and nsd['revision'] == 1, [knf, nsd])
            deployed = ns['_admin']['deployed']['K8s']
            self.evidence.check(step, 'KDUs deployed under member-vnf-index ' + MEMBER_INDEX,
                                all(d.get('member-vnf-index') == MEMBER_INDEX for d in deployed))
            models = {d['kdu-name']: d.get('kdu-model') for d in deployed}
            self.evidence.check(step, 'KDU models are revision-pinned storage paths of revision 1', bool(knf) and all(
                (m := lifecycle.KDU_MODEL.fullmatch(str(models[k]))) and m['id'] == knf['id'] and m['rev'] == '1'
                and m['package'] == VNFD and m['kdu'] == k for k in RELEASES), models)
            snap = self.snapshot(step, 'baseline')
            for kdu, release in RELEASES.items():
                self.expect_release(step, snap, kdu, EXPECTED_CHART[1], 'r1', 'baseline')
                helm = snap['helm'][kdu]
                # A consistent view at revision 1 has exactly one history entry.
                self.evidence.check(step, 'Helm release ' + release + ' at revision 1',
                                    helm['status'] is not None and helm['status']['version'] == 1, helm)
            self.expect_identity(step, snap)
            self.evidence.check(step, 'no OSM operation other than the completed instantiation',
                                [(o['type'], o['state']) for o in snap['operations']] == [('instantiate', 'COMPLETED')],
                                snap['operations'])
            inventory = self.cluster.inventory()
            foreign = foreign_objects(inventory)
            self.evidence.check(step, 'namespace holds only experiment objects', not foreign,
                                {'foreign': foreign, 'inventory': inventory})
            # The runner's P1 state is written at instantiation (active NS, context) and gains
            # active_release only after an upgrade, so a revision-1 baseline must not have one.
            # A missing file does not invalidate the deployment (nothing in OSM, Kubernetes or
            # Helm depends on it): it is recorded, and would be re-established before any upgrade.
            try:
                self.context = self.runtime_context()
                if not self.state_path.exists():
                    self.evidence.observe(step, 'P1 state not present (re-established before any upgrade)',
                                          str(self.state_path))
                else:
                    state = self.read_state()
                    osm = state['osm']
                    self.evidence.check(step, 'P1 state names this NS as active with nothing pending',
                                        state.get('active') == SCENARIO and osm.get('active_instance_id') == self.ns_id
                                        and not any(k.startswith('pending_') for k in osm)
                                        and 'active_release' not in osm, state)
            except (lifecycle.LifecycleError, ValueError) as error:
                self.evidence.check(step, 'P1 state names this NS as active with nothing pending', False,
                                    type(error).__name__ + ': ' + str(error))
        except Exception as error:          # read-only: report, never repair
            self.evidence.check(step, 'baseline readable', False, type(error).__name__ + ': ' + str(error)[:500])
        failed = [c for c in self.evidence.checks if c['passed'] is False]
        self.evidence.steps.append({'step': step, 'result': 'failed' if failed else 'passed',
                                    'started': started, 'ended': self.clock.time()})
        return self.evidence.summary({'commit': self.commit, 'ns_id': self.ns_id, 'lcm_digest': self.lcm_digest,
                                      'target': self.config['target_address'], 'read_only': True})

    # -- teardown ----------------------------------------------------------------------------------------------
    # (member-vnf-index, KDU, Helm release, Kubernetes namespace) of the experiment's KDUs
    ISOLATED_KDUS = sorted((MEMBER_INDEX, kdu, release, NAMESPACE) for kdu, release in RELEASES.items())

    def other_ns_states(self, experiment_id):
        """Identity and state of every OSM NS except the experiment NS (G4 witness)."""
        return sorted([str(n.get('_id')), n.get('name', n.get('nsName')), n.get('nsState'), n.get('currentOperation'),
                       n.get('operational-status'), n.get('config-status')]
                      for n in self.osm.ns_instances() if n.get('_id') != experiment_id)

    def verified_teardown_ns(self, ns_id, nsd):
        """Prove from OSM records, not the name, that ns_id is the experiment NS (G1).

        Requires: the NS (by id) is named NS_NAME; OSM recorded exactly one instantiate
        operation for it, requesting this NSD package with only the isolated CU/DU KDUs
        (member index, KDU name, Kubernetes namespace, release name); while instantiated,
        its NSD reference is that package, its deployed KDUs are exactly the isolated ones and
        every VNFR uses the experiment KNF; once terminated, nothing remains deployed.
        """
        ns = self.osm.lifecycle.get_ns(ns_id)
        problems = []
        if ns.get('name', ns.get('nsName')) != NS_NAME:
            problems.append('name')
        if not nsd:
            problems.append('experiment NSD package missing')
        instantiations = [o for o in self.osm.lifecycle.ns_operations(ns_id) if o.get('lcmOperationType') == 'instantiate']
        params = (instantiations[0].get('operationParams') or {}) if instantiations else {}
        requested = sorted((v.get('member-vnf-index'), k.get('kdu_name'), k.get('kdu-deployment-name'),
                            k.get('k8s-namespace'))
                           for v in params.get('additionalParamsForVnf') or [] for k in v.get('additionalParamsForKdu') or [])
        if len(instantiations) != 1 or params.get('nsName') != NS_NAME or not nsd or \
                params.get('nsdId') != nsd['id'] or requested != self.ISOLATED_KDUS:
            problems.append('instantiate operation is not the isolated experiment request')
        deployed = sorted((k.get('member-vnf-index'), k.get('kdu-name'), k.get('kdu-instance'), k.get('namespace'))
                          for k in (ns.get('_admin') or {}).get('deployed', {}).get('K8s') or [])
        if ns.get('nsState') == 'NOT_INSTANTIATED':
            if deployed:
                problems.append('terminated NS still lists deployed KDUs')
        else:
            knf = self.osm.lifecycle.package('knf', VNFD)
            vnfrs = self.osm.lifecycle.vnf_instances(ns_id)
            if not nsd or ns.get('nsd-id') != nsd['id']:
                problems.append('NSD reference')
            if deployed != self.ISOLATED_KDUS:
                problems.append('deployed KDUs')
            if not knf or not vnfrs or any(v.get('vnfd-id') != knf['id'] for v in vnfrs):
                problems.append('VNFR descriptors')
        identity = {'ns_id': ns_id, 'nsState': ns.get('nsState'), 'nsd_id': ns.get('nsd-id'), 'deployed': deployed,
                    'instantiate_operations': [o.get('_id') for o in instantiations], 'requested': requested,
                    'problems': problems}
        if problems:
            raise TargetError('OSM NS ' + ns_id + ' is not provably the experiment NS: ' + ', '.join(problems))
        return ns, identity

    def wait_terminated(self, ns_id, nsd):
        """G2: terminated NS idle with nothing deployed, no running operation, no P4 Helm release."""
        deadline = self.clock.monotonic() + self.timeouts['op_timeout']
        while True:
            ns, identity = self.verified_teardown_ns(ns_id, nsd)
            running = [o.get('_id') for o in self.osm.lifecycle.ns_operations(ns_id)
                       if o.get('operationState') not in TERMINAL]
            releases = [r.get('name') for r in self.cluster.releases()] if self.cluster.namespace() else []
            safe = (ns.get('nsState') == 'NOT_INSTANTIATED' and ns.get('currentOperation') in ('IDLE', None) and
                    not running and not releases)
            if safe or self.clock.monotonic() >= deadline:
                return safe, dict(identity, currentOperation=ns.get('currentOperation'), running=running,
                                  releases=releases)
            self.clock.sleep(self.timeouts['poll'])

    def teardown(self):
        """Remove only experiment resources: NS, descriptors, Helm releases, namespace.

        Nothing is touched unless the target is the Dell, the namespace holds only P4
        objects and the NS is provably the experiment NS (G1, G3). The NS record and the
        packages are deleted only after a COMPLETED termination left nothing running or
        deployed (G2). Every other OSM NS must be unchanged afterwards (G4).
        """
        step = 'teardown'
        others_before = experiment_id = None
        try:
            self.verify_target()
            nsd = self.osm.lifecycle.package('ns', NSD)
            ns = self.osm.experiment_ns()
            experiment_id = ns and ns['_id']
            others_before = self.other_ns_states(experiment_id)
            self.evidence.record(step, 'other-ns-before', others_before)
            if self.cluster.namespace():
                inventory = self.cluster.inventory()
                self.evidence.record(step, 'namespace-before-teardown', inventory)
                foreign = foreign_objects(inventory)
                if foreign:
                    raise ExperimentError('Namespace holds non-experiment resources; nothing removed: ' + str(foreign))
            if ns:
                self.ns_id = ns['_id']
                ns, identity = self.verified_teardown_ns(ns['_id'], nsd)
                self.evidence.record(step, 'ns-identity', identity)
                if ns.get('nsState') != 'NOT_INSTANTIATED':
                    if ns.get('currentOperation') not in ('IDLE', None):
                        raise ExperimentError('Experiment NS has an operation in progress; not terminating')
                    state = self.wait_operation(self.osm.terminate(ns['_id']))
                    if not self.evidence.check(step, 'experiment NS terminated', state == 'COMPLETED', state):
                        raise ExperimentError('Termination not COMPLETED (' + str(state) + '); NS and packages kept')
                safe, detail = self.wait_terminated(ns['_id'], nsd)
                self.evidence.record(step, 'after-termination', detail)
                if not self.evidence.check(step, 'terminated NS idle with nothing deployed or running', safe, detail):
                    raise ExperimentError('Terminated NS not in a safe state; NS and packages kept')
                self.osm.delete_ns(ns['_id'])
                if self.osm.experiment_ns() is not None:
                    raise ExperimentError('Experiment NS record still present; packages kept')
            users = [n.get('_id') for n in self.osm.ns_instances() if nsd and n.get('nsd-id') == nsd['id']]
            if users:
                raise ExperimentError('Experiment NSD still referenced by NS ' + str(users) + '; packages kept')
            for kind, name in (('ns', NSD), ('knf', VNFD)):
                self.evidence.record(step, 'deleted-' + name, self.osm.delete_package(kind, name))
            deadline = self.clock.monotonic() + self.timeouts['health_timeout']
            # After the uninstall, pods of deleted ReplicaSets may still be terminating; wait for
            # them too, so only objects that stay are judged foreign.
            while self.cluster.namespace() and (self.cluster.releases() or foreign_objects(self.cluster.inventory())) \
                    and self.clock.monotonic() < deadline:
                self.clock.sleep(self.timeouts['poll'])
            if self.cluster.namespace():
                releases = self.cluster.releases()
                inventory = self.cluster.inventory()
                self.evidence.record(step, 'namespace-before-delete', {'releases': releases, 'inventory': inventory})
                foreign = foreign_objects(inventory)
                if releases or foreign:
                    raise ExperimentError('Namespace holds non-experiment resources; not deleting it: '
                                          + str(foreign or releases))
                self.cluster.delete_namespace()
            clean = (self.osm.experiment_ns() is None and self.osm.lifecycle.package('knf', VNFD) is None and
                     self.osm.lifecycle.package('ns', NSD) is None and self.cluster.namespace() is None)
            self.evidence.check(step, 'experiment resources removed', clean)
        except Exception as error:          # stop; whatever was not yet removed stays for inspection
            self.evidence.check(step, 'teardown completed', False, type(error).__name__ + ': ' + str(error)[:500])
        if others_before is not None:
            others_after = self.other_ns_states(experiment_id)
            self.evidence.record(step, 'other-ns-after', others_after)
            self.evidence.check(step, 'every other OSM NS unchanged', others_after == others_before,
                                {'before': others_before, 'after': others_after})
        return self.evidence.summary({'teardown': True})


def git_commit():
    try:
        value = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                               timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        value = ''
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ExperimentError('Cannot determine the experiment source commit')
    return value


def connect(config):
    catalog = Catalog(config['osm_url'], ca_file=config.get('osm_ca_file'))
    catalog.authenticate(config['osm_user'], read_secret(config['osm_password_file']), config['osm_project'])
    return catalog.request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('build-packages', 'preflight', 'validate-baseline', 'run', 'teardown'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, help='build-packages: output directory')
    parser.add_argument('--allow-lcm-restart', action='store_true',
                        help='run the LCM restart test (restarts OSM LCM on the experiment target)')
    args = parser.parse_args(argv)
    if args.command == 'build-packages':
        print(json.dumps(p4_packages.write_all(args.output), indent=2, sort_keys=True))
        return 0
    if not args.config:
        parser.error('--config is required')
    config = load_config(args.config)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    evidence = Evidence(Path(config['work_dir']) / 'evidence' / (args.command + '-' + stamp))
    cluster = Cluster(config['kubectl'], config['helm'], config['kubeconfig'], config['osm_namespace'])
    experiment = Experiment(config, connect(config), cluster, evidence)
    if args.command == 'teardown':
        summary = experiment.teardown()
    elif args.command == 'validate-baseline':
        summary = experiment.validate_baseline()
    else:
        summary = experiment.run(('preflight',) if args.command == 'preflight' else STEPS, args.allow_lcm_restart)
    print(json.dumps({k: summary[k] for k in ('steps', 'passed', 'failed', 'observations', 'summary_sha256')
                      if k in summary}, indent=2, default=str))
    print('evidence: ' + str(evidence.directory))
    return 0 if summary['failed'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
