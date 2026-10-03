"""Pure reference-source adaptations; never execute upstream installer scripts."""
import json
import hashlib
from urllib.parse import urlsplit
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def versions():
    return json.loads((ROOT / 'config/reference-versions.json').read_text())


def apply_osm_source_patch(source, lock, runner):
    """Reconstruct the reference committed tree in an expendable build copy.

    The pristine public checkout remains untouched. No upstream script executes.
    Already reconstructed copies are accepted only after content verification.
    """
    from runtime_config import ConfigError
    source = Path(source)
    spec = lock['osm_source_patch']
    patch = ROOT / spec['path']
    if patch.is_symlink() or not patch.is_file() or hashlib.sha256(patch.read_bytes()).hexdigest() != spec['sha256']:
        raise ConfigError('OSM reference source patch checksum mismatch')
    def git(*args, check=True):
        return runner.run(['git', '-C', str(source), *args], check=check,
                          context='OSM reference source patch')
    if git('rev-parse', 'HEAD').stdout.decode().strip() != lock['osm_source_commit']:
        raise ConfigError('OSM reference source patch: wrong public base')
    if git('diff', '--quiet', check=False).returncode or git('ls-files', '--others').stdout:
        raise ConfigError('OSM reference source patch: unexpected working-tree content')
    tree = git('write-tree').stdout.decode().strip()
    if tree == spec['tree']:
        return tree
    if tree != git('rev-parse', 'HEAD^{tree}').stdout.decode().strip():
        raise ConfigError('OSM reference source patch: unexpected staged content')
    git('apply', '--check', '--index', str(patch))
    git('apply', '--index', str(patch))
    tree = git('write-tree').stdout.decode().strip()
    if tree != spec['tree'] or git('diff', '--quiet', check=False).returncode:
        raise ConfigError('OSM reference source patch: reconstructed tree mismatch')
    return tree


def adapt_osm_chart(chart):
    """Fix the observed upstream client-only server certificates in a build copy.

    The patched reference wrapper skipped upstream k3s/auxiliary installation;
    our installer owns those stages instead. Its host-specific domain override
    becomes values generated from runtime configuration. No external scripts run.
    """
    chart = Path(chart)
    changed = []
    for path in sorted((chart / 'templates').rglob('*certificate.yaml')):
        if path.name == 'lcm-client-certificate.yaml':
            continue
        text = path.read_text()
        old = '    - "client auth"'
        if old in text:
            path.write_text(text.replace(old, '    - "server auth"\n    - "client auth"'))
            changed.append(path.relative_to(chart).as_posix())
    if not changed:
        raise ValueError('Pinned OSM certificate patch no longer matches source')
    deployment = chart / 'templates/lcm/lcm-deployment.yaml'
    if deployment.exists():
        text = deployment.read_text()
        for indent, block in [
            ('            ', '- mountPath: /etc/osm/mgmtcluster-kubeconfig.yaml\n              name: mgmtcluster-kubeconfig\n              readOnly: true\n              subPath: mgmtcluster-kubeconfig.yaml'),
            ('        ', '- name: mgmtcluster-kubeconfig\n          secret:\n            defaultMode: 420\n            items:\n            - key: kubeconfig\n              path: mgmtcluster-kubeconfig.yaml\n            secretName: mgmtcluster-secret')]:
            old = indent + '{{- if .Values.global.gitops.enabled }}\n' + indent + block + '\n' + indent + '{{- end }}'
            if text.count(old) != 1:
                raise ValueError('Pinned OSM management kubeconfig mount patch no longer matches source')
            text = text.replace(old, indent + block)
        deployment.write_text(text.replace('defaultMode: 420\n            items:\n            - key: kubeconfig', 'defaultMode: 416\n            items:\n            - key: kubeconfig'))
        changed.append(deployment.relative_to(chart).as_posix())
    return changed


def management_credentials(namespace, token_data):
    """Private target-cluster kubeconfig; never write it to chart values/files."""
    import base64
    import yaml
    from runtime_config import ConfigError
    try:
        token = base64.b64decode(token_data['token'], validate=True).decode()
        ca = token_data['ca.crt']
        if not token or not base64.b64decode(ca, validate=True):
            raise ValueError()
    except (KeyError, ValueError, UnicodeError, TypeError):
        raise ConfigError('OSM management service-account credentials are missing or invalid') from None
    document = {'apiVersion': 'v1', 'kind': 'Config',
                'clusters': [{'name': 'management', 'cluster': {'server': 'https://kubernetes.default.svc', 'certificate-authority-data': ca}}],
                'users': [{'name': 'osm-lcm-management', 'user': {'token': token}}],
                'contexts': [{'name': 'management', 'context': {'cluster': 'management', 'user': 'osm-lcm-management', 'namespace': namespace}}],
                'current-context': 'management'}
    return {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
            'metadata': {'name': 'mgmtcluster-secret', 'namespace': namespace,
                         'labels': {'app.kubernetes.io/managed-by': 'reference-installer'}},
            'data': {'kubeconfig': base64.b64encode(yaml.safe_dump(document).encode()).decode()}}


