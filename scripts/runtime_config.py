"""Declarative runtime configuration. Importing this module performs no discovery.

Only local network discovery runs in load_config(network=True). OSM and Kubernetes
requests require an explicit call to discover_context; tests inject those adapters.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
from urllib.parse import urlsplit
import uuid
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
class ConfigError(ValueError):
    pass

DEFAULTS = {
    'HOST_IP': '', 'HOST_INTERFACE': '', 'REPO_ROOT': '', 'RUNTIME_DIR': '',
    'KUBECONFIG_PATH': '', 'OSM_KUBECONFIG_PATH': '', 'DEPLOYMENT_PROFILE': 'rfsim',
    'POD_CIDR': '10.244.0.0/16', 'SERVICE_CIDR': '10.96.0.0/12', 'UE_CIDR': '10.45.0.0/16',
    'EDGE_CIDR': '10.47.0.0/16', 'KUBELET_MAX_PODS': '200',
    'OSM_HOST': '', 'OSM_BASE_DOMAIN': '', 'OSM_HTTPS_PORT': '30843',
    'OSM_USER': '', 'OSM_PASSWORD': '', 'OSM_PROJECT': '', 'OSM_PROJECT_ID': '',
    'OSM_VIM_NAME': '', 'OSM_VIM_ACCOUNT_ID': '', 'OSM_K8S_CLUSTER_ID': '',
    'OSM_PROJECT_NAMESPACE': '', 'OSM_NAMESPACE': 'osm',
    'PROMETHEUS_NODEPORT': '30990', 'GRAFANA_NODEPORT': '31998', 'DASHBOARD_PORT': '8090',
    'RANCHER_PORT': '8443', 'PROMETHEUS_URL': '', 'GRAFANA_URL': '',
    'DASHBOARD_URL': '', 'RANCHER_URL': '', 'GRAFANA_ADMIN_PASSWORD': '',
    'RANCHER_BOOTSTRAP_PASSWORD': '', 'MCC': '208', 'MNC': '93', 'TAC': '1',
    'SST': '1', 'SD': '010203', 'DNN': 'internet',
    'LAYER3_BLER_THRESHOLD': '0.05', 'LAYER3_CHECK_INTERVAL_SECONDS': '10',
    'LAYER3_COOLDOWN_SECONDS': '120', 'LAYER3_FAILOVER_SCENARIO': '',
    'ACTIONS_LOG_PATH': '', 'RF_DEVICE_ARGS': '', 'SRSRAN_BINARY': '',
    'IMS_CONFIG_PATH': '', 'IMS_MYSQL_DATA_PATH': '', 'FLEXRIC_ADDRESS': '',
    'BENCH_IPERF_HOST': '', 'BENCH_PING_HOST': '8.8.8.8', 'BENCH_IPERF_PORT': '5201',
    'OSM_CA_CERT_PATH': '', 'OSM_BOOTSTRAP_PASSWORD': '',
    'INGRESS_HTTP_NODEPORT': '32080', 'OSM_CLUSTER_NAME': 'submission-cluster', 'SUBSCRIBER_DATABASE_INPUT': '',
}
SECRETS = {'OSM_PASSWORD', 'GRAFANA_ADMIN_PASSWORD', 'RANCHER_BOOTSTRAP_PASSWORD', 'OSM_BOOTSTRAP_PASSWORD'}
DNS = re.compile(r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z')

def private_text(path, required=False, strict=False):
    """Read one descriptor; never follow a replaced final-component symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if required:
            raise ConfigError('Required private configuration is missing') from None
        return None
    except OSError:
        raise ConfigError('Cannot safely open private configuration') from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ConfigError('Configuration must be an owner-controlled regular file')
        if strict and stat.S_IMODE(info.st_mode) & 0o077:
            raise ConfigError('Configuration containing secrets must have mode 0600 or stricter')
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            fd = None
            text = stream.read()
        return text, stat.S_IMODE(info.st_mode)
    except (OSError, UnicodeError):
        raise ConfigError('Cannot read private configuration') from None
    finally:
        if fd is not None:
            os.close(fd)


def read_snapshot(path):
    text, mode = private_text(path, required=True, strict=True)
    if mode & 0o077:
        raise ConfigError('Configuration snapshot must have mode 0600 or stricter')
    try:
        values = json.loads(text)
    except ValueError:
        raise ConfigError('Malformed configuration snapshot') from None
    if (not isinstance(values, dict) or set(values) != set(DEFAULTS)
            or any(not isinstance(v, str) for v in values.values())):
        raise ConfigError('Invalid configuration snapshot contract')
    return values


