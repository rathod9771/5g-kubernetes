"""Pure, fail-closed validation of a quiescent worker cgroup kept alive by a guard.

This only validates evidence; the guard itself never expands build permissions.
"""
import json
from pathlib import Path
from build_policy import Blocked, LIMITS

REQUIRED = ('memory.max', 'memory.swap.max', 'cpu.max', 'pids.max',
            'memory.peak', 'pids.peak', 'cpu.stat', 'memory.events',
            'pids.events', 'cgroup.events')


def validate_guard_status(raw, main_pid):
    if not isinstance(raw, str) or not 0 < len(raw.encode('utf-8')) <= 512:
        raise Blocked('TERMINAL_GUARD_STATUS_SIZE')
    if not raw.endswith('\nCOMMITTED\n'):
        raise Blocked('TERMINAL_GUARD_STATUS_NOT_COMMITTED')
    try:
        info = json.loads(raw.removesuffix('\nCOMMITTED\n'))
    except ValueError as error:
        raise Blocked('TERMINAL_GUARD_STATUS_JSON') from error
    if not isinstance(info, dict) or set(info) != {'schema', 'guard_pid', 'worker_pid', 'worker_returncode'} or info['schema'] != 1:
        raise Blocked('TERMINAL_GUARD_STATUS_SCHEMA')
    if any(type(info[key]) is not int for key in ('guard_pid', 'worker_pid', 'worker_returncode')):
        raise Blocked('TERMINAL_GUARD_STATUS_TYPE')
    if info['guard_pid'] <= 1 or info['guard_pid'] != main_pid or info['worker_pid'] <= 1 or info['worker_pid'] == main_pid:
        raise Blocked('TERMINAL_GUARD_PID_MISMATCH')
    if not -128 <= info['worker_returncode'] <= 255:
        raise Blocked('TERMINAL_GUARD_EXIT_RANGE')
    return info


def validate_quiescent_capture(snapshot, guard, cgroup_procs, pids_current, child_cgroups, properties):
    """Verified post-worker readback. Does not claim the cgroup is empty."""
    if not isinstance(snapshot, dict) or any(x not in snapshot for x in REQUIRED):
        raise Blocked('TERMINAL_GUARD_INCOMPLETE_COUNTERS')
    expected = {'memory.max': str(LIMITS['memory.max']),
                'memory.swap.max': str(LIMITS['memory.swap.max']),
                'cpu.max': LIMITS['cpu.max'],
                'pids.max': str(LIMITS['pids.max'])}
    for field, value in expected.items():
        if snapshot[field] != value:
            raise Blocked('TERMINAL_GUARD_LIMIT_DRIFT:'+field)
    if cgroup_procs.strip() != str(guard['guard_pid']) or pids_current.strip() != '1' or child_cgroups:
        raise Blocked('TERMINAL_GUARD_NOT_QUIESCENT')
    if 'populated 1' not in snapshot['cgroup.events'].splitlines():
        raise Blocked('TERMINAL_GUARD_NOT_POPULATED')
    if properties.get('ExecMainPID') != str(guard['guard_pid']) or properties.get('SubState') != 'running':
        raise Blocked('TERMINAL_GUARD_UNIT_MISMATCH')
    for field, name in (('memory.events', 'oom_kill'), ('pids.events', 'max')):
        try:
            counters = dict(line.split() for line in snapshot[field].splitlines())
        except ValueError as error:
            raise Blocked('TERMINAL_GUARD_INVALID_COUNTERS') from error
        if counters.get(name) != '0':
            raise Blocked('TERMINAL_GUARD_RESOURCE_LIMIT_HIT:'+field)
    return {'mode':'POST_WORKER_QUIESCENT_GUARD_HELD',
            'worker_exit_code':guard['worker_returncode'],
            'guard_pid':guard['guard_pid'],
            'worker_pid':guard['worker_pid'],
            'worker_accounting_complete':True,
            'terminal_cgroup_empty':False,
            'native_compilation_authorized':False}


def verify_guard_release_end(guard, final_properties, observed_exit):
    """Bind systemd terminal status to the child exit after release."""
    if observed_exit.get('signal') is not None or observed_exit.get('timeout'):
        raise Blocked('TERMINAL_GUARD_UNEXPECTED_TERMINATION')
    if observed_exit.get('exit_code') != guard['worker_returncode']:
        raise Blocked('TERMINAL_GUARD_EXIT_DRIFT')
    if final_properties.get('ExecMainPID') not in (str(guard['guard_pid']), '0'):
        raise Blocked('TERMINAL_GUARD_MAIN_PID_DRIFT')
    if final_properties.get('SubState') not in ('exited', 'failed', 'dead'):
        raise Blocked('TERMINAL_GUARD_UNIT_NOT_TERMINAL')
    if observed_exit['exit_code'] == 0 and final_properties.get('Result') != 'success':
        raise Blocked('TERMINAL_GUARD_SUCCESS_RESULT_MISMATCH')
    return True
