"""Reviewed local policies; actual metric support is established by a dated inventory."""
import json
import math
from pathlib import Path
from .model import SEVERITIES, EVIDENCE_KEYS


def load_catalog(path=None):
    data=json.loads(Path(path or Path(__file__).parents[2]/'config/assurance-policies.json').resolve().read_text())
    if data.get('schema_version')!=1 or data.get('live_auto_enabled') is not False:
        raise ValueError('Policy schema or automation safety flag invalid')
    alarms=data.get('alarms')
    if not isinstance(alarms,dict) or not 1<=len(alarms)<=128:raise ValueError('Invalid alarm catalog')
    for name,rule in alarms.items():
        if not isinstance(rule,dict) or rule.get('severity') not in SEVERITIES:raise ValueError('Invalid alarm severity')
        for key in ('persistence','clear_persistence','max_age','max_gap','cooldown'):
            value=rule.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=86400:
                raise ValueError('Invalid bounded policy duration')
        if rule['max_age']==0 or rule['max_gap']==0 or rule.get('max_retries')!=0:
            raise ValueError('Missing freshness window or production retries enabled')
        if not set(rule.get('required_evidence',[]))<=EVIDENCE_KEYS:
            raise ValueError('Unsupported policy evidence')
        if rule.get('automation_eligible') is not False:
            raise ValueError('No production policy may be automatically eligible')
    return alarms