def write_snapshot(path, cfg):
    from local_safety import atomic_write
    root = Path(cfg['REPO_ROOT'])
    path = safe_path(str(path), root, Path.home())
    if path.is_relative_to(root) and not path.is_relative_to(root / '.runtime'):
        raise ConfigError('In-checkout snapshots must be under .runtime/')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o022:
        raise ConfigError('Snapshot directory must be owner-controlled and not shared writable')
    atomic_write(path, json.dumps({k: cfg[k] for k in DEFAULTS}, sort_keys=True).encode())


def read_env(path):
    """KEY=literal, optional quotes; no expansion, execution or inline comments."""
    opened = private_text(path, strict=Path(path).name == 'global.env')
    if opened is None:
        return {}
    text, mode = opened
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key not in DEFAULTS or key in values:
            raise ConfigError(f'Invalid or duplicate configuration key at line {number}')
        value = value.strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(f'Unclosed configuration quote at line {number}')
            value = value[1:-1]
        if '\x00' in value or '\r' in value:
            raise ConfigError(f'Invalid configuration value at line {number}')
        values[key] = value
    if any(values.get(key) for key in SECRETS) and mode & 0o077:
        raise ConfigError('Configuration containing secrets must have mode 0600 or stricter')
    return values


def safe_path(value, root, home):
    # Explicit HOME expansion only. Shell syntax stays literal, never executed.
    value = value.replace('${HOME}', str(home)).replace('$HOME', str(home))
    if value == '~' or value.startswith('~/'):
        value = str(home) + value[1:]
    if not value or any(ord(c) < 32 for c in value):
        raise ConfigError('Unsafe or empty configured path')
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ConfigError('Configured paths must not contain traversal or symlinks')
    return path.absolute()

def command_json(args):
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ConfigError('Structured discovery command failed: ' + args[0]) from None

def discovery_entries(value, kind):
    if not isinstance(value, list) or any(not isinstance(e, dict) for e in value):
        raise ConfigError('Invalid structured ' + kind + ' response')
    return value


def discover_network(host, interface, query=command_json, with_subnet=False):
    addresses = discovery_entries(query(['ip', '-j', '-d', '-4', 'address', 'show']), 'interface')
    pairs, prefixes, virtual = set(), {}, set()
    for entry in addresses:
        name = entry.get('ifname')
        info = entry.get('linkinfo', {})
        if not isinstance(name, str) or not isinstance(info, dict):
            raise ConfigError('Invalid structured interface metadata')
        if (re.match(r'^(lo$|docker|cni|flannel|cali|cilium|weave|veth|virbr|br-|kube|tun|tap|wg|tailscale|zt)', name)
                or info.get('info_kind') in ('bridge', 'veth', 'vxlan', 'geneve', 'tun', 'wireguard', 'dummy', 'macvlan', 'ipvlan', 'ipip', 'sit', 'gre', 'gretap', 'ip6gre', 'ip6tnl')):
            virtual.add(name)
        for addr in discovery_entries(entry.get('addr_info', []), 'interface address'):
            if addr.get('family') != 'inet':
                continue
            try:
                ip = str(ipaddress.IPv4Address(addr['local']))
                prefix = addr['prefixlen']
                if not isinstance(prefix, int) or isinstance(prefix, bool) or not 0 <= prefix <= 32:
                    raise ValueError()
                subnet = ipaddress.ip_network(f'{ip}/{prefix}', strict=False)
            except (KeyError, TypeError, ValueError):
                raise ConfigError('Invalid structured IPv4 address/prefix') from None
            pairs.add((name, ip))
            prefixes[(name, ip)] = subnet
    if interface and interface not in {entry['ifname'] for entry in addresses}:
        raise ConfigError('HOST_INTERFACE does not exist')
    if host:
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise ConfigError('HOST_IP must be a valid IPv4 address') from None
        candidates = {(name, addr) for name, addr in pairs if addr == host and (not interface or name == interface)}
    else:
        routes = discovery_entries(query(['ip', '-j', '-4', 'route', 'get', '1.1.1.1']), 'route')
        candidates = set()
        for route in routes:
            dev, src = route.get('dev'), route.get('prefsrc', route.get('src'))
            if not isinstance(dev, str) or not isinstance(src, str):
                raise ConfigError('Route has no usable device/source; configure HOST_IP and HOST_INTERFACE')
            if not interface or interface == dev:
                candidates.add((dev, src))
        candidates &= pairs
    if len(candidates) != 1:
        raise ConfigError('Host address/interface is missing or ambiguous; configure HOST_IP and HOST_INTERFACE')
    interface_selected, host_selected = candidates.pop()
    if interface_selected in virtual and not (host and interface):
        raise ConfigError('Virtual/container/VPN interface requires explicit HOST_IP and HOST_INTERFACE')
    ip = ipaddress.IPv4Address(host_selected)
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
        raise ConfigError('HOST_IP must be a routable host address')
    result = (host_selected, interface_selected)
    return (*result, prefixes[(interface_selected, host_selected)]) if with_subnet else result


