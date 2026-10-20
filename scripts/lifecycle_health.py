"""Post-update RAN health gate. Pod readiness alone is never accepted.

Pure evaluation over observations gathered read-only elsewhere:
  {kdu: {'pods': [pod...], 'assocs': '<proc/net/sctp/assocs>', 'logs': [(epoch, line), ...]}}
"""
import re

from ran_health import established

RAN_ROLES = ('cu', 'du', 'macro', 'small')
NG_SETUP = re.compile(r'Received\s+NG\s*Setup\s*Response\s+from\s+AMF', re.I)
F1_REQUEST = re.compile(r'Received\s+F1\s*Setup\s*Request', re.I)
F1_RESPONSE = re.compile(r'received\s+F1\s*Setup\s*Response', re.I)


def _ready(pod):
    status = pod.get('status', {})
    return (status.get('phase') == 'Running' and not pod.get('metadata', {}).get('deletionTimestamp') and
            any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])) and
            all(c.get('restartCount', 0) == 0 for c in status.get('containerStatuses', [])))


def _logged(observation, pattern, since):
    return any(stamp >= since and pattern.search(line) for stamp, line in observation.get('logs', []))


def evaluate(entry, observations, since):
    """Return (healthy, detail) for the scenario's KDUs after an update at `since`."""
    kdus = [c['kdu'] for c in entry['charts']]
    for kdu in kdus:
        pods = observations.get(kdu, {}).get('pods', [])
        if len(pods) != 1:
            return False, kdu + ': expected exactly one pod (old and new pods must not overlap)'
        if not _ready(pods[0]):
            return False, kdu + ': pod not Running/Ready or restarted'
    ran = [k for k in kdus if k in RAN_ROLES]
    if 'cu' not in ran or len(ran) < 2:
        return False, 'Scenario has no CU/DU pair to verify'
    cu = observations['cu']
    if not established(cu.get('assocs', ''), 38412, remote_only=True):
        return False, 'CU has no established NGAP association (RPORT 38412)'
    oai = entry.get('implementation') == 'OAI'
    if oai and not _logged(cu, NG_SETUP, since):
        return False, 'CU logged no NG Setup Response after the update'
    for du in (k for k in ran if k != 'cu'):
        if not established(observations[du].get('assocs', ''), 38472):
            return False, du + ': no established F1 association (port 38472)'
        if oai and not _logged(observations[du], F1_RESPONSE, since):
            return False, du + ': logged no F1 Setup Response after the update'
    if not established(cu.get('assocs', ''), 38472):
        return False, 'CU has no established F1 association (port 38472)'
    if oai and not _logged(cu, F1_REQUEST, since):
        return False, 'CU logged no F1 Setup Request after the update'
    return True, 'Pods ready without restarts; NGAP and F1 established' + (' with post-update setup logs' if oai else '')
