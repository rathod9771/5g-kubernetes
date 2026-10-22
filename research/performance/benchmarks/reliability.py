"""Ground-truth classification; absence of alarms is not evidence of health."""
def confusion(trials):
    counts={'TP':0,'FP':0,'TN':0,'FN':0};seen=set()
    for item in trials:
        if item.get('provenance') not in ('LIVE','RECORDED_LIVE') or not item.get('ground_truth_ref'):raise ValueError('Independent real ground truth required')
        if type(item.get('condition_present')) is not bool or type(item.get('alarm_fired')) is not bool:raise ValueError('Explicit positive/negative truth and alarm state required')
        key=(item['experiment_id'],item['trial_id'])
        if key in seen:raise ValueError('Duplicate evaluation window')
        seen.add(key);truth,alarm=item['condition_present'],item['alarm_fired']
        counts['TP' if truth and alarm else 'FP' if alarm else 'FN' if truth else 'TN']+=1
    negative=counts['FP']+counts['TN'];alarms=counts['FP']+counts['TP']
    return dict(counts,false_positive_rate=counts['FP']/negative if negative else None,false_discovery_fraction=counts['FP']/alarms if alarms else None,negative_windows=negative)


def success_rate(trials):
    counts={'SUCCESS':0,'FAILED':0,'UNKNOWN':0};seen=set()
    for item in trials:
        if item.get('provenance') not in ('LIVE','RECORDED_LIVE') or not item.get('evidence_ref'):raise ValueError('Verified real outcome evidence required')
        if item.get('outcome') not in counts or item['trial_id'] in seen:raise ValueError('Unique outcome per trial required')
        seen.add(item['trial_id']);counts[item['outcome']]+=1
    total=sum(counts.values())
    return dict(counts,total_trials=total,success_rate=counts['SUCCESS']/total if total and not counts['UNKNOWN'] else None,success_fraction_lower_bound=counts['SUCCESS']/total if total else None,success_fraction_upper_bound=(counts['SUCCESS']+counts['UNKNOWN'])/total if total else None)