def uuid_value(value, field):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, AttributeError):
        raise ConfigError(field + ' must be a UUID') from None
    return value

def load_config(root=ROOT, environ=None, home=None, network=False, require=(), query=command_json):
    environ = os.environ if environ is None else environ
    home = Path(home if home is not None else environ.get('HOME', str(Path.home())))
    root = safe_path(str(root), Path('/'), home)
    cfg = dict(DEFAULTS)
    if environ.get('P0_RUNTIME_CONFIG'):
        cfg.update(read_snapshot(safe_path(environ['P0_RUNTIME_CONFIG'], root, home)))
    else:
        cfg.update(read_env(root / 'config/global.env'))
        cfg.update({key: environ[key] for key in DEFAULTS if key in environ})
    if any(not isinstance(value, str) or '\x00' in value for value in cfg.values()):
        raise ConfigError('Configuration values must be text without NUL characters')
    if cfg['REPO_ROOT']:
        selected = safe_path(cfg['REPO_ROOT'], root, home)
        if selected != root:
            raise ConfigError('REPO_ROOT must match the running source checkout')
    cfg['REPO_ROOT'] = str(root)
    if not (root / 'config/scenarios.json').is_file():
        raise ConfigError('Repository lacks config/scenarios.json')
    for field in ['RUNTIME_DIR', 'ACTIONS_LOG_PATH', 'KUBECONFIG_PATH', 'OSM_KUBECONFIG_PATH',
                  'SRSRAN_BINARY', 'IMS_CONFIG_PATH', 'IMS_MYSQL_DATA_PATH',
                  'OSM_CA_CERT_PATH', 'SUBSCRIBER_DATABASE_INPUT']:
        fallback = {'RUNTIME_DIR': str(root / '.runtime'), 'KUBECONFIG_PATH': str(home / '.kube/config')}
        if field == 'OSM_KUBECONFIG_PATH':
            fallback[field] = cfg['KUBECONFIG_PATH']
        if field == 'ACTIONS_LOG_PATH':
            fallback[field] = str(Path(cfg['RUNTIME_DIR']) / 'actions.log')
        value = cfg[field] or fallback.get(field, '')
        cfg[field] = str(safe_path(value, root, home)) if value else ''
    for field in ['SRSRAN_BINARY', 'IMS_CONFIG_PATH', 'IMS_MYSQL_DATA_PATH']:
        if cfg[field] and not Path(cfg[field]).exists():
            raise ConfigError(field + ' must reference an existing external dependency')
    state = Path(cfg['RUNTIME_DIR'])
    if state.exists() and (not state.is_dir() or stat.S_IMODE(state.stat().st_mode) & 0o022):
        raise ConfigError('RUNTIME_DIR must be a directory without group/world write permission')
    if state.is_relative_to(root) and not state.is_relative_to(root / '.runtime'):
        raise ConfigError('In-checkout runtime state must be under .runtime/')
    cfg['ACTIVE_STATE_PATH'] = str(safe_path(str(state / 'active-ran.yaml'), root, home))
    for field in ['POD_CIDR', 'SERVICE_CIDR', 'UE_CIDR', 'EDGE_CIDR']:
        try:
            ipaddress.IPv4Network(cfg[field], strict=True)
        except ValueError:
            raise ConfigError(field + ' must be a valid, aligned IPv4 CIDR') from None
    ranges = [(k, ipaddress.ip_network(cfg[k])) for k in ['POD_CIDR', 'SERVICE_CIDR', 'UE_CIDR', 'EDGE_CIDR']]
    # IMS is part of the canonical core topology, not an alias for the UE/edge ranges.
    ranges.append(('canonical IMS', ipaddress.ip_network('10.46.0.0/16')))
    for index, (key, value) in enumerate(ranges):
        for other, subnet in ranges[index + 1:]:
            if value.overlaps(subnet):
                raise ConfigError('Conflicting network ranges: ' + key + ' and ' + other)
    ports = []
    for field in ['INGRESS_HTTP_NODEPORT', 'OSM_HTTPS_PORT', 'PROMETHEUS_NODEPORT', 'GRAFANA_NODEPORT', 'DASHBOARD_PORT', 'RANCHER_PORT']:
        if not cfg[field].isdigit() or not 1 <= int(cfg[field]) <= 65535:
            raise ConfigError(field + ' must be a port between 1 and 65535')
        if field.endswith('NODEPORT') and not 30000 <= int(cfg[field]) <= 32767:
            raise ConfigError(field + ' must be in the default Kubernetes NodePort range')
        ports.append(int(cfg[field]))
    if len(set(ports)) != len(ports):
        raise ConfigError('Configured host ports conflict')
    if cfg['DEPLOYMENT_PROFILE'] not in ('rfsim', 'usrp'):
        raise ConfigError('DEPLOYMENT_PROFILE must be rfsim or usrp')
    if not cfg['OSM_NAMESPACE']:
        raise ConfigError('OSM_NAMESPACE must not be empty')
    for field in ['OSM_NAMESPACE', 'OSM_PROJECT_NAMESPACE', 'OSM_CLUSTER_NAME']:
        if cfg[field] and (len(cfg[field]) > 63 or not DNS.fullmatch(cfg[field])):
            raise ConfigError(field + ' must be a Kubernetes namespace name')
    for field in ['OSM_PROJECT_ID', 'OSM_VIM_ACCOUNT_ID', 'OSM_K8S_CLUSTER_ID']:
        if cfg[field]:
            uuid_value(cfg[field], field)
    if cfg['HOST_IP']:
        try:
            ipaddress.IPv4Address(cfg['HOST_IP'])
        except ValueError:
            raise ConfigError('HOST_IP must be a valid IPv4 address') from None
    for field in ['BENCH_IPERF_HOST', 'BENCH_PING_HOST']:
        if cfg[field]:
            try:
                ipaddress.ip_address(cfg[field])
            except ValueError:
                raise ConfigError(field + ' must be an IP address') from None
    if not cfg['BENCH_IPERF_PORT'].isdigit() or not 1 <= int(cfg['BENCH_IPERF_PORT']) <= 65535:
        raise ConfigError('BENCH_IPERF_PORT must be between 1 and 65535')
    if cfg['FLEXRIC_ADDRESS']:
        try:
            ipaddress.ip_address(cfg['FLEXRIC_ADDRESS'])
        except ValueError:
            raise ConfigError('FLEXRIC_ADDRESS must be an IP address') from None
    if network is True or (network == 'auto' and not cfg['OSM_HOST']):
        cfg['HOST_IP'], cfg['HOST_INTERFACE'], host_subnet = discover_network(cfg['HOST_IP'], cfg['HOST_INTERFACE'], query, with_subnet=True)
        if any(host_subnet.overlaps(subnet) for _, subnet in ranges):
            raise ConfigError('Host connected subnet conflicts with a configured cluster/UE/IMS/edge range')
    if cfg['HOST_INTERFACE'] and not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', cfg['HOST_INTERFACE']):
        raise ConfigError('Invalid HOST_INTERFACE name')
    if cfg['HOST_IP']:
        cfg['OSM_BASE_DOMAIN'] = cfg['OSM_BASE_DOMAIN'] or cfg['HOST_IP'] + '.nip.io'
        cfg['OSM_HOST'] = cfg['OSM_HOST'] or 'https://gui.' + cfg['OSM_BASE_DOMAIN'] + ':' + cfg['OSM_HTTPS_PORT']
        cfg['GRAFANA_URL'] = cfg['GRAFANA_URL'] or 'http://' + cfg['HOST_IP'] + ':' + cfg['GRAFANA_NODEPORT']
        cfg['RANCHER_URL'] = cfg['RANCHER_URL'] or 'https://' + cfg['HOST_IP'] + ':' + cfg['RANCHER_PORT'] + '/dashboard/'
    cfg['PROMETHEUS_URL'] = cfg['PROMETHEUS_URL'] or 'http://127.0.0.1:' + cfg['PROMETHEUS_NODEPORT']
    cfg['DASHBOARD_URL'] = cfg['DASHBOARD_URL'] or 'http://127.0.0.1:' + cfg['DASHBOARD_PORT']
    for field in ['OSM_HOST', 'PROMETHEUS_URL', 'DASHBOARD_URL', 'GRAFANA_URL', 'RANCHER_URL']:
        if not cfg[field]:
            continue
        try:
            parsed = urlsplit(cfg[field])
            _ = parsed.port
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not (parsed.username or parsed.password or parsed.query or parsed.fragment) and not any(c in cfg[field] for c in '\r\n\x00<>"\x27` ')
        except ValueError:
            valid = False
        if valid:
            try:
                ipaddress.ip_address(parsed.hostname)
            except ValueError:
                valid = all(len(label) <= 63 and DNS.fullmatch(label.lower()) for label in parsed.hostname.split('.'))
        if not valid or (field == 'OSM_HOST' and (parsed.scheme != 'https' or parsed.path not in ('', '/'))):
            raise ConfigError(field + ' must be a safe HTTP(S) URL without embedded credentials')
    if cfg['OSM_BASE_DOMAIN'] and not re.fullmatch(r'[A-Za-z0-9.-]+', cfg['OSM_BASE_DOMAIN']):
        raise ConfigError('Invalid OSM_BASE_DOMAIN')
    for field in ['KUBELET_MAX_PODS', 'TAC', 'SST', 'LAYER3_CHECK_INTERVAL_SECONDS', 'LAYER3_COOLDOWN_SECONDS']:
        if not cfg[field].isdigit() or int(cfg[field]) < 1:
            raise ConfigError(field + ' must be a positive integer')
    if not re.fullmatch(r'\d{3}', cfg['MCC']) or not re.fullmatch(r'\d{2,3}', cfg['MNC']) or not re.fullmatch(r'[0-9a-fA-F]{6}', cfg['SD']):
        raise ConfigError('Invalid PLMN/slice configuration')
    if any(cfg[field] != DEFAULTS[field] for field in ['UE_CIDR', 'EDGE_CIDR']):
        raise ConfigError('Changing UE/edge CIDRs requires reconciled core/F-RAN profiles; overrides are not wired')
    if any(cfg[field] != DEFAULTS[field] for field in ['MCC', 'MNC', 'TAC', 'SST', 'SD', 'DNN']):
        raise ConfigError('Changing PLMN/slice/DNN requires a reconciled canonical chart profile; global overrides are not wired')
    try:
        valid_bler = 0 <= float(cfg['LAYER3_BLER_THRESHOLD']) <= 1
    except ValueError:
        valid_bler = False
    if not valid_bler:
        raise ConfigError('LAYER3_BLER_THRESHOLD must be between 0 and 1')
    from scenario_registry import load_registry
    registry = load_registry(root)
    failover = registry['aliases'].get(cfg['LAYER3_FAILOVER_SCENARIO'], cfg['LAYER3_FAILOVER_SCENARIO'])
    if failover and not any(s['key'] == failover for s in registry['scenarios']):
        raise ConfigError('LAYER3_FAILOVER_SCENARIO must name a registered scenario')
    if 'watcher' in require and not any(s['key'] == failover and s['generation_status'] == 'ready' for s in registry['scenarios']):
        raise ConfigError('Watcher requires an explicitly configured ready LAYER3_FAILOVER_SCENARIO')
    if 'kubernetes' in require:
        for key in ['KUBECONFIG_PATH', 'OSM_KUBECONFIG_PATH']:
            if not Path(cfg[key]).is_file():
                raise ConfigError(key + ' must reference an existing regular file')
    if 'osm' in require:
        for field in ['OSM_HOST', 'OSM_USER', 'OSM_PASSWORD', 'OSM_PROJECT']:
            if not cfg[field]:
                raise ConfigError('Missing required ' + field)
    if 'install' in require:
        if cfg['POD_CIDR'] != DEFAULTS['POD_CIDR']:
            raise ConfigError('The bundled Flannel installation supports only the canonical POD_CIDR; custom CNI rendering is deferred')
        if cfg['OSM_KUBECONFIG_PATH'] != cfg['KUBECONFIG_PATH'] and not Path(cfg['OSM_KUBECONFIG_PATH']).is_file():
            raise ConfigError('A separate OSM_KUBECONFIG_PATH must already exist')
        for field in ['GRAFANA_ADMIN_PASSWORD', 'RANCHER_BOOTSTRAP_PASSWORD']:
            if not cfg[field]:
                raise ConfigError('Missing required ' + field)
    if 'rf' in require and cfg['DEPLOYMENT_PROFILE'] == 'usrp':
        if not cfg['RF_DEVICE_ARGS'] or not cfg['SRSRAN_BINARY'] or not Path(cfg['SRSRAN_BINARY']).is_file():
            raise ConfigError('USRP requires RF_DEVICE_ARGS and an existing SRSRAN_BINARY')
    return MappingProxyType(cfg)

