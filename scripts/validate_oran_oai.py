"""Read-only canonical O-RAN acceptance; never deploys or changes runtime state.

Run without --e2 for the initial NGAP/F1 milestone. With --e2, accept an
established E2 association OR the OAI agent's explicit SETUP-REQUEST tx log.
Both modes require a stable, ready CU/DU pair with zero container restarts.
--ric-address prints the actual FlexRIC Service IPv4 for the optional profile.
UE/PDU, E2 Setup Response, subscriptions and xApps are outside this milestone.
"""
import argparse
import ipaddress
import json
import re
import subprocess
import sys
import time

from runtime_images import chart_values, ROOT
from scenario_registry import load_registry, select_scenarios

PLUGINS = {'libkpm_sm.so', 'librc_sm.so'}
SM_DIR = '/tmp/flexric-sm-kpm-rc/'


def service_ipv4(service):
    address = service.get('spec', {}).get('clusterIP', '')
    parsed = ipaddress.ip_address(address)
    if parsed.version != 4 or parsed.is_unspecified or parsed.is_multicast:
        raise ValueError('FlexRIC requires a concrete Service IPv4 address')
    return str(parsed)


def association(table, port, peer):
    """Require ST=3, the remote port and exact peer address (not a substring)."""
    lines = table.splitlines()
    if not lines:
        return False
    header = lines[0].split()
    if not all(key in header for key in ('ST', 'RPORT', 'RADDRS')):
        return False
    for line in lines[1:]:
        fields = line.split()
        try:
            # /proc/net/sctp/assocs permits multiple local/remote addresses.
            remotes = fields[fields.index('<->') + 1:]
            if (fields[header.index('ST')] == '3' and
                    fields[header.index('RPORT')] == str(port) and
                    peer in {a.lstrip('*') for a in remotes}):
                return True
        except (ValueError, IndexError):
            continue
    return False


def f1_cu_association(table, peer):
    # The CU listens on 38472; the DU uses an ephemeral source port.
    lines = table.splitlines()
    if not lines:
        return False
    header = lines[0].split()
    if 'LPORT' not in header or 'RPORT' not in header:
        return False
    swapped = [lines[0]]
    for line in lines[1:]:
        fields = line.split()
        try:
            local, remote = header.index('LPORT'), header.index('RPORT')
            fields[local], fields[remote] = fields[remote], fields[local]
        except IndexError:
            continue
        swapped.append(' '.join(fields))
    return association('\n'.join(swapped), 38472, peer)


def select_pair(pods):
    pair = {}
    for role in ('cu', 'du'):
        identity = 'oran-oai-' + role
        matches = [p for p in pods.get('items', [])
                   if p.get('metadata', {}).get('labels', {}).get('app') == identity]
        if len(matches) != 1:
            raise ValueError('Require exactly one canonical ' + identity + ' pod')
        pair[role] = matches[0]
    return pair


