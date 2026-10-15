"""Bounded, process-local read-only monitoring; never used for mutations."""
import os
import selectors
import signal
import subprocess
import threading
import time
from collections import OrderedDict


class BusyError(RuntimeError):
    pass


class Cache:
    def __init__(self, limit=128, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self.lock = threading.Condition()
        self.values = OrderedDict()
        self.pending = set()
        self.generation = 0

    def clear(self):
        with self.lock:
            self.generation += 1
            self.values.clear()
            self.lock.notify_all()

    def get(self, key, ttl, compute, wait=30):
        deadline = self.clock() + wait
        with self.lock:
            while key in self.pending:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise BusyError('Monitoring request busy; retry')
                self.lock.wait(remaining)
            item = self.values.get(key)
            if item and item[0] > self.clock():
                self.values.move_to_end(key)
                return item[1]
            self.pending.add(key)
            generation = self.generation
        try:
            result = compute()
            with self.lock:
                if generation == self.generation:
                    self.values[key] = (self.clock() + ttl, result)
                    self.values.move_to_end(key)
                    while len(self.values) > self.limit:
                        self.values.popitem(last=False)
            return result
        finally:
            with self.lock:
                self.pending.discard(key)
                self.lock.notify_all()


cache = Cache()
slots = threading.BoundedSemaphore(4)


def run(command, *, shell=False, capture_output=True, text=True, timeout=8):
    """Kill the entire monitoring command group on timeout, including pipe children."""
    if not slots.acquire(timeout=2):
        return subprocess.CompletedProcess(command, 124, '', 'Monitoring capacity busy; retry')
    try:
        with subprocess.Popen(command, shell=shell, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True) as proc:
            buffers={proc.stdout:bytearray(),proc.stderr:bytearray()}
            deadline=time.monotonic()+timeout;total=0;reason=None
            with selectors.DefaultSelector() as selector:
                for stream in buffers:
                    os.set_blocking(stream.fileno(),False)
                    selector.register(stream,selectors.EVENT_READ)
                while selector.get_map() or proc.poll() is None:
                    if time.monotonic()>=deadline:reason='Monitoring command timed out';break
                    for key,_ in selector.select(.02):
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk:selector.unregister(key.fileobj);continue
                        total+=len(chunk)
                        if total>2*1024*1024:reason='Monitoring output limit exceeded';break
                        buffers[key.fileobj].extend(chunk)
                    if reason:break
                if reason:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    proc.wait(timeout=5)
                    return subprocess.CompletedProcess(command,124,'' if text else b'',reason if text else reason.encode())
            out,err=(bytes(buffers[stream]) for stream in (proc.stdout,proc.stderr))
            if text:out,err=out.decode('utf-8','replace'),err.decode('utf-8','replace')
            return subprocess.CompletedProcess(command,proc.wait(timeout=5),out,err)
    finally:
        slots.release()
