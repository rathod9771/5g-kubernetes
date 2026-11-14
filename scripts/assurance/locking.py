"""Nonblocking process locks shared by all assurance collection entry points."""
import contextlib
import fcntl
import os
from pathlib import Path

@contextlib.contextmanager
def exclusive(path):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Assurance execution already active') from None
        yield
    finally:
        os.close(fd)
