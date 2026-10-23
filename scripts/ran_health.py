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



def scoped_association(text, port, local_ip, peer_ip, remote_only=False, local_only=False):
    """Tri-state kernel evidence: malformed is None, valid absence is False.

    Named ports/state are reused from established(); address columns are also
    checked so an unrelated association never establishes target health.
    """
    import ipaddress
    if not isinstance(text,str) or len(text.encode())>65536:return None
    lines=text.splitlines()
    if not lines:return None
    header=lines[0].split()
    try:
        st,lp,rp,la,arrow,ra=(header.index(k) for k in ('ST','LPORT','RPORT','LADDRS','<->','RADDRS'))
        if not (st<lp<rp<la<arrow<ra):return None
        local_ip=ipaddress.ip_address(local_ip);peer_ip=ipaddress.ip_address(peer_ip)
        found=False;ambiguous=False
        if len(lines)>257:return None
        for line in lines[1:]:
            if not line.strip():continue
            fields=line.split()
            split=fields.index('<->')
            if split<=rp or len(fields)<=split+1:return None
            if not fields[st].isdigit() or not 0<=int(fields[st])<=10:return None
            if not all(fields[i].isdigit() and 0<int(fields[i])<=65535 for i in (lp,rp)):return None
            local={ipaddress.ip_address(x) for x in fields[rp+1:split]}
            remote={ipaddress.ip_address(x) for x in fields[split+1:]}
            if not local or not remote:return None
            if local_only and fields[lp]!=str(port):continue
            if local_ip in local and peer_ip not in remote and established(lines[0]+'\n'+line,port,remote_only):ambiguous=True
            if local_ip in local and peer_ip in remote and established(lines[0]+'\n'+line,port,remote_only):found=True
        return None if ambiguous and not found else found
    except (ValueError,IndexError,TypeError):return None


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
