"""Explainable hypotheses, proposals and fail-closed action governance."""
from .model import PROTECTED_NS

FORBIDDEN = frozenset(('core_restart','core_terminate','namespace_delete','catalog_modify',
    'ran_terminate','ran_switch','cpu_quota_change','firewall_modify','traffic_manipulate'))
SAFETY_FIELDS = ('ownership_verified','capacity_sufficient','monitoring_fresh',
                 'osm_trustworthy','core_healthy')

class Policy:
    def __init__(self, catalog):
        self.catalog = catalog
    def evaluate(self, alarm):
        rule = self.catalog.get(alarm['alarm_type'], {})
        evidence = alarm['observations']
        missing = [k for k in rule.get('required_evidence', []) if evidence.get(k) is None]
        conflicts = [k for k,v in rule.get('required_conditions', {}).items() if evidence.get(k) is not v]
        corroborated = not missing and not conflicts and len(evidence) >= 2
        action = rule.get('action', 'observe_notify')
        if action!='observe_notify' and not corroborated:
            action='observe_notify'
        protected = alarm['service'] in PROTECTED_NS or evidence.get('protected_state') is not False
        return {'action': action, 'class': 'A' if action == 'observe_notify' else 'C',
                'hypothesis': rule.get('hypothesis', 'Unclassified condition; operator investigation required'),
                'probable_cause': 'HYPOTHESIS_ONLY', 'missing_evidence': missing,
                'corroborated': corroborated, 'conflicting_evidence': conflicts, 'protected': protected,
                'service_impact': rule.get('impact', 'UNKNOWN'),
                'automation_eligible': False,
                'verification': rule.get('verification', ['fresh telemetry','protected core healthy']),
                'rollback': rule.get('rollback', 'No live action; preserve current deployment and escalate'),
                'cooldown': rule.get('cooldown', 600), 'max_retries': 0,
                'runbook': 'docs/layer3/alarm-catalog.md#'+alarm['alarm_type'].lower()}

class SafetyValidator:
    """All gates remain mandatory even after operator approval."""
    def validate(self, action, context, *, mode, emergency_stop=False):
        reasons = []
        if action['action'] in FORBIDDEN or action.get('class') == 'D':
            reasons.append('forbidden action')
        if mode not in ('APPROVAL_REQUIRED',):
            reasons.append('mode does not permit production actions')
        if emergency_stop:
            reasons.append('emergency stop')
        if action.get('provenance') != 'LIVE':
            reasons.append('simulation cannot invoke production executor')
        if action.get('service') in PROTECTED_NS or context.get('protected_state') is not False:
            reasons.append('protected or unknown service state')
        for field in SAFETY_FIELDS:
            if context.get(field) is not True:
                reasons.append('missing safety evidence: '+field)
        if context.get('pending_operation') is not False:
            reasons.append('pending or unknown lifecycle operation')
        if context.get('operation_state') != 'COMPLETED':
            reasons.append('untrustworthy lifecycle state')
        if context.get('cooldown_complete') is not True:
            reasons.append('cooldown active or unknown')
        if action.get('status') != 'APPROVED':
            reasons.append('operator approval required')
        # No production operation has profile-specific validation in this change.
        reasons.append('no validated production action registered')
        return reasons

class ProductionExecutor:
    """Deliberately has no network client or mutation callback. Cannot be enabled by mode."""
    def execute(self, *args, **kwargs):
        raise PermissionError('Production remediation is disabled in this offline implementation')

class Verification:
    @staticmethod
    def assess(evidence, required):
        missing = [key for key in required if evidence.get(key) is not True]
        return {'passed': not missing, 'missing': missing}
