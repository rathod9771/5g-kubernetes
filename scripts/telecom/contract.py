"""Versioned provider declarations; validation is not platform verification."""
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

ROLES = {'gnb', 'cu', 'du', 'amf', 'smf', 'upf', 'nrf', 'ausf', 'udm', 'udr', 'pcf', 'nssf'}
INTERFACES = {'F1-C': 'SCTP', 'F1-U': 'GTP-U', 'N2': 'SCTP', 'N3': 'GTP-U',
              'N4': 'PFCP', 'SBI': 'HTTPS', 'E1': 'SCTP', 'N6': 'IP'}
REQUIRED = {'gnb': {'N2', 'N3'}, 'cu': {'F1-C', 'F1-U', 'N2', 'N3'},
            'du': {'F1-C', 'F1-U'}, 'amf': {'N2', 'SBI'},
            'smf': {'N4', 'SBI'}, 'upf': {'N3', 'N4', 'N6'}, 'nrf': {'SBI'}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def identity(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('An immutable sha256 digest is required')
    return value


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', value):
        raise ValueError('Invalid component identity')
    return value


def https(value):
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('A credential-free HTTPS source is required')
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            raise ValueError('Private source addresses are forbidden')
    except ValueError as error:
        if str(error) == 'Private source addresses are forbidden':
            raise
    if p.hostname in ('localhost', 'metadata.google.internal') or p.hostname.endswith(('.local', '.internal')):
        raise ValueError('Private source addresses are forbidden')
    return value


def validate(manifest):
    m = json.loads(canonical(manifest))
    if m.get('schema_version') != 1:
        raise ValueError('Unsupported capability schema')
    allowed = {'schema_version', 'name', 'version', 'roles', 'source', 'image', 'interfaces',
               'plmn', 'tac', 'slices', 'dnns', 'resources', 'hardware', 'monitoring', 'extensions'}
    if set(m) - allowed:
        raise ValueError('Unknown or secret-bearing manifest fields')
    slug(m.get('name'))
    if not isinstance(m.get('version'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}', m['version']):
        raise ValueError('A bounded provider version is required')
    roles = m.get('roles')
    if not isinstance(roles, list) or not roles or len(roles) > 16 or len(set(roles)) != len(roles):
        raise ValueError('Declare unique NF roles')
    for r in roles:
        if r not in ROLES and not (isinstance(r, str) and re.fullmatch(r'x-[a-z0-9-]{1,30}', r)):
            raise ValueError('Unknown role requires an x- extension declaration')
    s = m['source']; kind = s['kind']
    fields = {'github': {'kind', 'url', 'commit'}, 'local-git': {'kind', 'path', 'commit'},
              'oci': {'kind', 'repository', 'digest'}, 'helm': {'kind', 'url', 'digest'}}
    if kind not in fields or set(s) != fields[kind]:
        raise ValueError('Invalid source contract')
    if kind in ('github', 'helm'):
        https(s['url'])
    if kind == 'github' and (urlsplit(s['url']).hostname != 'github.com' or not re.fullmatch(r'[0-9a-f]{40}', s['commit'])):
        raise ValueError('GitHub source requires an exact commit and github.com URL')
    if kind == 'local-git' and (not isinstance(s['path'], str) or not re.fullmatch(r'[0-9a-f]{40}', s['commit'])):
        raise ValueError('Local Git source requires an exact commit')
    if kind == 'oci':
        if not re.fullmatch(r'[a-z0-9][a-z0-9./_-]{0,200}', s['repository']):
            raise ValueError('Invalid OCI repository')
        digest(s['digest'])
    if kind == 'helm':
        digest(s['digest'])
    image = m['image']
    if set(image) != {'repository', 'digest', 'platform'} or not re.fullmatch(r'[a-z0-9][a-z0-9./_-]{0,200}', image['repository']):
        raise ValueError('Invalid immutable image contract')
    digest(image['digest'])
    if image['platform'] not in ('linux/amd64', 'linux/arm64'):
        raise ValueError('Unsupported image platform')
    if kind == 'oci' and (s['digest'] != image['digest'] or s['repository'] != image['repository']):
        raise ValueError('Source/image identities disagree')
    interfaces = m['interfaces']
    if not isinstance(interfaces, dict) or len(interfaces) > 16:
        raise ValueError('Invalid interface contract')
    for name, interface in interfaces.items():
        if name not in INTERFACES or set(interface) != {'transport', 'endpoint', 'port', 'mtu'} or interface['transport'] != INTERFACES[name]:
            raise ValueError('Interface transport/schema mismatch')
        if not isinstance(interface['endpoint'], str) or not re.fullmatch(r'[a-zA-Z0-9:.-]{1,253}', interface['endpoint']):
            raise ValueError('Invalid interface endpoint')
        endpoint=interface['endpoint']
        try:
            address=ipaddress.ip_address(endpoint)
            if address.is_multicast or address.is_unspecified:
                raise ValueError('Advertised peer endpoint must be unicast')
        except ValueError:
            if ':' in endpoint or re.fullmatch(r'[0-9.]+',endpoint) or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',part) for part in endpoint.split('.')):
                raise ValueError('Invalid advertised IP/DNS endpoint')
        if (name=='N6' and interface['port'] is not None) or (name!='N6' and (type(interface['port']) is not int or not 1<=interface['port']<=65535)) or type(interface['mtu']) is not int or not 576 <= interface['mtu'] <= 9216:
            raise ValueError('Invalid interface port/MTU')
    if not set().union(*(REQUIRED.get(r, set()) for r in roles)) <= set(interfaces):
        raise ValueError('Required NF interfaces are missing')
    if not re.fullmatch(r'[0-9]{5,6}', m['plmn']) or type(m['tac']) is not int or not 0 <= m['tac'] <= 0xffffff:
        raise ValueError('Invalid PLMN/TAC')
    if not isinstance(m['slices'], list) or not 1 <= len(m['slices']) <= 16:
        raise ValueError('Declare bounded S-NSSAI list')
    for s in m['slices']:
        if set(s) != {'sst', 'sd'} or type(s['sst']) is not int or not 0 <= s['sst'] <= 255 or not re.fullmatch(r'[0-9A-Fa-f]{6}', s['sd']):
            raise ValueError('Invalid S-NSSAI')
    if not isinstance(m['dnns'], list) or not 1 <= len(m['dnns']) <= 16 or any(not isinstance(d, str) or not re.fullmatch(r'[a-z][a-z0-9.-]{0,62}', d) for d in m['dnns']):
        raise ValueError('Invalid DNN')
    if set(m['resources']) != {'cpu_millicores', 'memory_mib'}:
        raise ValueError('Declare CPU and memory budgets')
    for k, maximum in [('cpu_millicores', 64000), ('memory_mib', 262144)]:
        if type(m['resources'][k]) is not int or not 1 <= m['resources'][k] <= maximum:
            raise ValueError('Invalid resource budget')
    h = m['hardware']
    if set(h) != {'mode', 'devices', 'hugepages_mib'} or h['mode'] not in ('simulation', 'hardware') or not isinstance(h['devices'], list) or len(h['devices']) > 8 or any(not isinstance(d, str) or not re.fullmatch(r'[a-z0-9/_.-]{1,80}', d) for d in h['devices']) or type(h['hugepages_mib']) is not int or not 0 <= h['hugepages_mib'] <= 262144:
        raise ValueError('Invalid hardware contract')
    if h['mode'] == 'simulation' and (h['devices'] or h['hugepages_mib']):
        raise ValueError('Simulation cannot request hardware')
    if set(m['monitoring']) != {'required_metrics', 'optional_metrics'}:
        raise ValueError('Declare monitoring requirements')
    for values in m['monitoring'].values():
        if not isinstance(values, list) or len(values) > 32 or any(not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z_:][a-zA-Z0-9_:]{0,79}', v) for v in values):
            raise ValueError('Invalid metric names')
    if 'extensions' in m and (not isinstance(m['extensions'], dict) or len(canonical(m['extensions'])) > 4096):
        raise ValueError('Invalid extension declarations')
    # All fields are provider claims. Never accept embedded verification/credentials.
    if re.search(r'password|private.key|subscriber|kubeconfig|bearer|token', canonical(m).decode(), re.I):
        raise ValueError('Private fields are forbidden in capability declarations')
    return m


def confined(root, path):
    root = Path(root).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    if p.is_symlink() or not p.resolve().is_relative_to(root):
        raise ValueError('Source path escapes the approved workspace')
    return p.resolve()
