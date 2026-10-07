"""Explicit local rendering helpers. Secrets go only to mode-0600 runtime files."""
import argparse
import getpass
import json
import hashlib
from pathlib import Path
import re
import sys
import yaml
from runtime_config import ROOT, load_config, ConfigError, write_snapshot, DEFAULTS, SECRETS, read_snapshot
from local_safety import atomic_write


def systemd_quote(value, executable=False):
    """Encode an argument; ExecStart uses ':' to disable environment expansion."""
    if any(ord(c) < 32 for c in value):
        raise ConfigError('Control characters are forbidden in service paths')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def render_service(kind, cfg, user=None):
    user = user or getpass.getuser()
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*\$?', user):
        raise ConfigError('Invalid service user name')
    root = Path(cfg['REPO_ROOT'])
    if kind not in ('dashboard', 'watcher', 'rancher-forward'):
        raise ConfigError('Unknown service kind')
    snapshot = Path(cfg['RUNTIME_DIR']) / (kind + '-service.json')
    command = ':/usr/bin/python3 ' + ' '.join(systemd_quote(str(arg)) for arg in
                [root / 'scripts/service_entry.py', kind, snapshot])
    cwd = root if kind == 'rancher-forward' else root / ('ran-selector' if kind == 'dashboard' else 'layer3-autonomous')
    public = {k: v for k, v in cfg.items() if k not in SECRETS}
    revision = 'Environment=P0_SERVICE_REVISION=' + hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    if any(ord(c) < 32 for c in str(cwd)):
        raise ConfigError('Control characters are forbidden in service paths')
    # WorkingDirectory has path/specifier semantics, not ExecStart argument semantics.
    return '\n'.join(['[Unit]', 'Description=5G ' + kind, 'After=network-online.target',
                     'Wants=network-online.target', '', '[Service]', 'Type=simple', 'User=' + user,
                     'WorkingDirectory=' + str(cwd).replace('%', '%%'), revision,
                     'ExecStart=' + command, 'Restart=always', 'RestartSec=10', '',
                     '[Install]', 'WantedBy=multi-user.target', ''])


def service_configuration(kind, cfg):
    selected = dict(cfg)
    for key in SECRETS:
        if kind != 'dashboard' or key != 'OSM_PASSWORD':
            selected[key] = ''
    return selected


def check_installed_service(kind, cfg, unit):
    try:
        installed = Path(unit).read_text()
        if installed != render_service(kind, cfg):
            # Older service units hashed an empty CA setting before the shared
            # managed trust file was provisioned. Their launcher reloads the
            # private snapshot; no ExecStart/user/context change is involved.
            legacy = dict(cfg, OSM_CA_CERT_PATH='')
            managed = Path(cfg['RUNTIME_DIR']) / 'osm-ca.crt'
            compatible = (cfg.get('OSM_CA_CERT_PATH') == str(managed.resolve()) and
                          installed == render_service(kind, legacy))
            if not compatible:
                raise ConfigError('Running service configuration differs; explicitly review and restart before installation')
            from osm_tls import validate_certificate
            from runtime_config import private_text
            certificate, mode = private_text(managed, required=True)
            if mode & 0o022:
                raise ConfigError('Managed CA must not be group/world writable')
            validate_certificate(certificate.encode('utf-8'))
        snapshot = Path(cfg['RUNTIME_DIR']) / (kind + '-service.json')
        if read_snapshot(snapshot) != {k: service_configuration(kind, cfg)[k] for k in DEFAULTS}:
            raise ConfigError('Running service private configuration differs; explicitly review and restart before installation')
    except OSError:
        raise ConfigError('Cannot verify running service configuration; explicit reconciliation required') from None


def render_manifest(source, cfg):
    source = Path(source)
    if not source.resolve().is_relative_to(ROOT / 'monitoring') or source.is_symlink():
        raise ConfigError('Only repository monitoring templates may be rendered')
    text = source.read_text()
    replacements = {'PLACEHOLDER_NAMESPACE': cfg['OSM_PROJECT_NAMESPACE'],
                    'PLACEHOLDER_GRAFANA_HOST': 'grafana-metrics.' + cfg['OSM_BASE_DOMAIN'],
                    'PLACEHOLDER_PROMETHEUS_NODEPORT': cfg['PROMETHEUS_NODEPORT']}
    for key, value in replacements.items():
        if key in text:
            if not value or (key == 'PLACEHOLDER_GRAFANA_HOST' and not cfg['OSM_BASE_DOMAIN']):
                raise ConfigError('Missing configuration for ' + key)
            text = text.replace(key, value)
    if 'PLACEHOLDER_' in text:
        raise ConfigError('Unresolved manifest placeholder')
    documents = list(yaml.safe_load_all(text))
    return yaml.safe_dump_all(documents, sort_keys=False)


