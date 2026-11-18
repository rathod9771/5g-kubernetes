"""Fail-closed differentiation of a live cgroup sample and terminal accounting.

Never promotes a *last live* snapshot into completed native-build accounting.
The fallback is only permitted for the pre-approved review stop (code 78).
"""
from build_policy import Blocked, LIMITS

REQUIRED = ('memory.max', 'memory.swap.max', 'cpu.max', 'pids.max',
            'memory.peak', 'pids.peak', 'cpu.stat', 'memory.events',
            'pids.events', 'cgroup.events')


def select_review_accounting(terminal_group_exists, last_live_snapshot,
                             last_live_path, last_properties, exit_code,
                             signal, timeout):
    """Return (verified_sample, mode) when terminal cgroup was removed.

    Never returns a terminal-readback qualification. When the cgroup remains
    available the caller MUST do the normal fresh capture and empty check.
    """
    if terminal_group_exists:
        raise Blocked('TERMINAL_GROUP_STILL_EXISTS_REQUIRES_FRESH_CAPTURE')
    if (exit_code != 78 or signal is not None or timeout
            or last_properties.get('Result') != 'exit-code'
            or last_properties.get('ExecMainCode') != '1'
            or last_properties.get('ExecMainStatus') != '78'):
        raise Blocked('FINAL_CGROUP_ACCOUNTING_UNAVAILABLE')
    if not last_live_path or not isinstance(last_live_snapshot, dict) or any(k not in last_live_snapshot for k in REQUIRED):
        raise Blocked('MISSING_VERIFIED_LAST_LIVE_SNAPSHOT')
    checks = {'memory.max': str(LIMITS['memory.max']),
              'memory.swap.max': str(LIMITS['memory.swap.max']),
              'cpu.max': LIMITS['cpu.max'],
              'pids.max': str(LIMITS['pids.max'])}
    for key, expected in checks.items():
        if last_live_snapshot[key] != expected:
            raise Blocked('LAST_LIVE_LIMIT_DRIFT:'+key)
    if 'populated 1' not in last_live_snapshot['cgroup.events'].splitlines():
        raise Blocked('LAST_LIVE_SNAPSHOT_NOT_POPULATED')
    for field, value in [('memory.events', 'oom_kill'), ('pids.events', 'max')]:
        vals = dict(line.split() for line in last_live_snapshot[field].splitlines())
        if vals.get(value) != '0':
            raise Blocked('LAST_LIVE_RESOURCE_LIMIT_HIT:'+field)
    return last_live_snapshot, 'LAST_LIVE_ONLY__TERMINAL_MISSING__NO_NATIVE_ADMISSION'
