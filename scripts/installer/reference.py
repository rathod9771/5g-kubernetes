"""Pure reference-source adaptations; never execute upstream installer scripts."""
import json
from urllib.parse import urlsplit
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def versions():
    return json.loads((ROOT / 'config/reference-versions.json').read_text())


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
    return changed


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