def unique_identity(entries, name, explicit_id, kind):
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ConfigError('Invalid structured OSM ' + kind + ' listing')
    if not name and not explicit_id:
        raise ConfigError('Configure an expected OSM ' + kind + ' name or explicit UUID')
    matches = [entry for entry in entries if (not name or entry.get('name') == name)
               and (not explicit_id or entry.get('_id', entry.get('id')) == explicit_id)]
    if len(matches) != 1:
        raise ConfigError('Missing or ambiguous OSM ' + kind + ' identity')
    result = dict(matches[0])
    result['_id'] = uuid_value(result.get('_id', result.get('id')), kind)
    return result

def discover_context(cfg, catalog, namespace_query=None, require_vim=True):
    """Explicit read-only API discovery. Never called by import or local validation."""
    from osm_catalog import parse_response
    project = unique_identity(parse_response(catalog.request('GET', '/admin/v1/projects')),
                              cfg['OSM_PROJECT'], cfg['OSM_PROJECT_ID'], 'project')
    context = {'project_id': project['_id']}
    if cfg['OSM_HOST']:
        context['osm_host'] = cfg['OSM_HOST'].rstrip('/')
    if cfg['OSM_K8S_CLUSTER_ID']:
        cluster = unique_identity(parse_response(catalog.request('GET', '/admin/v1/k8sclusters')),
                                  '', cfg['OSM_K8S_CLUSTER_ID'], 'Kubernetes cluster')
        context['k8s_cluster_id'] = cluster['_id']
    if require_vim:
        vim = unique_identity(parse_response(catalog.request('GET', '/admin/v1/vim_accounts')),
                              cfg['OSM_VIM_NAME'], cfg['OSM_VIM_ACCOUNT_ID'], 'VIM')
        context['vim_id'] = vim['_id']
        namespace = cfg['OSM_PROJECT_NAMESPACE'] or project['_id']
        if namespace_query is None:
            raise ConfigError('Kubernetes namespace verification is required')
        listing = namespace_query()
        items = listing.get('items') if isinstance(listing, dict) else None
        if not isinstance(items, list) or any(not isinstance(e, dict) for e in items):
            raise ConfigError('Invalid structured namespace listing')
        metadata = [entry.get('metadata') for entry in items]
        if any(not isinstance(e, dict) for e in metadata):
            raise ConfigError('Invalid structured namespace metadata')
        matches = [entry for entry in metadata if entry.get('name') == namespace]
        if len(matches) != 1:
            raise ConfigError('OSM namespace is missing or ambiguous; configure OSM_PROJECT_NAMESPACE')
        if not DNS.fullmatch(namespace) or len(namespace) > 63:
            raise ConfigError('Invalid discovered namespace')
        systems = [entry for entry in metadata if entry.get('name') == 'kube-system']
        if len(systems) != 1:
            raise ConfigError('Cannot establish Kubernetes cluster identity from kube-system UID')
        context['cluster_uid'] = uuid_value(systems[0].get('uid'), 'Kubernetes cluster UID')
        context['namespace_uid'] = uuid_value(matches[0].get('uid'), 'namespace UID')
        context['namespace'] = namespace
        context['repo_root'] = cfg.get('REPO_ROOT', str(ROOT))
        context['workload_kubeconfig'] = cfg.get('OSM_KUBECONFIG_PATH', '')
        context['deployment_profile'] = cfg.get('DEPLOYMENT_PROFILE', 'rfsim')
        if cfg['OSM_K8S_CLUSTER_ID']:
            credentials = cluster.get('credentials')
            if isinstance(credentials, str):
                import yaml
                try:
                    credentials = yaml.safe_load(credentials)
                except yaml.YAMLError:
                    raise ConfigError('Invalid OSM cluster credentials structure') from None
            if not isinstance(credentials, dict):
                raise ConfigError('OSM cluster association requires structured kubeconfig credentials')
            users = discovery_entries(credentials.get('users', []), 'OSM cluster kubeconfig user')
            for user in users:
                auth = user.get('user')
                if not isinstance(auth, dict) or any(k in auth for k in ('exec', 'auth-provider', 'tokenFile', 'client-certificate', 'client-key')):
                    raise ConfigError('OSM cluster association requires inline credentials without executable plugins or host file references')
            for entry in discovery_entries(credentials.get('clusters', []), 'OSM kubeconfig cluster'):
                transport = entry.get('cluster')
                if not isinstance(transport, dict) or 'certificate-authority' in transport or transport.get('insecure-skip-tls-verify'):
                    raise ConfigError('OSM cluster association requires verified TLS and inline certificate data')
            # Query the selected OSM cluster credentials in an owner-only temporary file.
            import tempfile, yaml
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'cluster.json'
                path.write_text(json.dumps(credentials))
                path.chmod(0o600)
                selected = command_json(['kubectl', '--kubeconfig', str(path), 'get', 'namespace', 'kube-system', '-o', 'json'])
            meta = selected.get('metadata') if isinstance(selected, dict) else None
            if not isinstance(meta, dict) or meta.get('uid') != context['cluster_uid']:
                raise ConfigError('Selected OSM cluster does not match the workload cluster')
    return context