def private_values(kind, cfg):
    field = 'RANCHER_BOOTSTRAP_PASSWORD' if kind == 'rancher' else 'GRAFANA_ADMIN_PASSWORD'
    if not cfg[field]:
        raise ConfigError('Missing required ' + field)
    values = {'bootstrapPassword': cfg[field]} if kind == 'rancher' else {
        'grafana': {'adminPassword': cfg[field], 'service': {'type': 'NodePort', 'nodePort': int(cfg['GRAFANA_NODEPORT'])}}}
    destination = Path(cfg['RUNTIME_DIR']) / (kind + '-private-values.yaml')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(destination, yaml.safe_dump(values).encode())
    destination.chmod(0o600)
    return destination


def initialize_state(path, context, instances):
    if not isinstance(instances, list) or instances:
        raise ConfigError('Empty initialization requires a verified empty OSM instance listing')
    from state_schema import validate_state
    from scenario_registry import load_registry
    candidate = {'active': 'none', 'osm': {}, 'context': context}
    validate_state(candidate, load_registry(), context)
    path = Path(path)
    if path.exists():
        raise ConfigError('Runtime state already exists; explicit reconciliation is required')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    from local_safety import exclusive_lock
    with exclusive_lock(path.parent / '.lifecycle.lock'):
        if path.exists():
            raise ConfigError('Runtime state already exists')
        atomic_write(path, yaml.safe_dump(candidate).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['dashboard', 'watcher', 'rancher-forward', 'manifest', 'rancher', 'grafana', 'init-state'])
    parser.add_argument('source', nargs='?')
    parser.add_argument('--check-installed', action='store_true', help='Fail on service configuration drift without writing')
    args = parser.parse_args()
    try:
        cfg = dict(load_config(network='auto', require=('osm', 'kubernetes') if args.kind == 'init-state' else
                          ('watcher',) if args.kind == 'watcher' else ()))
        if args.check_installed:
            if args.kind not in ('dashboard', 'watcher') or not args.source:
                raise ConfigError('Service check requires dashboard/watcher and an installed unit path')
            check_installed_service(args.kind, cfg, args.source)
            return
        if args.kind in ('dashboard', 'watcher', 'rancher-forward'):
            from osm_tls import ensure_osm_ca
            ensure_osm_ca(cfg)
            write_snapshot(Path(cfg['RUNTIME_DIR']) / (args.kind + '-service.json'), service_configuration(args.kind, cfg))
            print(render_service(args.kind, cfg), end='')
        elif args.kind == 'manifest':
            print(render_manifest(args.source, cfg), end='')
        elif args.kind == 'init-state':
            # Explicit future operator command. Read-only remote discovery, local state only.
            from osm_catalog import Catalog, parse_response
            from runtime_config import discover_context, kubernetes_json
            catalog = Catalog(cfg['OSM_HOST'])
            catalog.ca_file = cfg.get('OSM_CA_CERT_PATH') or None
            catalog.authenticate(cfg['OSM_USER'], cfg['OSM_PASSWORD'], cfg['OSM_PROJECT_ID'] or cfg['OSM_PROJECT'])
            context = discover_context(cfg, catalog, lambda: kubernetes_json(cfg, ['get', 'namespaces', '-o', 'json']))
            initialize_state(cfg['ACTIVE_STATE_PATH'], context, parse_response(catalog.request('GET', '/nslcm/v1/ns_instances')))
            print('Initialized empty runtime state')
        else:
            print(private_values(args.kind, cfg))
    except (ConfigError, OSError, ValueError) as error:
        parser.exit(1, 'ERROR: ' + (str(error) if isinstance(error, ConfigError) else 'Runtime rendering failed') + '\n')

if __name__ == '__main__':
    main()
