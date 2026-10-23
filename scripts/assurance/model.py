"""Bounded, secret-free operational evidence and normalized alarm contracts."""
import hashlib
import json
import math
from datetime import datetime

SEVERITIES = ('CRITICAL', 'MAJOR', 'MINOR', 'WARNING', 'INFO')
PROTECTED_NS = frozenset(('2c128db3-e27e-4dbe-8854-17751ba17680',
                          '1b1cb300-b571-43f2-a567-6540895d7d0f'))
EVIDENCE_KEYS = frozenset(('pod_ready', 'f1_association', 'ngap_association',
    'f1_setup', 'ng_setup', 'cell_active', 'rrc_established', 'ue_registered',
    'pdu_established', 'user_plane_measured', 'core_healthy', 'osm_trustworthy',
    'capacity_sufficient', 'ownership_verified', 'host_pressure', 'service_degraded',
    'fog_latency', 'operation_failed', 'node_ready', 'metric_present',
    'registration_failures', 'pdu_failures', 'packet_loss', 'protected_state', 'configuration_failure',
    'monitoring_fresh', 'resource_uid', 'operation_id', 'kdu_id', 'vnf_id', 'namespace_uid', 'operation_started_at', 'operation_state_entered_at'))


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError('Timestamp requires timezone')
        value = value.timestamp()
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid timestamp')
    return float(value)


def identity(value, default='unknown'):
    if value is None:
        return default
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid operational identity')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def normalize(raw, now, *, simulation=False):
    """Caller chooses provenance; untrusted payload cannot choose a sandbox."""
    if not isinstance(raw, dict):
        raise ValueError('Alarm must be an object')
    if not simulation and raw.get('provenance', 'LIVE') != 'LIVE':
        raise ValueError('Synthetic event cannot enter production engine')
    labels = raw.get('labels', {})
    if not isinstance(labels, dict):
        raise ValueError('Labels must be an object')
    kind = identity(raw.get('alarm_type', labels.get('alertname')))
    severity = str(raw.get('severity', labels.get('severity', 'WARNING'))).upper()
    if severity not in SEVERITIES:
        raise ValueError('Invalid severity')
    at = timestamp(raw.get('timestamp', raw.get('startsAt', now)))
    if at > now + 30:
        raise ValueError('Future event timestamp')
    observations = raw.get('observations', {})
    if not isinstance(observations, dict) or len(observations) > 64:
        raise ValueError('Invalid observations')
    safe = {}
    for key, value in observations.items():
        if key not in EVIDENCE_KEYS:
            continue  # Never retain logs, annotations, subscriber IDs or credentials.
        if key in ('operation_started_at','operation_state_entered_at'):
            safe[key]=None if value is None else timestamp(value)
            if safe[key] is not None and safe[key]>now+30:raise ValueError('Future operation evidence')
        elif key=='namespace_uid':
            safe[key]=None if value is None else identity(value)
        elif value is None or type(value) is bool:
            safe[key] = value
        elif type(value) in (float, int) and math.isfinite(value):
            safe[key] = value
        elif key in ('resource_uid', 'operation_id', 'kdu_id', 'vnf_id'):
            safe[key] = identity(value)
        else:
            raise ValueError('Evidence must be a boolean or finite measurement')
    status = raw.get('status', 'firing')
    if status not in ('firing', 'resolved'):
        raise ValueError('Invalid alert status')
    alarm = dict(alarm_type=kind, severity=severity,
        source=identity(raw.get('source', 'alertmanager')),
        cluster=identity(raw.get('cluster', labels.get('cluster'))),
        namespace=identity(raw.get('namespace', labels.get('namespace'))),
        resource=identity(raw.get('resource', labels.get('pod', labels.get('node', labels.get('instance'))))),
        node=identity(raw.get('node', labels.get('node'))),
        service=identity(raw.get('service', labels.get('ns_id'))),
        vnf=safe.get('vnf_id', 'unknown'), kdu=safe.get('kdu_id', 'unknown'),
        timestamp=at, observations=safe, status=status,
        provenance='SIMULATED' if simulation else 'LIVE',
        confidence='SYNTHETIC_ASSERTION' if simulation else 'SOURCE_ASSERTION')
    alarm['key'] = digest([alarm[k] for k in ('alarm_type', 'cluster', 'namespace', 'resource', 'service', 'provenance')])
    # Source/fingerprint excluded so the same condition routed by two collectors deduplicates.
    alarm['delivery_id'] = digest([alarm['key'], at, status, safe])
    return alarm