def validate_management_mount(deployment, secret_keys):
    from runtime_config import ConfigError
    if 'kubeconfig' not in secret_keys:
        raise ConfigError('OSM LCM management kubeconfig missing: mgmtcluster-secret/kubeconfig')
    spec = deployment.get('spec', {}).get('template', {}).get('spec', {})
    volumes = [v for v in spec.get('volumes', []) if v.get('name') == 'mgmtcluster-kubeconfig']
    containers = [c for c in spec.get('containers', []) if c.get('name') == 'lcm']
    mounts = [m for c in containers for m in c.get('volumeMounts', []) if m.get('name') == 'mgmtcluster-kubeconfig']
    if len(volumes) != 1 or len(mounts) != 1:
        raise ConfigError('OSM LCM management kubeconfig mount missing')
    secret = volumes[0].get('secret', {})
    if secret.get('defaultMode') == 416 and spec.get('securityContext', {}).get('fsGroup') != 1000:
        raise ConfigError('OSM LCM management kubeconfig permissions require fsGroup 1000')
    if secret.get('secretName') != 'mgmtcluster-secret' or secret.get('optional', False) or secret.get('defaultMode') not in (416, 420) or secret.get('items') != [{'key': 'kubeconfig', 'path': 'mgmtcluster-kubeconfig.yaml'}] or mounts[0].get('mountPath') != '/etc/osm/mgmtcluster-kubeconfig.yaml' or mounts[0].get('subPath') != 'mgmtcluster-kubeconfig.yaml' or mounts[0].get('readOnly') is not True:
        raise ConfigError('OSM LCM management kubeconfig mount path/source/permissions mismatch')


def osm_values(cfg, lock):
    """Reference OSM chart configuration, excluding all private inputs."""
    domain = cfg['OSM_BASE_DOMAIN']
    values = {'global': {'hostname': domain, 'ingressClassName': 'nginx',
                         'gitops': {'enabled': False}},
              'certauth': {'enabled': True}}
    # Pin the actual reference NF bytes while preserving repository/tag behavior.
    for component, chart_key in [('nbi', 'nbi'), ('lcm', 'lcm'), ('mon', 'mon'),
                                 ('ro', 'ro'), ('ng-ui', 'ngui'), ('webhook', 'webhookTranslator')]:
        repo = 'docker.io/opensourcemano/' + component
        key = repo + ':releasenineteen-daily'
        values[chart_key] = {'image': {'repository': repo,
                                     'tag': 'releasenineteen-daily@' + lock['images'][key]}}
    values['ngui']['ingress'] = {'host': urlsplit(cfg.get('OSM_HOST') or 'https://gui.' + domain).hostname}
    # Bitnami historical tags no longer resolve reliably; these are observed bytes.
    values['mongodb'] = {'image': {'registry': 'registry-1.docker.io',
                                   'repository': 'bitnami/mongodb', 'tag': 'latest',
                                   'digest': lock['images']['registry-1.docker.io/bitnami/mongodb:latest']}}
    values['kafka'] = {'image': {'registry': 'docker.io', 'repository': 'bitnamilegacy/kafka',
                               'tag': '3.8.0-debian-12-r5',
                               'digest': lock['images']['docker.io/bitnamilegacy/kafka:3.8.0-debian-12-r5']}}
    values['airflow'] = {'defaultAirflowRepository': 'docker.io/opensourcemano/airflow',
                         'defaultAirflowTag': 'releasenineteen-daily@' + lock['images']['docker.io/opensourcemano/airflow:releasenineteen-daily'],
                         'postgresql': {'image': {'registry': 'docker.io',
                                                   'repository': 'bitnamilegacy/postgresql', 'tag': '11',
                                                   'digest': lock['images']['docker.io/bitnamilegacy/postgresql:11']}},
                         'ingress': {'web': {'hosts': [{'name': 'airflow.' + domain,
                                                       'tls': {'enabled': True, 'secretName': 'airflow-cert'}}]}}}
    values['grafana'] = {'ingress': {'hosts': ['grafana.' + domain],
                                    'tls': [{'secretName': 'grafana-cert', 'hosts': ['grafana.' + domain]}]}}
    values['prometheus'] = {'server': {'ingress': {'hosts': ['prometheus.' + domain],
                                                  'tls': [{'secretName': 'prometheus-cert', 'hosts': ['prometheus.' + domain]}]}}}
    values['prometheus']['server']['sidecarContainers'] = {'prometheus-config-sidecar': {'image': 'docker.io/opensourcemano/prometheus:releasenineteen-daily@' + lock['images']['docker.io/opensourcemano/prometheus:releasenineteen-daily']}}
    return values


def api_ingress(cfg):
    """Keep the existing GUI /osm API contract with a verified server certificate."""
    from urllib.parse import urlsplit
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress',
            'metadata': {'name': 'submission-osm-api', 'namespace': cfg['OSM_NAMESPACE'],
                         'annotations': {'nginx.ingress.kubernetes.io/force-ssl-redirect': 'true'}},
            'spec': {'ingressClassName': 'nginx',
                     'tls': [{'secretName': 'ngui-cert', 'hosts': [urlsplit(cfg['OSM_HOST']).hostname]}],
                     'rules': [{'host': urlsplit(cfg['OSM_HOST']).hostname,
                                'http': {'paths': [{'path': '/osm', 'pathType': 'Prefix',
                                                    'backend': {'service': {'name': 'nbi', 'port': {'number': 9999}}}}]}}]}}
