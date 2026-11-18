"""Reviewed unprivileged holder for post-worker cgroup accounting.

Executes exactly one child (the existing bwrap command). On child exit,
records the real return code and stays alive for at most 30 seconds while the
root supervisor checks the quiescent cgroup; then exits with that return code.
The isolated child never sees the control directory.
"""
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

WAIT_SECONDS = 30


def _regular(path, uid, mode):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != mode:
        raise RuntimeError('GUARD_CONTROL_FILE_UNTRUSTED')


def run_guard(control, argv):
    uid = os.getuid()
    if uid == 0:
        raise RuntimeError('GUARD_MUST_BE_UNPRIVILEGED')
    root = Path(control)
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o711:
        raise RuntimeError('GUARD_CONTROL_DIR_UNTRUSTED')
    status, release = root/'worker-exited.json', root/'release'
    _regular(status, uid, 0o600)
    _regular(release, 0, 0o644)
    if status.stat().st_size != 0 or release.read_text() != 'HOLD\n':
        raise RuntimeError('GUARD_NOT_INITIALIZED')
    p = subprocess.Popen(argv, env={'PATH':'/usr/bin:/bin'}, stdin=subprocess.DEVNULL, close_fds=True)
    code = p.wait()
    report = json.dumps({'schema':1,'guard_pid':os.getpid(),
                         'worker_pid':p.pid,'worker_returncode':code}, separators=(',',':')).encode('utf-8')
    fd = os.open(status, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if os.fstat(fd).st_size != 0 or os.write(fd, report) != len(report):
            raise RuntimeError('GUARD_STATUS_WRITE_FAILED')
        if os.write(fd, b'\nCOMMITTED\n') != len(b'\nCOMMITTED\n'):
            raise RuntimeError('GUARD_STATUS_COMMIT_FAILED')
        os.fsync(fd)
    finally:
        os.close(fd)
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        _regular(release, 0, 0o644)
        if release.read_text() == 'RELEASE\n':
            return code if code >= 0 else 128 - code
        time.sleep(0.1)
    return 124


def main():
    if len(sys.argv) < 6 or sys.argv[1] != '--control' or sys.argv[3] != '--' or sys.argv[4] != '/usr/bin/bwrap':
        raise RuntimeError('GUARD_UNREVIEWED_CHILD')
    code = run_guard(sys.argv[2], sys.argv[4:])
    raise SystemExit(code)


if __name__ == '__main__':
    main()
