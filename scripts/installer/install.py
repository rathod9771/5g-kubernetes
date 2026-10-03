#!/usr/bin/env python3
"""Explicit target-machine installer. Static imports never perform lifecycle work."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from installer.reference import ROOT, versions, adapt_osm_chart, osm_values, api_ingress, apply_osm_source_patch
from installer.reference import management_credentials, validate_management_mount
from installer.private_input import private_archive, import_decision, restore_command
from local_safety import atomic_write, exclusive_lock
from runtime_config import load_config, write_snapshot, discover_context, ConfigError, read_snapshot
from runtime_render import render_service, service_configuration, check_installed_service, render_manifest
from osm_catalog import Catalog, CatalogError, AuthorizationError, parse_response, identifier
from osm_packages import prepare, validated_snapshot, DEFAULT_OUTPUT
from scenario_registry import load_registry, select_scenarios
from osm_onboard import onboard
from state_schema import validate_state


# Summaries are fixed public phrases, never raw stderr (which can contain secrets).
NETWORK_ERRORS = ('connection reset by peer', 'connection refused', 'temporary failure in name resolution',
                  'no such host', 'network is unreachable', 'i/o timeout', 'context deadline exceeded',
                  'tls handshake timeout', 'unexpected eof', 'service unavailable', 'bad gateway',
                  'gateway timeout', 'could not resolve host', 'failed to connect', 'early eof')
DETERMINISTIC_ERRORS = ('failed to parse', 'parse error', 'yaml parse', 'execution error',
                        'unknown flag', 'not found', 'unauthorized', 'forbidden', 'x509:',
                        'certificate verify failed', 'invalid value')


def stderr_summary(stderr):
    text = (stderr or b'').decode('utf-8', errors='replace').lower()
    for phrase in DETERMINISTIC_ERRORS:
        if phrase in text:
            return phrase, False
    for phrase in NETWORK_ERRORS:
        if phrase in text:
            return phrase, True
    return 'unclassified failure (private stderr suppressed)', False


class Runner:
    stage = 'installer'

    def run(self, args, data=None, check=True, timeout=1800, context=None, remote_fetch=False, fetch_timeout=120):
        # Only explicitly classified read/fetch operations may retry. Never retry
        # installation/lifecycle commands, whose side effects could be ambiguous.
        attempts = 4 if remote_fetch and check else 1
        label = self.stage + ': ' + (context or str(args[0]))
        for attempt in range(attempts):
            try:
                result = subprocess.run([str(a) for a in args], input=data, capture_output=True,
                                        timeout=min(timeout, fetch_timeout) if remote_fetch else timeout)
            except subprocess.TimeoutExpired:
                if remote_fetch and attempt + 1 < attempts:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ConfigError(label + ': failed after ' + str(attempt + 1) +
                                  ' attempt(s); final stderr summary: command timeout (private output suppressed)') from None
            except OSError:
                raise ConfigError(label + ': unable to execute command; check executable availability and permissions') from None
            if not check or not result.returncode:
                return result
            summary, transient = stderr_summary(result.stderr)
            if remote_fetch and transient and attempt + 1 < attempts:
                time.sleep(2 ** (attempt + 1))
                continue
            raise ConfigError(label + ': failed after ' + str(attempt + 1) +
                              ' attempt(s); final stderr summary: ' + summary +
                              '; inspect target status and rerun')

    def json(self, args):
        try:
            return json.loads(self.run(args).stdout)
        except ValueError:
            raise ConfigError(self.stage + ': invalid structured response from ' + str(args[0])) from None


class Installer:
    def __init__(self, cfg, runner=None):
        self.cfg, self.runner = dict(cfg), runner or Runner()
        self.lock = versions()
        self.directory = Path(cfg['RUNTIME_DIR'])
        self.registry = load_registry()

    def kubectl(self, *args, workload=False, data=None, check=True):
        key = 'OSM_KUBECONFIG_PATH' if workload else 'KUBECONFIG_PATH'
        return self.runner.run(['kubectl', '--kubeconfig', self.cfg[key], *args], data=data, check=check)

    def kjson(self, *args, workload=False):
        return json.loads(self.kubectl(*args, '-o', 'json', workload=workload).stdout)

    def apply(self, document, workload=False):
        return self.kubectl('apply', '-f', '-', workload=workload,
                            data=yaml.safe_dump_all(document if isinstance(document, list) else [document]).encode())

    def snapshot(self):
        path = self.directory / 'installer-config.json'
        write_snapshot(path, self.cfg)
        os.environ['P0_RUNTIME_CONFIG'] = str(path)

    def stage(self, name, function):
        print('Installing/verifying ' + name, flush=True)
        self.runner.stage = name
        function()
        print('READY: ' + name, flush=True)

    def helm(self, *args, workload=False, context=None):
        key = 'OSM_KUBECONFIG_PATH' if workload else 'KUBECONFIG_PATH'
        remote = args[0] == 'pull' or (args[0] == 'show' and '--repo' in args) or args[:2] in (('repo', 'add'), ('repo', 'update'), ('dependency', 'build'), ('dependency', 'update'))
        return self.runner.run(['helm', '--kubeconfig', self.cfg[key], *args],
                               context=context or 'Helm ' + ' '.join(str(a) for a in args[:2]), remote_fetch=remote)

    def release(self, name, namespace, chart, version, values=None, repo=None, wait=True):
        self.apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace}})
        installed = json.loads(self.helm('list', '-n', namespace, '-a', '-o', 'json').stdout)
        existing = [r for r in installed if r['name'] == name]
        if existing:
            if existing[0]['status'] != 'deployed' or not existing[0]['chart'].endswith('-' + version):
                raise ConfigError(name + ': existing release is failed or has another version; explicit recovery required (no automatic upgrade/uninstall)')
        else:
            args = ['upgrade', '--install', name, chart, '-n', namespace,
                    '--version', version, '--timeout', '20m']
            if wait:
                args += ['--wait']
            fetch_directory = None
            if repo:
                # Complete remote fetching before any release creation. Installing
                # a verified local chart avoids retrying a mutating Helm operation.
                import tempfile
                fetch_directory = tempfile.TemporaryDirectory(prefix='installer-chart-')
                try:
                    self.helm('pull', chart, '--repo', repo, '--version', version,
                              '--destination', fetch_directory.name, context=name + ' chart ' + chart + '/' + version)
                    archives = list(Path(fetch_directory.name).glob('*.tgz'))
                    if len(archives) != 1:
                        raise ConfigError(name + ': chart fetch did not produce exactly one archive')
                    args[3] = str(archives[0])
                except Exception:
                    fetch_directory.cleanup()
                    raise
            if values:
                path = self.directory / (name + '-values.yaml')
                atomic_write(path, yaml.safe_dump(values).encode())
                args += ['-f', str(path)]
            try:
                self.helm(*args, context=name + ' chart ' + chart + '/' + version)
            finally:
                if fetch_directory is not None:
                    fetch_directory.cleanup()
        for kind in ('deployment', 'statefulset', 'daemonset'):
            items = self.kjson('get', kind, '-n', namespace)['items']
            for item in items:
                self.kubectl('rollout', 'status', kind + '/' + item['metadata']['name'], '-n', namespace, '--timeout=600s')

    def kubernetes(self):
        path = Path(self.cfg['KUBECONFIG_PATH'])
        reachable = self.kubectl('get', 'nodes', '-o', 'json', check=False)
        if reachable.returncode and path.exists():
            raise ConfigError('Existing kubeconfig cannot reach the cluster; do not overwrite it or reinitialize the cluster')
        if reachable.returncode:
            admin = Path('/etc/kubernetes/admin.conf')
            if not admin.exists():
                self.runner.run(['sudo', 'mkdir', '-p', '/etc/systemd/system/kubelet.service.d'])
                unit = '[Service]\nEnvironment="KUBELET_EXTRA_ARGS=--max-pods=' + self.cfg['KUBELET_MAX_PODS'] + '"\n'
                self.runner.run(['sudo', 'tee', '/etc/systemd/system/kubelet.service.d/20-max-pods.conf'], data=unit.encode())
                self.runner.run(['sudo', 'systemctl', 'daemon-reload'])
                self.runner.run(['sudo', 'kubeadm', 'init', '--kubernetes-version', 'v' + self.lock['kubernetes'],
                                 '--apiserver-advertise-address', self.cfg['HOST_IP'],
                                 '--pod-network-cidr', self.cfg['POD_CIDR'], '--service-cidr', self.cfg['SERVICE_CIDR']])
            if path.exists():
                raise ConfigError('Existing kubeconfig cannot reach the cluster; do not overwrite it or reinitialize the cluster')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.runner.run(['sudo', 'cp', str(admin), str(path)])
            self.runner.run(['sudo', 'chown', str(os.getuid()) + ':' + str(os.getgid()), str(path)])
            path.chmod(0o600)
        nodes = self.kjson('get', 'nodes')['items']
        if len(nodes) != 1 or any(n['status']['nodeInfo']['kubeletVersion'] != 'v' + self.lock['kubernetes'] for n in nodes):
            raise ConfigError('Submission requires a single-node Kubernetes ' + self.lock['kubernetes'] + ' cluster; no automatic cluster upgrades')
        cni = self.kubectl('get', 'daemonset', 'kube-flannel-ds', '-n', 'kube-flannel', check=False)
        if cni.returncode:
            self.kubectl('apply', '-f', 'https://github.com/flannel-io/flannel/releases/download/v' + self.lock['flannel'] + '/kube-flannel.yml')
        flannel = self.kjson('get', 'daemonset', 'kube-flannel-ds', '-n', 'kube-flannel')
        images = [c['image'].split('@')[0] for c in flannel['spec']['template']['spec']['containers']]
        if not any(i.endswith(':v' + self.lock['flannel']) for i in images):
            raise ConfigError('Existing Flannel version differs from reference; explicit reconciliation required')
        self.kubectl('rollout', 'status', 'daemonset/kube-flannel-ds', '-n', 'kube-flannel', '--timeout=600s')
        for node in nodes:
            taints = node.get('spec', {}).get('taints', [])
            if any(t['key'] == 'node-role.kubernetes.io/control-plane' for t in taints):
                self.kubectl('taint', 'node', node['metadata']['name'], 'node-role.kubernetes.io/control-plane-')
        self.kubectl('wait', 'nodes', '--all', '--for=condition=Ready', '--timeout=600s')

    def infrastructure(self):
        self.release('longhorn', 'longhorn-system', 'longhorn', self.lock['longhorn'],
                     {'persistence': {'defaultClassReplicaCount': 1}}, 'https://charts.longhorn.io')
        self.kubectl('get', 'storageclass', 'longhorn')
        if self.kubectl('get', 'crd', 'certificates.cert-manager.io', check=False).returncode:
            self.kubectl('apply', '-f', 'https://github.com/cert-manager/cert-manager/releases/download/v' + self.lock['cert_manager'] + '/cert-manager.yaml')
        for name in ('cert-manager', 'cert-manager-cainjector', 'cert-manager-webhook'):
            deployment = self.kjson('get', 'deployment', name, '-n', 'cert-manager')
            image = deployment['spec']['template']['spec']['containers'][0]['image'].split('@')[0]
            if not image.endswith(':v' + self.lock['cert_manager']):
                raise ConfigError('Existing cert-manager version differs from reference; explicit reconciliation required')
        self.kubectl('rollout', 'status', 'deployment', '-n', 'cert-manager', '--timeout=600s')
        self.release('ingress-nginx', 'ingress-nginx', 'ingress-nginx', self.lock['ingress_nginx_chart'],
                     {'controller': {'service': {'type': 'NodePort', 'nodePorts': {'http': int(self.cfg['INGRESS_HTTP_NODEPORT']), 'https': int(self.cfg['OSM_HTTPS_PORT'])}}}},
                     'https://kubernetes.github.io/ingress-nginx')
        self.release('rancher', 'cattle-system', 'rancher', self.lock['rancher'],
                     {'hostname': 'rancher.' + self.cfg['OSM_BASE_DOMAIN'], 'replicas': 1,
                      'bootstrapPassword': self.cfg['RANCHER_BOOTSTRAP_PASSWORD']},
                     'https://releases.rancher.com/server-charts/latest')
        self.release('istio-base', 'istio-system', 'base', self.lock['istio'],
                     {'defaultRevision': 'default'}, 'https://istio-release.storage.googleapis.com/charts')
        self.release('istiod', 'istio-system', 'istiod', self.lock['istio'],
                     repo='https://istio-release.storage.googleapis.com/charts')

    def acquire_osm_source(self):
        source = self.directory / 'osm-devops'
        commit, url = self.lock['osm_source_commit'], self.lock['osm_source_url']
        if source.is_symlink():
            raise ConfigError('OSM source checkout must not be a symlink')
        def git(path, *args, check=True, context='OSM source validation'):
            return self.runner.run(['git', '-C', str(path), *args], check=check, context=context)
        def valid_repository(path):
            # Reject linked worktrees and arbitrary directories: only a self-contained
            # cache with the locked origin is eligible for automatic recovery.
            if not (path / '.git').is_dir() or (path / '.git').is_symlink():
                raise ConfigError('OSM invalid partial cache: expected a self-contained Git repository; preserve and inspect it')
            top = git(path, 'rev-parse', '--show-toplevel', check=False)
            origin = git(path, 'remote', 'get-url', 'origin', check=False)
            if top.returncode or Path(os.fsdecode(top.stdout).strip()).resolve() != path.resolve() or origin.returncode or os.fsdecode(origin.stdout).strip() != url:
                raise ConfigError('OSM invalid partial cache: repository/source identity mismatch; preserve and inspect it')
        def has_commit(path):
            return not git(path, 'cat-file', '-e', commit + '^{commit}', check=False).returncode
        partial = False
        if source.exists():
            valid_repository(source)
            partial = {p.name for p in source.iterdir()} == {'.git'}
            if not partial:
                if git(source, 'status', '--porcelain').stdout:
                    raise ConfigError('OSM download tree has local modifications; refuse to use it')
                if has_commit(source) and os.fsdecode(git(source, 'rev-parse', 'HEAD').stdout).strip() == commit:
                    return source
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.osm-acquisition-', dir=self.directory) as temporary:
            root = Path(temporary)
            candidate = root / 'checkout'
            if source.exists() and not partial:
                shutil.copytree(source, candidate, symlinks=True)
            else:
                # Each retry gets an empty destination, even after Git leaves .git.
                for attempt in range(4):
                    if candidate.exists():
                        shutil.rmtree(candidate)
                    try:
                        self.runner.run(['git', 'clone', '--no-checkout', url, str(candidate)],
                                        timeout=300, context='OSM source clone (remote Git; timeout 300s)')
                        break
                    except ConfigError as error:
                        message = str(error)
                        transient = 'command timeout' in message or any(phrase in message for phrase in NETWORK_ERRORS)
                        if not transient or attempt == 3:
                            raise ConfigError(message + '; clone acquisition attempt ' + str(attempt + 1) + '/4') from None
                        time.sleep(2 ** (attempt + 1))
            valid_repository(candidate)
            if not has_commit(candidate):
                self.runner.run(['git', '-C', str(candidate), 'fetch', 'origin', commit],
                                remote_fetch=True, fetch_timeout=300, timeout=300,
                                context='OSM pinned commit unavailable: remote Git fetch')
            if not has_commit(candidate):
                raise ConfigError('OSM pinned commit unavailable after source acquisition')
            git(candidate, 'checkout', '--detach', commit, context='OSM source checkout failure (pinned commit)')
            if os.fsdecode(git(candidate, 'rev-parse', 'HEAD').stdout).strip() != commit or git(candidate, 'status', '--porcelain').stdout:
                raise ConfigError('OSM source checkout failure: revision or clean working tree verification failed')
            previous = root / 'previous'
            if source.exists():
                os.replace(source, previous)
            try:
                os.replace(candidate, source)
            except BaseException:
                if previous.exists():
                    os.replace(previous, source)
                raise
        return source

    def osm_source(self):
        source = self.acquire_osm_source()
        chart = self.directory / 'osm-chart'
        if chart.is_symlink():
            raise ConfigError('Generated OSM chart directory must not be a symlink')
        if chart.exists():
            # Generated build copy only. Upstream checkout and persistent data are untouched.
            shutil.rmtree(chart)
        with tempfile.TemporaryDirectory(prefix='.osm-reference-', dir=self.directory) as temporary:
            reconstructed = Path(temporary) / 'source'
            shutil.copytree(source, reconstructed, symlinks=True)
            apply_osm_source_patch(reconstructed, self.lock, self.runner)
            shutil.copytree(reconstructed / 'installers/helm/osm', chart)
        if yaml.safe_load((chart / 'Chart.yaml').read_text())['version'] != self.lock['osm']:
            raise ConfigError('Downloaded OSM chart version mismatch')
        adapt_osm_chart(chart)
        for name, url in [('bitnami', 'https://charts.bitnami.com/bitnami'),
                          ('grafana', 'https://grafana.github.io/helm-charts'),
                          ('prometheus-community', 'https://prometheus-community.github.io/helm-charts'),
                          ('apache-airflow', 'https://airflow.apache.org')]:
            self.helm('repo', 'add', name, url, '--force-update', context='OSM 19.0.0 dependency repository ' + name)
        self.helm('dependency', 'build', str(chart), context='OSM chart ' + self.lock['osm'] + ' pinned dependencies')
        for dependency in self.lock['osm_dependencies']:
            archive = chart / 'charts' / (dependency['name'] + '-' + dependency['version'] + '.tgz')
            if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != dependency['sha256']:
                raise ConfigError('OSM dependency bytes differ from reference lock: ' + dependency['name'])
        self.helm('lint', str(chart), '--strict')
        return chart

    def authenticate_ready(self, password):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                self.catalog.authenticate(self.cfg['OSM_USER'], password, 'admin')
                return
            except AuthorizationError:
                raise
            except CatalogError:
                time.sleep(5)
        raise ConfigError('OSM API/TLS readiness timed out; inspect target ingress/NBI/certificates')

    def prepare_management_credentials(self):
        namespace = self.cfg['OSM_NAMESPACE']
        metadata = {'namespace': namespace, 'labels': {'app.kubernetes.io/managed-by': 'reference-installer'}}
        account = 'osm-lcm-management'
        self.apply([
            {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace}},
            {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': dict(metadata, name=account)},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': dict(metadata, name=account),
             'rules': [{'apiGroups': [''], 'resources': ['pods', 'services', 'configmaps'], 'verbs': ['get', 'list', 'watch']},
                       {'apiGroups': ['apps'], 'resources': ['deployments', 'statefulsets'], 'verbs': ['get', 'list', 'watch']}]},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': dict(metadata, name=account),
             'subjects': [{'kind': 'ServiceAccount', 'name': account, 'namespace': namespace}],
             'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': account}},
            {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'kubernetes.io/service-account-token',
             'metadata': dict(metadata, name=account + '-token', annotations={'kubernetes.io/service-account.name': account})}])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            token = self.kjson('get', 'secret', account + '-token', '-n', namespace)
            if token.get('data', {}).get('token') and token.get('data', {}).get('ca.crt'):
                document = management_credentials(namespace, token['data'])
                # Server-side apply avoids duplicating private data in last-applied annotations.
                self.kubectl('apply', '--server-side', '--field-manager=reference-installer', '-f', '-',
                             data=yaml.safe_dump(document).encode())
                return
            time.sleep(2)
        raise ConfigError('OSM management service-account token provisioning failed; kubeconfig not created')

    def validate_lcm_management(self, chart=None):
        namespace = self.cfg['OSM_NAMESPACE']
        keys = self.kubectl('get', 'secret', 'mgmtcluster-secret', '-n', namespace,
                            '-o', 'go-template={{range $key,$value := .data}}{{$key}}{{"\\n"}}{{end}}', check=False)
        if keys.returncode or 'kubeconfig' not in keys.stdout.decode().splitlines():
            raise ConfigError('OSM LCM management kubeconfig missing: mgmtcluster-secret/kubeconfig')
        deployment = self.kjson('get', 'deployment', 'lcm', '-n', namespace)
        spec = deployment.get('spec', {}).get('template', {}).get('spec', {})
        if chart is not None and not any(v.get('name') == 'mgmtcluster-kubeconfig' for v in spec.get('volumes', [])):
            # Repair the already-installed pinned release without changing its values.
            self.helm('upgrade', 'osm', str(chart), '-n', namespace, '--reuse-values', '--timeout', '20m',
                      context='OSM LCM management kubeconfig mount reconciliation')
            deployment = self.kjson('get', 'deployment', 'lcm', '-n', namespace)
        validate_management_mount(deployment, keys.stdout.decode().splitlines())

    def reconcile_lcm_gitops(self, chart):
        namespace = self.cfg['OSM_NAMESPACE']
        desired = osm_values(self.cfg, self.lock)['global']['gitops']
        def matches():
            current = json.loads(self.helm('get', 'values', 'osm', '-n', namespace, '-o', 'json').stdout) or {}
            configured = current.get('global', {}).get('gitops', {})
            if any(configured.get(k) != v for k, v in desired.items()):
                return False
            deployment = self.kjson('get', 'deployment', 'lcm', '-n', namespace)
            containers = deployment.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
            if not any(c.get('name') == 'lcm' and any(e.get('secretRef', {}).get('name') == 'osm-gitops-secret' for e in c.get('envFrom', [])) for c in containers):
                return False
            keys = self.kubectl('get', 'secret', 'osm-gitops-secret', '-n', namespace,
                                '-o', 'go-template={{range $key,$value := .data}}{{$key}}{{"\\n"}}{{end}}', check=False)
            return not keys.returncode and {'OSM_GITOPS_GIT_BASE_URL', 'OSM_GITOPS_FLEET_REPO_URL', 'OSM_GITOPS_SW_CATALOGS_REPO_URL'} <= set(keys.stdout.decode().splitlines())
        if matches():
            return
        path = self.directory / 'osm-gitops-values.yaml'
        atomic_write(path, yaml.safe_dump({'global': {'gitops': desired}}).encode())
        self.helm('upgrade', 'osm', str(chart), '-n', namespace, '--reuse-values', '-f', str(path), '--timeout', '20m',
                  context='OSM reference GitOps startup configuration reconciliation')
        if not matches():
            raise ConfigError('OSM LCM reference GitOps configuration missing after reconciliation; readiness refused')

    def osm(self):
        chart = self.osm_source()
        self.prepare_management_credentials()
        self.release('osm', self.cfg['OSM_NAMESPACE'], str(chart), self.lock['osm'], osm_values(self.cfg, self.lock), wait=False)
        self.reconcile_lcm_gitops(chart)
        self.validate_lcm_management(chart)
        self.kubectl('wait', 'certificate', '--all', '-n', self.cfg['OSM_NAMESPACE'], '--for=condition=Ready', '--timeout=600s')
        self.apply(api_ingress(self.cfg))
        ca = self.kjson('get', 'secret', 'osm-ca', '-n', self.cfg['OSM_NAMESPACE'])['data']['tls.crt']
        certificate = base64.b64decode(ca, validate=True)
        ca_path = self.directory / 'osm-ca.crt'
        atomic_write(ca_path, certificate)
        self.cfg['OSM_CA_CERT_PATH'] = str(ca_path)
        self.snapshot()
        self.catalog = Catalog(self.cfg['OSM_HOST'], ca_file=str(ca_path))
        try:
            self.authenticate_ready(self.cfg['OSM_PASSWORD'])
        except AuthorizationError:
            if not self.cfg['OSM_BOOTSTRAP_PASSWORD']:
                raise ConfigError('Set private OSM_BOOTSTRAP_PASSWORD for the initial upstream account; it will be changed to OSM_PASSWORD') from None
            self.authenticate_ready(self.cfg['OSM_BOOTSTRAP_PASSWORD'])
            users = parse_response(self.catalog.request('GET', '/admin/v1/users'))
            matches = [u for u in users if (u.get('username') or u.get('name')) == self.cfg['OSM_USER']]
            if len(matches) != 1:
                raise ConfigError('Initial OSM account is missing or ambiguous')
            self.catalog.request('PATCH', '/admin/v1/users/' + quote(matches[0]['_id'], safe=''),
                                 yaml.safe_dump({'password': self.cfg['OSM_PASSWORD']}).encode())
            self.authenticate_ready(self.cfg['OSM_PASSWORD'])
        self.registration()

    def entries(self, path):
        result = parse_response(self.catalog.request('GET', path))
        if not isinstance(result, list):
            raise ConfigError('Invalid OSM catalog listing')
        return result

    def ensure_registration(self, path, name, document):
        matches = [e for e in self.entries(path) if e.get('name') == name]
        if len(matches) > 1:
            raise ConfigError('Ambiguous OSM registration: ' + name)
        if not matches:
            self.catalog.request('POST', path, yaml.safe_dump(document).encode())
            matches = [e for e in self.entries(path) if e.get('name') == name]
        if len(matches) != 1:
            raise ConfigError('OSM registration did not become visible: ' + name)
        return matches[0]

    def registration(self):
        self.validate_lcm_management()
        project = self.ensure_registration('/admin/v1/projects', self.cfg['OSM_PROJECT'], {'name': self.cfg['OSM_PROJECT']})
        self.cfg['OSM_PROJECT_ID'] = identifier(project['_id'])
        self.catalog.authenticate(self.cfg['OSM_USER'], self.cfg['OSM_PASSWORD'], self.cfg['OSM_PROJECT_ID'])
        name = self.cfg['OSM_VIM_NAME']
        if not name:
            raise ConfigError('Set OSM_VIM_NAME to the intended dummy VIM name')
        vim = self.ensure_registration('/admin/v1/vim_accounts', name,
                                      {'name': name, 'vim_type': 'dummy', 'vim_url': 'http://localhost',
                                       'vim_user': 'installer', 'vim_password': secrets.token_hex(24),
                                       'vim_tenant_name': 'submission', 'config': {}})
        if vim.get('vim_type') != 'dummy':
            raise ConfigError('Existing VIM has a different type; refuse to reuse it')
        self.cfg['OSM_VIM_ACCOUNT_ID'] = identifier(vim['_id'])
        kubeconfig = self.kjson('config', 'view', '--raw', '--flatten', '--minify', workload=True)
        cluster = self.ensure_registration('/admin/v1/k8sclusters', self.cfg['OSM_CLUSTER_NAME'],
                                          {'name': self.cfg['OSM_CLUSTER_NAME'], 'credentials': kubeconfig,
                                           'vim_account': self.cfg['OSM_VIM_ACCOUNT_ID'], 'k8s_version': '1.29',
                                           'nets': {'net1': 'mgmt'}, 'deployment_methods': {'helm-chart-v3': True, 'juju-bundle': False}})
        if cluster.get('vim_account') != self.cfg['OSM_VIM_ACCOUNT_ID'] or not cluster.get('deployment_methods', {}).get('helm-chart-v3'):
            raise ConfigError('OSM cluster registration differs from the required VIM/Helm method')
        self.cfg['OSM_K8S_CLUSTER_ID'] = identifier(cluster['_id'])
        if self.cfg['OSM_PROJECT_NAMESPACE'] and self.cfg['OSM_PROJECT_NAMESPACE'] != self.cfg['OSM_PROJECT_ID']:
            raise ConfigError('Submission dashboard OSM deployment requires the discovered project namespace; leave OSM_PROJECT_NAMESPACE blank initially')
        self.cfg['OSM_PROJECT_NAMESPACE'] = self.cfg['OSM_PROJECT_NAMESPACE'] or self.cfg['OSM_PROJECT_ID']
        self.apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': self.cfg['OSM_PROJECT_NAMESPACE']}}, workload=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            current = next(c for c in self.entries('/admin/v1/k8sclusters') if c['_id'] == cluster['_id'])
            method = current.get('_admin', {}).get('helm-chart-v3', {})
            if method.get('id'):
                break
            time.sleep(5)
        else:
            raise ConfigError('OSM Helm cluster initialization timed out; rerun after inspecting OSM LCM')
        self.context = discover_context(self.cfg, self.catalog,
                                       lambda: self.kjson('get', 'namespaces', workload=True))
        management_uid = self.kjson('get', 'namespace', 'kube-system')['metadata']['uid']
        if management_uid != self.context['cluster_uid']:
            raise ConfigError('Submission monitoring requires management/workload contexts to address the same cluster; separate clusters need explicit monitoring integration')
        self.snapshot()

    def packages(self, keys):
        scenarios = select_scenarios(self.registry, keys)
        prepare(ROOT, DEFAULT_OUTPUT, scenarios)
        artifacts = [validated_snapshot(ROOT, DEFAULT_OUTPUT, s) for s in scenarios]
        onboard(self.catalog, scenarios, artifacts)
        return [self.catalog.verify(s, a) for s, a in zip(scenarios, artifacts)]

    def core(self):
        identities = self.packages(['open5gs'])[0]
        namespace = self.cfg['OSM_PROJECT_NAMESPACE']
        path = '/nslcm/v1/ns_instances'
        instances = self.entries(path)
        matches = [e for e in instances if e.get('name') == 'core-persistent']
        receipt = self.directory / 'core-instance.json'
        if receipt.exists():
            recorded = json.loads(receipt.read_text())
            if recorded['context'] != self.context or len(matches) != 1 or matches[0]['id'] != recorded['id']:
                raise ConfigError('Core receipt/context differs; refuse to adopt another deployment')
            if recorded.get('stage') != 'instantiated':
                raise ConfigError('Interrupted core creation/instantiation; inspect the recorded OSM instance before explicit recovery')
            ns_id = recorded['id']
        else:
            if matches:
                raise ConfigError('Existing core has no installer receipt; explicit adoption required')
            response = parse_response(self.catalog.request('POST', path,
                                      yaml.safe_dump({'nsName': 'core-persistent', 'nsdId': identities['ns'],
                                                      'vimAccountId': self.context['vim_id']}).encode()))
            ns_id = identifier(response.get('id') or response.get('_id'))
            atomic_write(receipt, json.dumps({'id': ns_id, 'context': self.context, 'stage': 'created'}).encode())
            body = {'nsdId': identities['ns'], 'vimAccountId': self.context['vim_id'],
                    'additionalParamsForVnf': [{'member-vnf-index': 'open5gs',
                                              'additionalParamsForKdu': [{'kdu_name': 'open5gs', 'k8s-namespace': namespace}]}]}
            self.catalog.request('POST', path + '/' + ns_id + '/instantiate', yaml.safe_dump(body).encode())
            atomic_write(receipt, json.dumps({'id': ns_id, 'context': self.context, 'stage': 'instantiated'}).encode())
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            status = parse_response(self.catalog.request('GET', path + '/' + ns_id))
            if status.get('nsState') == 'READY':
                break
            if status.get('nsState') in ('BROKEN', 'FAILED'):
                raise ConfigError('OSM core instantiation failed; receipt retained for explicit recovery')
            time.sleep(5)
        else:
            raise ConfigError('OSM core readiness timeout; rerun waits for the same instance')
        self.kubectl('wait', 'pvc/data-open5gs-mongo-custom-0', '-n', namespace,
                     '--for=jsonpath={.status.phase}=Bound', '--timeout=600s', workload=True)
        self.kubectl('rollout', 'status', 'statefulset/open5gs-mongo-custom', '-n', namespace,
                     '--timeout=600s', workload=True)
        self.core_workload_readiness(namespace)
        services = self.kjson('get', 'services', '-n', namespace, workload=True)['items']
        alias = [s for s in services if s['metadata']['name'] == 'amf-ngap-stable']
        if len(alias) != 1 or not any(p['port'] == 38412 and p.get('protocol') == 'SCTP' for p in alias[0]['spec']['ports']):
            raise ConfigError('Stable AMF SCTP Service is missing')
        metrics = [s for s in services if s['metadata']['name'].endswith('-metrics')]
        if len(metrics) != 4:
            raise ConfigError('Expected four Open5GS metrics Services')
        endpoints = self.kjson('get', 'endpoints', 'amf-ngap-stable', '-n', namespace, workload=True)
        if not any(s.get('addresses') for s in endpoints.get('subsets', [])):
            raise ConfigError('Stable AMF Service has no ready endpoints')
        self.core_id = ns_id

    def core_workload_readiness(self, namespace):
        scenario = select_scenarios(self.registry, ['open5gs'])[0]
        source = ROOT / scenario['charts'][0]['source']
        values = yaml.safe_load((source / 'values.yaml').read_text())
        dependencies = yaml.safe_load((source / 'Chart.yaml').read_text())['dependencies']
        required = {d.get('alias', d['name']) for d in dependencies
                    if values.get(d.get('alias', d['name']), {}).get('enabled')}
        deployments = self.kjson('get', 'deployments', '-n', namespace, workload=True)['items']
        present = {d['metadata'].get('labels', {}).get('app.kubernetes.io/name'): d for d in deployments}
        if required - present.keys():
            raise ConfigError('Open5GS is missing required chart workloads: ' + ', '.join(sorted(required - present.keys())))
        for component in sorted(required):
            deployment = present[component]
            if deployment['spec'].get('replicas', 1) < 1:
                raise ConfigError('Open5GS workload is scaled to zero: ' + component)
            self.kubectl('rollout', 'status', 'deployment/' + deployment['metadata']['name'],
                         '-n', namespace, '--timeout=600s', workload=True)
        pvc = self.kjson('get', 'pvc', 'data-open5gs-mongo-custom-0', '-n', namespace, workload=True)
        if pvc['spec'].get('storageClassName') != 'longhorn':
            raise ConfigError('Open5GS Mongo PVC is not using the required Longhorn storage class')
        # StatefulSet pod readiness alone is insufficient: its chart has no probe.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = self.kubectl('exec', '-n', namespace, 'open5gs-mongo-custom-0', '--',
                                  'mongosh', '--quiet', '--eval', 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)',
                                  workload=True, check=False)
            if result.returncode == 0:
                return
            time.sleep(2)
        raise ConfigError('Open5GS Mongo database readiness timed out')

    def subscribers(self):
        content = private_archive(self.cfg['SUBSCRIBER_DATABASE_INPUT'])
        namespace = self.cfg['OSM_PROJECT_NAMESPACE']
        pvc = self.kjson('get', 'pvc', 'data-open5gs-mongo-custom-0', '-n', namespace, workload=True)
        binding = {'context': self.context, 'pvc_uid': pvc['metadata']['uid']}
        digest = hashlib.sha256(content).hexdigest()
        receipt = self.directory / 'subscriber-import.json'
        record = json.loads(receipt.read_text()) if receipt.exists() else None
        def counts():
            # Existence/count only. Never read authentication fields/documents.
            expression = "JSON.stringify({all:db.getSiblingDB('open5gs').subscribers.countDocuments({}),reference:db.getSiblingDB('open5gs').subscribers.countDocuments({imsi:'208930000000003'})})"
            data = self.kubectl('exec', '-n', namespace, 'open5gs-mongo-custom-0', '--',
                                'mongosh', '--quiet', '--eval', expression, workload=True).stdout
            return json.loads(data)
        current = counts()
        decision = import_decision(record, binding, digest, current['all'], 0)
        if decision == 'restore':
            # Fail before restoring if this exact tooling is unavailable.
            self.kubectl('exec', '-n', namespace, 'open5gs-mongo-custom-0', '--',
                         'mongorestore', '--version', workload=True)
            self.runner.run(restore_command(self.cfg['OSM_KUBECONFIG_PATH'], namespace), data=content)
            current = counts()
            if current['reference'] != 1:
                raise ConfigError('Private import must contain exactly one reference subscriber; no receipt published')
            atomic_write(receipt, json.dumps({'binding': binding, 'input_sha256': digest, 'complete': True}).encode())
        elif current['reference'] != 1:
            raise ConfigError('Reference subscriber existence check failed')

    def baseline(self):
        self.packages(['cran-srsran'])
        state_path = Path(self.cfg['ACTIVE_STATE_PATH'])
        with exclusive_lock(self.directory / '.lifecycle.lock'):
            if state_path.exists():
                state = yaml.safe_load(state_path.read_text())
                validate_state(state, self.registry, self.context)
                if state.get('osm', {}).get('core_instance_id') != self.core_id:
                    raise ConfigError('Runtime core identity differs; explicit reconciliation required')
            else:
                # Do not adopt an unknown RAN instance from an existing project.
                instances = self.entries('/nslcm/v1/ns_instances')
                if any(e['id'] != self.core_id for e in instances):
                    raise ConfigError('Project contains unowned instances; explicit reconciliation required')
                state = {'active': 'none', 'osm': {'core_instance_id': self.core_id}, 'context': self.context}
                validate_state(state, self.registry, self.context)
                atomic_write(state_path, yaml.safe_dump(state).encode())

    def monitoring(self):
        values = yaml.safe_load((ROOT / 'monitoring/kube-prometheus-stack-values.yaml').read_text())
        values.setdefault('grafana', {}).update({'adminPassword': self.cfg['GRAFANA_ADMIN_PASSWORD'],
                                                'service': {'type': 'NodePort', 'nodePort': int(self.cfg['GRAFANA_NODEPORT'])}})
        self.release('kube-prometheus-stack', 'monitoring', 'kube-prometheus-stack', self.lock['monitoring_chart'],
                     values, 'https://prometheus-community.github.io/helm-charts')
        self.kubectl('rollout', 'status', 'statefulset', '-n', 'monitoring', '--timeout=600s')
        self.apply(list(yaml.safe_load_all(render_manifest(ROOT / 'monitoring/prometheus-nodeport.yaml', self.cfg))))
        for relative in ['monitoring/open5gs-podmonitor.yaml', 'monitoring/ran-exporter/podmonitor.yaml',
                         'monitoring/latency-probe/podmonitor.yaml']:
            self.apply(list(yaml.safe_load_all(render_manifest(ROOT / relative, self.cfg))))
        namespace = self.cfg['OSM_PROJECT_NAMESPACE']
        services = self.kjson('get', 'services', '-n', namespace, workload=True)['items']
        amf = [s['metadata']['name'] for s in services if s['metadata']['name'].endswith('-amf-sbi')]
        if len(amf) != 1:
            raise ConfigError('AMF SBI discovery is missing or ambiguous')
        for component, script in [('ran-exporter', 'ran_exporter.py'), ('latency-probe', 'latency_probe.py')]:
            self.apply({'apiVersion': 'v1', 'kind': 'ConfigMap',
                        'metadata': {'name': component + '-script', 'namespace': namespace},
                        'data': {script: (ROOT / 'monitoring' / component / script).read_text()}}, workload=True)
            if component == 'ran-exporter':
                self.apply(list(yaml.safe_load_all(render_manifest(ROOT / 'monitoring/ran-exporter/rbac.yaml', self.cfg))), workload=True)
            documents = list(yaml.safe_load_all(render_manifest(ROOT / 'monitoring' / component / 'deployment.yaml', self.cfg)))
            for d in documents:
                if d and d['kind'] == 'Deployment':
                    c = d['spec']['template']['spec']['containers'][0]
                    for e in c.get('env', []):
                        if e['name'] == 'TARGET_HOST':
                            e['value'] = amf[0]
                        if e['name'] == 'POD_LABEL_SELECTOR':
                            e['value'] = 'app=cran-srsran-cu'
                    image = 'docker.io/library/python:3.12-slim'
                    c['image'] = image + '@' + self.lock['images'][image]
            self.apply(documents, workload=True)
            self.kubectl('rollout', 'status', 'deployment/' + component, '-n', namespace, '--timeout=600s', workload=True)

    def services(self):
        dashboard_active = self.runner.run(['systemctl', 'is-active', '--quiet', 'ran-selector'], check=False).returncode == 0
        if dashboard_active:
            check_installed_service('dashboard', self.cfg, Path('/etc/systemd/system/ran-selector.service'))
        else:
            self.runner.run(['python3', '-m', 'venv', str(ROOT / 'ran-selector/venv')])
            self.runner.run([ROOT / 'ran-selector/venv/bin/pip', 'install', '-r', ROOT / 'ran-selector/requirements.txt'])
        for kind, name in [('dashboard', 'ran-selector'), ('watcher', 'layer3-watcher'), ('rancher-forward', 'rancher-portforward')]:
            unit = Path('/etc/systemd/system/' + name + '.service')
            active = self.runner.run(['systemctl', 'is-active', '--quiet', name], check=False).returncode == 0
            if active:
                check_installed_service(kind, self.cfg, unit)
                continue
            write_snapshot(self.directory / (kind + '-service.json'), service_configuration(kind, self.cfg))
            self.runner.run(['sudo', 'tee', str(unit)], data=render_service(kind, self.cfg).encode())
            self.runner.run(['sudo', 'systemctl', 'daemon-reload'])
            self.runner.run(['sudo', 'systemctl', 'enable', '--now', name + '.service'])
            self.runner.run(['systemctl', 'is-active', '--quiet', name + '.service'])
        # Existing dashboard lifecycle performs all preflight/catalog checks.
        import urllib.request
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.cfg['DASHBOARD_URL'] + '/api/config', timeout=5) as response:
                    json.load(response)
                break
            except OSError:
                time.sleep(2)
        else:
            raise ConfigError('Dashboard readiness timeout')

    def run(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive_lock(self.directory / '.installer.lock'):
            self.snapshot()
            for label, action in [('Kubernetes', self.kubernetes), ('infrastructure', self.infrastructure),
                                  ('OSM', self.osm), ('Open5GS', self.core),
                                  ('private subscriber input', self.subscribers),
                                  ('C-RAN/srsRAN packages and runtime state', self.baseline),
                                  ('monitoring', self.monitoring), ('services', self.services)]:
                self.stage(label, action)


def resume_config(cfg):
    """Reuse discovered identities only; never overwrite changed private inputs."""
    cfg = dict(cfg)
    saved = Path(cfg['RUNTIME_DIR']) / 'installer-config.json'
    if saved.exists():
        previous = read_snapshot(saved)
        for field in ('OSM_PROJECT_ID', 'OSM_VIM_ACCOUNT_ID', 'OSM_K8S_CLUSTER_ID',
                      'OSM_PROJECT_NAMESPACE', 'OSM_CA_CERT_PATH'):
            if not cfg[field]:
                cfg[field] = previous[field]
    return cfg


def preflight(cfg):
    # Validate the owner-only input before host/bootstrap or any deployment.
    # Read it again at import time to reject later permission/format changes.
    private_archive(cfg['SUBSCRIBER_DATABASE_INPUT'])
    if cfg['DEPLOYMENT_PROFILE'] != 'rfsim':
        raise ConfigError('Submission installer supports the existing rfsim baseline only')
    if cfg['LAYER3_FAILOVER_SCENARIO'] != 'cran-srsran':
        raise ConfigError('Submission watcher requires LAYER3_FAILOVER_SCENARIO=cran-srsran')
    if not 30000 <= int(cfg['OSM_HTTPS_PORT']) <= 32767:
        raise ConfigError('Submission ingress requires OSM_HTTPS_PORT in the NodePort range')
    if not cfg['OSM_CLUSTER_NAME']:
        raise ConfigError('Set OSM_CLUSTER_NAME')
    if not cfg['OSM_VIM_NAME']:
        raise ConfigError('Set OSM_VIM_NAME for the dummy VIM')
    for kind, name in [('dashboard', 'ran-selector'), ('watcher', 'layer3-watcher'), ('rancher-forward', 'rancher-portforward')]:
        if subprocess.run(['systemctl', 'is-active', '--quiet', name], capture_output=True).returncode == 0:
            check_installed_service(kind, cfg, Path('/etc/systemd/system/' + name + '.service'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    cfg = resume_config(load_config(network=True, require=('install', 'watcher', 'osm')))
    preflight(cfg)
    if not args.preflight:
        Installer(cfg).run()


if __name__ == '__main__':
    try:
        main()
    except (ConfigError, CatalogError, OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit('ERROR: ' + str(error))
