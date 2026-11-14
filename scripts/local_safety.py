"""Local process locks and durable, atomic file replacement."""
import contextlib
import fcntl
import os
from pathlib import Path
import tempfile
import time


class LockTimeout(TimeoutError):
    pass


@contextlib.contextmanager
def exclusive_lock(path, timeout=None, poll=0.1):
    # timeout=None keeps the original blocking behaviour for existing callers.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout('Lifecycle lock is held by another operation') from None
                    time.sleep(poll)
        yield
    finally:
        os.close(fd)

def atomic_write(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing symlink state file')
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
