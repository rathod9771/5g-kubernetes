"""Read-only split srsRAN health from pod readiness and kernel SCTP state."""
import argparse
import json
import subprocess
import sys


def established(text, port, remote_only=False):
    """Use named columns: ST is not SST/STY; address lists follow both ports."""
    lines = text.splitlines()
    if not lines:
        return False
    header = lines[0].split()
    try:
        state, local, remote = (header.index(key) for key in ('ST', 'LPORT', 'RPORT'))
    except ValueError:
        return False
    for line in lines[1:]:
        fields = line.split()
        if len(fields) <= max(state, local, remote):
            continue
        if fields[state] == '3' and (fields[remote] == str(port) or
                                   (not remote_only and fields[local] == str(port))):
            return True
    return False


def srsran_pods(pods):
    return [p for p in pods.get('items', []) if any(
        'srsran' in c.get('image', '').lower()
        for c in p.get('spec', {}).get('containers', []))]


def health(pods, read_assocs):
    selected = srsran_pods(pods)
    if not selected:
        return 'NOT_APPLICABLE', 'Use existing non-srsRAN health checks'
    roles = {'cu': [], 'du': []}
    for pod in selected:
        role = pod.get('metadata', {}).get('labels', {}).get('component')
        if role in roles:
            roles[role].append(pod)
    if not all(roles.values()):
        return 'DEGRADED', 'srsRAN CU and DU are both required'
    for pod in roles['cu'] + roles['du']:
        status = pod.get('status', {})
        if status.get('phase') != 'Running':
            return 'DOWN', 'srsRAN CU/DU is not Running'
        if pod.get('metadata', {}).get('deletionTimestamp') or not any(
                c.get('type') == 'Ready' and c.get('status') == 'True'
                for c in status.get('conditions', [])):
            return 'DEGRADED', 'srsRAN CU/DU is not Ready'
    for role, role_pods in roles.items():
        for pod in role_pods:
            try:
                table = read_assocs(pod, role)
            except (OSError, subprocess.SubprocessError):
                return 'DEGRADED', 'Cannot read srsRAN SCTP association state'
            if role == 'cu' and not established(table, 38412, remote_only=True):
                return 'DEGRADED', 'CU -> AMF requires RPORT=38412 and ST=3'
            if not established(table, 38472):
                return 'DEGRADED', 'CU and DU F1 require LPORT/RPORT=38472 and ST=3'
    return 'READY', 'CU/DU Running/Ready; NGAP RPORT=38412 ST=3; CU and DU F1 port=38472 ST=3'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--kubeconfig', required=True)
    args = parser.parse_args()
    def read(pod, role):
        containers = pod['spec']['containers']
        candidates = [c['name'] for c in containers if 'srsran' in c.get('image', '').lower()]
        if len(candidates) != 1:
            raise OSError('Ambiguous srsRAN container')
        result = subprocess.run(['kubectl', '--kubeconfig', args.kubeconfig, 'exec',
                                 '-n', args.namespace, pod['metadata']['name'], '-c', candidates[0],
                                 '--', 'cat', '/proc/net/sctp/assocs'],
                                capture_output=True, text=True, timeout=15, check=True)
        return result.stdout
    try:
        result, evidence = health(json.load(sys.stdin), read)
    except (ValueError, KeyError, TypeError, AttributeError):
        result, evidence = 'DEGRADED', 'Cannot inspect RAN pod readiness safely'
    print(result)
    print(evidence)


if __name__ == '__main__':
    main()
