{{- define "fog.server" -}}
"""Logical fog HTTP API with bounded DNS refresh and request concurrency."""
import json
import math
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

DNS_TIMEOUT = 2
DNS_INTERVAL = 30
DNS_MAX_AGE = 60
MAX_REQUESTS = 4
started = time.monotonic()
counters = {'requests': 0, 'processed': 0}
counters_lock = threading.Lock()


def bounded_dns(host, port, timeout=DNS_TIMEOUT):
    # A fixed child bounds libc DNS independently of DNS/network configuration.
    # Its output is limited to eight addresses; run() kills/reaps it on timeout.
    try:
        result = subprocess.run(
            [sys.executable, '-B', '-c',
             'import json,socket,sys; print(json.dumps(sorted({r[4][0] for r in '
             'socket.getaddrinfo(sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM)})[:8]))',
             host, str(port)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError('DNS lookup timed out') from None
    if result.returncode:
        raise OSError('DNS lookup failed')
    try:
        addresses = json.loads(result.stdout)
        if (not isinstance(addresses, list) or not 0 < len(addresses) <= 8
                or any(not isinstance(a, str) or len(a) > 64 for a in addresses)):
            raise ValueError()
    except (ValueError, TypeError):
        raise OSError('Invalid DNS result') from None
    return tuple(addresses)


class DNSCache:
    def __init__(self, host, resolve=None, clock=time.monotonic,
                 interval=DNS_INTERVAL, max_age=DNS_MAX_AGE):
        if interval <= 0 or max_age <= 0:
            raise ValueError('DNS intervals must be positive')
        self.host, self.resolve, self.clock = host, resolve, clock
        self.interval, self.max_age = interval, max_age
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.addresses = ()
        self.last_success = None
        self.last_ok = False
        self.failures = self.timeouts = 0

    def seed(self, addresses):
        with self.lock:
            self.addresses = tuple(addresses or ())
            self.last_success = self.clock()
            self.last_ok = True

    def refresh(self):
        # Never hold the cache lock while waiting for the resolver.
        try:
            addresses = (self.resolve or bounded_dns)(self.host, 38472, timeout=DNS_TIMEOUT)
        except OSError as error:
            with self.lock:
                self.last_ok = False
                self.failures += 1
                self.timeouts += isinstance(error, TimeoutError)
        else:
            self.seed(addresses)

    def snapshot(self):
        with self.lock:
            age = -1 if self.last_success is None else max(0, self.clock() - self.last_success)
            stale = not self.last_ok or age < 0 or age >= self.max_age
            return {'resolved': int(not stale), 'age': age, 'stale': int(stale),
                    'addresses': self.addresses, 'failures': self.failures,
                    'timeouts': self.timeouts}

    def _run(self):
        # Startup discovery seeds the cache. Subsequent attempts never overlap,
        # and even failed attempts wait the full interval before another attempt.
        while not self.stop_event.wait(self.interval):
            self.refresh()

    def start(self):
        with self.lock:
            if self.thread is not None:
                raise RuntimeError('DNS worker already started')
            self.thread = threading.Thread(target=self._run, name='fog-dns')
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(DNS_TIMEOUT + 1)
            if self.thread.is_alive():
                raise RuntimeError('DNS worker did not stop within its resolver budget')


dns_cache = DNSCache(os.environ.get('DU_SERVICE', ''))


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *args):
        pass

    def respond(self, code, body, content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def count_request(self):
        with counters_lock:
            counters['requests'] += 1

    def do_GET(self):
        self.count_request()
        if self.path == '/health':
            return self.respond(200, json.dumps({'healthy': True, 'mode': 'logical-fog'}))
        if self.path != '/metrics':
            return self.respond(404, '{}')
        dns = dns_cache.snapshot()
        with counters_lock:
            current = dict(counters)
        metrics = [('fog_requests_total', 'counter', current['requests']),
                   ('fog_processed_values_total', 'counter', current['processed']),
                   ('fog_uptime_seconds', 'gauge', time.monotonic() - started),
                   ('fog_du_dns_resolved', 'gauge', dns['resolved']),
                   ('fog_du_dns_cache_age_seconds', 'gauge', dns['age']),
                   ('fog_du_dns_stale', 'gauge', dns['stale']),
                   ('fog_du_dns_cached_addresses', 'gauge', len(dns['addresses'])),
                   ('fog_du_dns_refresh_failures_total', 'counter', dns['failures']),
                   ('fog_du_dns_timeouts_total', 'counter', dns['timeouts'])]
        body = ''.join(f'# TYPE {name} {kind}\n{name} {value}\n' for name, kind, value in metrics)
        self.respond(200, body, 'text/plain; version=0.0.4')

    def do_POST(self):
        self.count_request()
        if self.path != '/process':
            return self.respond(404, '{}')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError()
            values = json.loads(self.rfile.read(length))['values']
            if not isinstance(values, list) or not 0 < len(values) <= 1024:
                raise ValueError()
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError()
            result = {'count': len(values), 'mean': statistics.fmean(values),
                      'minimum': min(values), 'maximum': max(values)}
        except (ValueError, KeyError, TypeError, OverflowError, TimeoutError):
            return self.respond(400, '{"error":"Expected a bounded numeric values array"}')
        with counters_lock:
            counters['processed'] += len(values)
        self.respond(200, json.dumps(result))


class BoundedHTTPServer(HTTPServer):
    """Four workers, no unbounded executor queue or thread-per-request creation."""
    request_queue_size = MAX_REQUESTS

    def __init__(self, address, handler=Handler):
        self.executor = ThreadPoolExecutor(max_workers=MAX_REQUESTS, thread_name_prefix='fog-http')
        self.slots = threading.BoundedSemaphore(MAX_REQUESTS)
        self.active = set()
        self.active_lock = threading.Lock()
        super().__init__(address, handler)
        self.timeout = .2

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(.1)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        with self.active_lock:
            self.active.add(request)
        try:
            self.executor.submit(self._process, request, address)
        except RuntimeError:
            with self.active_lock:
                self.active.discard(request)
            self.slots.release()
            self.shutdown_request(request)

    def _process(self, request, address):
        try:
            self.finish_request(request, address)
        except (OSError, ValueError):
            pass  # Disconnected clients/shutdown do not leak worker slots.
        finally:
            self.shutdown_request(request)
            with self.active_lock:
                self.active.discard(request)
            self.slots.release()

    def server_close(self):
        super().server_close()
        # Interrupt request reads even when a peer keeps trickling bytes.
        with self.active_lock:
            for request in self.active:
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.executor.shutdown(wait=True)


def wait_for_du(resolve=bounded_dns, clock=time.monotonic, pause=time.sleep, stop_event=None):
    deadline = clock() + 120
    while True:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError('Startup stopped')
        try:
            remaining = deadline - clock()
            if remaining <= 0:
                raise RuntimeError('Edge DU service discovery timed out')
            return resolve(os.environ['DU_SERVICE'], 38472, timeout=min(DNS_TIMEOUT, remaining))
        except OSError:
            if clock() >= deadline:
                raise RuntimeError('Edge DU service discovery timed out') from None
            delay = min(1, max(0, deadline - clock()))
            if stop_event is None:
                pause(delay)
            else:
                stop_event.wait(delay)


def main():
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    try:
        addresses = wait_for_du(stop_event=stop)
    except RuntimeError:
        if stop.is_set():
            return
        raise
    if stop.is_set():
        return
    dns_cache.host = os.environ['DU_SERVICE']
    dns_cache.seed(addresses)
    server = BoundedHTTPServer(('0.0.0.0', 8080))
    dns_cache.start()
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        dns_cache.stop_event.set()
        server.server_close()
        dns_cache.stop()


if __name__ == '__main__':
    main()
{{- end -}}