def kubernetes_json(cfg, args):
    return command_json(['kubectl', '--kubeconfig', cfg['OSM_KUBECONFIG_PATH'], *args])

def discover_service(items, name, namespace, port_name):
    """Resolve a uniquely identified Service and named port from structured data."""
    if not isinstance(items, dict):
        raise ConfigError('Invalid structured service listing')
    entries = discovery_entries(items.get('items'), 'service')
    if any(not isinstance(e.get('metadata'), dict) or not isinstance(e.get('spec'), dict) for e in entries):
        raise ConfigError('Invalid structured service metadata/spec')
    matches = [e for e in entries if e.get('metadata', {}).get('name') == name
               and e.get('metadata', {}).get('namespace') == namespace]
    if len(matches) != 1:
        raise ConfigError('Missing or ambiguous Kubernetes service')
    spec = matches[0].get('spec', {})
    ports = [p for p in discovery_entries(spec.get('ports'), 'service port') if p.get('name') == port_name]
    if len(ports) != 1:
        raise ConfigError('Missing or ambiguous service port')
    try:
        address = str(ipaddress.ip_address(spec['clusterIP']))
        port = int(ports[0]['port'])
        if not 1 <= port <= 65535:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ConfigError('Invalid service address/port') from None
    return address, port

def main():
    parser = argparse.ArgumentParser(description='Local configuration validation; no cluster/OSM requests')
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--auto-network', action='store_true', help='Discover only a missing derived OSM endpoint')
    parser.add_argument('--require', action='append', choices=['kubernetes', 'osm', 'install', 'rf', 'watcher'], default=[])
    parser.add_argument('--snapshot', help='Write an owner-only configuration snapshot')
    parser.add_argument('--shell-records', action='store_true', help='NUL-delimited exports including secrets; consume privately')
    args = parser.parse_args()
    try:
        cfg = load_config(network=True if args.network else 'auto' if args.auto_network else False, require=args.require)
        if args.snapshot:
            write_snapshot(args.snapshot, cfg)
        if args.shell_records:
            import sys
            for key, value in cfg.items():
                sys.stdout.buffer.write(key.encode() + b'\0' + value.encode() + b'\0')
        else:
            print('Configuration valid (values and secrets omitted)')
    except (ConfigError, OSError, ValueError):
        # ConfigError text names fields but never reproduces values.
        import sys
        error = sys.exc_info()[1]
        parser.exit(1, 'ERROR: ' + (str(error) if isinstance(error, ConfigError) else 'Cannot read runtime configuration') + '\n')

if __name__ == '__main__':
    main()
