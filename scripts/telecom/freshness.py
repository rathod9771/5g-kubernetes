"""Read-time aging does not rewrite immutable historical evidence."""
import copy


def current_evidence(matrix, now):
    value=copy.deepcopy(matrix)
    samples=list(value['tests'].values())+list(value['monitoring'].values())
    stale=any(now-v['observed_at']>120 for v in samples)
    value['current_freshness']='STALE' if stale else 'SIMULATED'
    value['production_eligible']=False
    return value