def evaluate(pair, observations, amf_ip, cu_ip, ric_ip=None):
    scenario = select_scenarios(load_registry(), ['oran-oai'])[0]
    checks = {}
    for role in ('cu', 'du'):
        pod = pair[role]
        metadata, status = pod.get('metadata', {}), pod.get('status', {})
        labels = metadata.get('labels', {})
        identity = 'oran-oai-' + role
        chart = next(c for c in scenario['charts'] if c['kdu'] == role)
        expected_image = chart_values(ROOT, chart)['image']['reference']
        containers = pod.get('spec', {}).get('containers', [])
        checks[role + '_canonical_identity'] = (
            metadata.get('name', '').startswith(identity + '-') and
            labels.get('app') == identity and labels.get('ran-type') == 'oran' and
            labels.get('component') == role and not metadata.get('deletionTimestamp') and
            len(containers) == 1 and containers[0].get('name') == role and
            containers[0].get('image') == expected_image)
        states = status.get('containerStatuses', [])
        checks[role + '_running_ready_zero_restarts'] = (
            status.get('phase') == 'Running' and len(states) == 1 and
            states[0].get('name') == role and states[0].get('ready') is True and
            states[0].get('restartCount') == 0 and 'running' in states[0].get('state', {}))
        evidence = observations[role]
        checks[role + '_no_fatal_log'] = not re.search(
            r'Assertion .*failed|assertion failed|Segmentation fault|\bFATAL\b',
            evidence.get('logs', ''), re.I)
        config = evidence.get('config', '')
        if ric_ip is None:
            checks[role + '_e2_disabled'] = 'e2_agent' not in config
        else:
            checks[role + '_e2_config'] = (
                re.search(r'near_ric_ip_addr\s*=\s*"' + re.escape(ric_ip) + r'"', config) is not None and
                'sm_dir = "' + SM_DIR + '"' in config)
            checks[role + '_only_kpm_rc_exposed'] = set(evidence.get('plugin_files', '').split()) == PLUGINS
            # Listing files does not prove dlopen; process memory maps do.
            maps = evidence.get('maps', '')
            loaded = set(re.findall(re.escape(SM_DIR) + r'(lib[^/\s]+\.so)', maps))
            full_directory = set(re.findall(r'/usr/local/lib/flexric/(lib[^/\s]+\.so)', maps))
            checks[role + '_kpm_rc_loaded'] = loaded == PLUGINS and not full_directory
    checks['rfsim'] = '--rfsim' in observations['du'].get('cmdline', '').split()
    checks['ngap_cu_amf'] = association(observations['cu']['assocs'], 38412, amf_ip)
    checks['ngap_setup_response'] = re.search(r'Received\s+NG\s*Setup\s*Response\s+from\s+AMF',
                                             observations['cu'].get('logs', ''), re.I) is not None
    checks['f1_cu_du'] = f1_cu_association(observations['cu']['assocs'], pair['du']['status'].get('podIP', ''))
    checks['f1_du_cu'] = association(observations['du']['assocs'], 38472, cu_ip)
    checks['f1_setup_exchange'] = (
        re.search(r'Received\s+F1\s*Setup\s*Request', observations['cu'].get('logs', ''), re.I) is not None and
        re.search(r'received\s+F1\s*Setup\s*Response', observations['du'].get('logs', ''), re.I) is not None)
    if ric_ip is not None:
        associated = any(association(observations[r]['assocs'], 36421, ric_ip) for r in ('cu', 'du'))
        # This is the actual OAI transmit marker, missed by the historical helper.
        # Do not accept generic "E2 Setup" text, an attempt, or a received request.
        transmitted = any(re.search(r'\[E2-AGENT\]:\s*E2 SETUP-REQUEST tx\b',
                                    observations[r].get('logs', '')) for r in ('cu', 'du'))
        checks['e2_association_or_setup_request_tx'] = associated or transmitted
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--amf-namespace', help='Namespace of amf-ngap-stable; defaults to --namespace')
    parser.add_argument('--e2', action='store_true')
    parser.add_argument('--ric-address', action='store_true', help='Read Service IPv4 only; no acceptance run')
    parser.add_argument('--stability-seconds', type=int, default=30)
    args = parser.parse_args(argv)
    if not 1 <= args.stability_seconds <= 60:
        parser.error('--stability-seconds must be between 1 and 60')
    base = ['kubectl', '--kubeconfig', args.kubeconfig, '--request-timeout=10s', '-n', args.namespace]

    def run(command, namespace=None):
        target = base if namespace is None else base[:-1] + [namespace]
        return subprocess.run(target + command, capture_output=True, text=True, check=True, timeout=20).stdout

    def get(kind, name=None, namespace=None):
        return json.loads(run(['get', kind] + ([name] if name else []) + ['-o', 'json'], namespace))

    def collect(pair):
        evidence = {}
        for role, pod in pair.items():
            name = pod['metadata']['name']
            prefix = ['exec', name, '-c', role, '--']
            states = pod['status'].get('containerStatuses', [])
            started = next((s.get('state', {}).get('running', {}).get('startedAt')
                            for s in states if s.get('name') == role), None)
            if not started:
                raise ValueError('Canonical CU/DU container is not Running')
            evidence[role] = {
                'assocs': run(prefix + ['cat', '/proc/net/sctp/assocs']),
                'config': run(prefix + ['cat', '/tmp/conf/' + role + '.conf']),
                'cmdline': run(prefix + ['cat', '/proc/1/cmdline']).replace('\x00', ' '),
                'logs': run(['logs', name, '-c', role, '--timestamps', '--since-time=' + started]),
            }
            if args.e2:
                evidence[role]['maps'] = run(prefix + ['cat', '/proc/1/maps'])
                evidence[role]['plugin_files'] = run(prefix + ['ls', '-1', SM_DIR])
        return evidence

    try:
        ric_ip = service_ipv4(get('service', 'flexric')) if args.e2 or args.ric_address else None
        if args.ric_address:
            print(ric_ip)
            return 0
        amf_ip = service_ipv4(get('service', 'amf-ngap-stable', args.amf_namespace))
        cu_ip = service_ipv4(get('service', 'oran-oai-cu'))
        first = select_pair(get('pods'))
        checks = evaluate(first, collect(first), amf_ip, cu_ip, ric_ip)
        if all(checks.values()):
            time.sleep(args.stability_seconds)
            second = select_pair(get('pods'))
            checks['same_pods_during_stability_window'] = all(
                first[r]['metadata'].get('uid') and
                first[r]['metadata']['uid'] == second[r]['metadata'].get('uid') for r in ('cu', 'du'))
            for key, passed in evaluate(second, collect(second), amf_ip, cu_ip, ric_ip).items():
                checks[key] = checks[key] and passed
        for key, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ') + key)
        accepted = all(checks.values())
        print('ORAN_OAI_' + ('E2' if args.e2 else 'RAN') + ('_ACCEPTED' if accepted else '_NOT_ACCEPTED'))
        return 0 if accepted else 1
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        # Raw container logs/config are never emitted by this helper.
        print('ORAN_OAI_NOT_ACCEPTED: required read-only runtime evidence unavailable', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
