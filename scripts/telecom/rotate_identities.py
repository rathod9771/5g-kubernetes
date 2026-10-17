"""Revoke and reissue every bearer token in an existing identities.json.

Preserves subject, tenant and role assignments exactly; only the sha256
digest each subject authenticates against is replaced. Never prints a
token to stdout/stderr, so nothing lands in a log, journal or shell
history. The new plaintext tokens are written once to a fresh, owner-only,
single-use sidecar file next to identities.json for the operator to
retrieve locally and distribute out-of-band, then delete.

No service restart is required to pick up a rotation: security.authenticate()
re-opens and re-reads TELECOM_AUTH_FILE on every request rather than caching
it, so the very next request after this script returns is checked against
the new file.
"""
import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_safety import atomic_write  # noqa: E402


def rotate(auth_path, expected_subjects=None):
    """Rotate every token in auth_path; return the path of the new sidecar file."""
    auth_path = Path(auth_path)
    parent = auth_path.parent
    if parent.is_symlink():
        raise ValueError('identities.json parent directory must not be a symlink')
    parent_info = os.stat(parent)
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022:
        raise ValueError('identities.json parent directory must be owner-only writable')
    fd = os.open(auth_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('identities.json must be an owner-only regular file (mode 0600)')
        with os.fdopen(fd) as stream:
            fd = None
            entries = json.loads(stream.read(65537))
    finally:
        if fd is not None:
            os.close(fd)
    if not isinstance(entries, list) or not entries:
        raise ValueError('identities.json is empty or malformed; refusing to rotate blindly')
    subjects = [entry.get('subject') for entry in entries]
    if expected_subjects is not None and sorted(subjects) != sorted(expected_subjects):
        raise ValueError('Unexpected subject set %r; expected exactly %r -- refusing to guess'
                          % (subjects, expected_subjects))

    new_tokens = {}
    rotated = []
    for entry in entries:
        token = secrets.token_hex(32)
        new_tokens[entry['subject']] = token
        rotated.append({**entry, 'sha256': hashlib.sha256(token.encode()).hexdigest()})

    # Write the new plaintext tokens to a fresh, single-use, owner-only
    # sidecar file FIRST, before identities.json is touched at all, and
    # never to stdout/stderr so nothing reaches a log, journal or shell
    # history. O_CREAT|O_EXCL refuses to clobber or follow an existing
    # path/symlink of the same name -- an existing sidecar is never
    # overwritten. If this write fails (disk full, permission error, a
    # same-second collision with a prior run), nothing below has run yet:
    # identities.json is still the untouched original and every old token
    # keeps working, so there is no window in which credentials exist that
    # are unrecoverable.
    handoff = parent / ('reissued-tokens.%d.json' % int(time.time()))
    handoff_fd = os.open(handoff, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handoff_fd, 'w') as stream:
        stream.write(json.dumps(new_tokens))
        stream.flush()
        os.fsync(stream.fileno())
    dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    # Only once the new tokens are safely and durably recorded do we swap
    # identities.json over to the new hashes. atomic_write()'s os.replace()
    # is same-filesystem-atomic: a concurrent request sees either the
    # fully-old or fully-new file, never a partial write, and mkstemp's own
    # 0600 mode carries over so permissions stay restrictive. If this step
    # fails, identities.json is left exactly as it was -- old tokens stay
    # valid -- and the sidecar written above still holds the new tokens, so
    # a retry picks up from here rather than losing anything.
    atomic_write(auth_path, json.dumps(rotated).encode())
    os.chmod(auth_path, 0o600)
    return handoff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('auth_path', help='Path to the existing identities.json to rotate')
    parser.add_argument('expected_subjects', nargs='*',
                         help='Exact set of subjects expected in identities.json; '
                              'rotation refuses to run if the file does not match')
    args = parser.parse_args()
    handoff = rotate(args.auth_path, expected_subjects=args.expected_subjects or None)
    print('New tokens written to: ' + str(handoff), file=sys.stderr)
    print('Retrieve them locally, distribute out-of-band, then delete that file.', file=sys.stderr)


if __name__ == '__main__':
    main()
