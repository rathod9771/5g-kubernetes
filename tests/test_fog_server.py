"""Offline API/cache/concurrency/lifecycle regressions; never contact a cluster."""
import concurrent.futures
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def module():
    import types
    path = ROOT/'helm/fog-compute/templates/_server.tpl'
    source = '\n'.join(path.read_text().splitlines()[1:-1]) + '\n'
    result = types.ModuleType('fog_test')
    exec(compile(source, str(path), 'exec'), result.__dict__)
    return result



class CacheTests(unittest.TestCase):
    def setUp(self):
        self.app = module()
        self.now = 100

    def cache(self, resolver):
        return self.app.DNSCache('synthetic', resolve=resolver, clock=lambda: self.now)

    def test_success_caches_addresses_and_age(self):
        cache = self.cache(mock.Mock(return_value=('192.0.2.1', '2001:db8::1')))
        cache.refresh(); self.now += 3
        state = cache.snapshot()
        self.assertEqual(state['addresses'], ('192.0.2.1', '2001:db8::1'))
        self.assertEqual((state['resolved'], state['age'], state['stale']), (1, 3, 0))

    def test_failure_retains_last_known_addresses_but_invalidates_status(self):
        cache = self.cache(mock.Mock(side_effect=OSError('DNS failed')))
        cache.seed(('192.0.2.1',)); self.now += 10; cache.refresh()
        state = cache.snapshot()
        self.assertEqual((state['resolved'], state['stale'], state['age']), (0, 1, 10))
        self.assertEqual(state['addresses'], ('192.0.2.1',))
        self.assertEqual((state['failures'], state['timeouts']), (1, 0))

    def test_timeout_and_recovery(self):
        cache = self.cache(mock.Mock(side_effect=[TimeoutError(), ('192.0.2.2',)]))
        cache.refresh(); self.assertEqual(cache.snapshot()['timeouts'], 1)
        cache.refresh(); self.assertEqual(cache.snapshot()['resolved'], 1)
        self.assertEqual(cache.snapshot()['addresses'], ('192.0.2.2',))

    def test_stale_and_uninitialized_cache(self):
        cache = self.cache(mock.Mock())
        self.assertEqual((cache.snapshot()['age'], cache.snapshot()['resolved']), (-1, 0))
        cache.seed(('192.0.2.1',)); self.now += 60
        self.assertEqual((cache.snapshot()['stale'], cache.snapshot()['resolved']), (1, 0))

    def test_real_dns_and_bounded_subprocess_timeout(self):
        self.assertTrue(self.app.bounded_dns('localhost', 38472))
        real = subprocess.run
        def stalled(args, **kwargs):
            return real([sys.executable, '-B', '-c', 'import time;time.sleep(20)'], **kwargs)
        start = time.monotonic()
        with mock.patch.object(self.app.subprocess, 'run', side_effect=stalled):
            with self.assertRaises(TimeoutError):self.app.bounded_dns('synthetic', 38472, timeout=.05)
        self.assertLess(time.monotonic()-start, 2)

    def test_child_failure_and_invalid_output(self):
        for returncode, output in [(1, b''), (0, b'{}'), (0, b'[]'), (0, b'not-json')]:
            with mock.patch.object(self.app.subprocess, 'run', return_value=mock.Mock(returncode=returncode, stdout=output)):
                with self.assertRaises(OSError):self.app.bounded_dns('synthetic', 38472)

    def test_one_worker_periodic_failures_and_clean_stop(self):
        resolver = mock.Mock(side_effect=OSError())
        cache = self.app.DNSCache('synthetic', resolve=resolver, interval=.03)
        cache.start()
        with self.assertRaises(RuntimeError):cache.start()
        time.sleep(.12); cache.stop(); count = resolver.call_count
        self.assertGreaterEqual(count, 1); self.assertLessEqual(count, 5)
        self.assertFalse(cache.thread.is_alive()); time.sleep(.04)
        self.assertEqual(resolver.call_count, count)


    def test_sigterm_stops_inflight_dns_child_and_http_workers(self):
        # Run main on an ephemeral local port, never the application's fixed port.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            ready=Path(directory)/'child-ready'
            code="""
import importlib.util, os, subprocess, sys
from pathlib import Path
import types
app=types.ModuleType('app');source='\\n'.join(Path(sys.argv[1]).read_text().splitlines()[1:-1]);exec(compile(source,sys.argv[1],'exec'),app.__dict__)
os.environ['DU_SERVICE']='localhost'
original=app.BoundedHTTPServer
app.BoundedHTTPServer=lambda address: original(('127.0.0.1',0))
app.dns_cache.interval=.01
real_run=subprocess.run
class Resolver:
    def __call__(self,*args,**kw):
        try:
            real_run([sys.executable,'-B','-c','import os,time;from pathlib import Path;Path('+repr(sys.argv[2])+').write_text(str(os.getpid()));time.sleep(20)'],timeout=.2,stdout=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:raise TimeoutError()
        return ('127.0.0.1',)
app.dns_cache.resolve=Resolver()
app.main()
"""
            process=subprocess.Popen([sys.executable,'-B','-c',code,str(ROOT/'helm/fog-compute/templates/_server.tpl'),str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+3
                while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(ready.exists())
                child=int(ready.read_text());start=time.monotonic();process.terminate()
                stdout,stderr=process.communicate(timeout=3)
                self.assertEqual(process.returncode,0,stderr.decode())
                self.assertLess(time.monotonic()-start,2)
                with self.assertRaises(ProcessLookupError):__import__('os').kill(child,0)
            finally:
                if process.poll() is None:process.kill();process.communicate()


class APITests(unittest.TestCase):
    def setUp(self):
        self.app = module()
        self.app.dns_cache.seed(('192.0.2.1',))
        self.server = self.app.BoundedHTTPServer(('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(2); self.server.server_close()

    def request(self, path, payload=None):
        req = Request(self.url+path, data=payload, headers={'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=3) as response:return response.status, response.read().decode(), response.headers
        except HTTPError as error:return error.code, error.read().decode(), error.headers

    def test_processing_health_and_invalid_inputs_unchanged(self):
        status, text, _ = self.request('/process', b'{"values":[2,4,6]}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text), {'count':3,'mean':4.0,'minimum':2,'maximum':6})
        self.assertEqual(json.loads(self.request('/health')[1]), {'healthy':True,'mode':'logical-fog'})
        for body in [b'{}', b'{"values":[]}', b'{"values":[true]}', b'{"values":[NaN]}', b'[]', b'{"values":[1e999]}', b'not-json']:
            self.assertEqual(self.request('/process', body)[0], 400)
        self.assertEqual(self.request('/unknown')[0], 404)

    def test_prometheus_names_types_counters_and_staleness(self):
        self.request('/process', b'{"values":[1,2,3,4]}')
        status, text, headers = self.request('/metrics')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'text/plain; version=0.0.4')
        values = dict(line.split() for line in text.splitlines() if not line.startswith('#'))
        expected = {'fog_requests_total','fog_processed_values_total','fog_uptime_seconds','fog_du_dns_resolved'}
        self.assertTrue(expected <= values.keys())
        self.assertEqual(values['fog_requests_total'], '2')
        self.assertEqual(values['fog_processed_values_total'], '4')
        self.assertEqual(values['fog_du_dns_resolved'], '1')
        self.assertEqual(values['fog_du_dns_cached_addresses'], '1')
        self.assertEqual(values['fog_du_dns_stale'], '0')
        for name, value in values.items():
            float(value)
            self.assertIn('# TYPE '+name+' '+('counter' if name.endswith('_total') else 'gauge'), text)
            self.assertNotIn('{', name)  # Existing unlabeled metric contract retained.
        self.app.dns_cache.last_success -= 61
        text = self.request('/metrics')[1]
        self.assertIn('fog_du_dns_resolved 0\n', text)
        self.assertIn('fog_du_dns_stale 1\n', text)

    def test_metrics_and_processing_never_wait_for_inflight_dns(self):
        entered = threading.Event(); release = threading.Event()
        def blocked(*args, **kw):entered.set(); release.wait(2); raise TimeoutError()
        cache = self.app.DNSCache('synthetic', resolve=blocked)
        cache.seed(('192.0.2.1',)); self.app.dns_cache = cache
        worker = threading.Thread(target=cache.refresh); worker.start(); self.assertTrue(entered.wait(1))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                start = time.monotonic()
                metrics = pool.submit(self.request, '/metrics')
                process = pool.submit(self.request, '/process', b'{"values":[1,2]}')
                self.assertEqual(metrics.result(timeout=1)[0], 200)
                self.assertEqual(process.result(timeout=1)[0], 200)
                self.assertLess(time.monotonic()-start, .5)
        finally:release.set(); worker.join(2)

    def test_processing_does_not_queue_behind_slow_monitoring_handler(self):
        entered = threading.Event(); release = threading.Event()
        original = self.app.dns_cache.snapshot
        def slow():entered.set(); release.wait(2); return original()
        with mock.patch.object(self.app.dns_cache, 'snapshot', side_effect=slow), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.request, '/metrics'); self.assertTrue(entered.wait(1))
            try:self.assertEqual(self.request('/process', b'{"values":[2]}')[0], 200)
            finally:release.set()
            self.assertEqual(future.result()[0], 200)

    def track_worker_completion(self):
        # A response body can arrive before shutdown_request()/slot release.
        # Observe the end of the actual worker, not client receipt or a sleep.
        condition = threading.Condition()
        completed = [0]
        original = self.server._process
        def tracked(request, address):
            try:
                return original(request, address)
            finally:
                with condition:
                    completed[0] += 1
                    condition.notify_all()
        self.server._process = tracked
        def wait_for(count):
            with condition:
                self.assertTrue(condition.wait_for(lambda: completed[0] >= count, timeout=3),
                                'accepted workers did not complete within deadline')
        return wait_for

    def test_counters_are_exact_under_concurrent_processing(self):
        wait_for = self.track_worker_completion()
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for batch in range(10):
                results.extend(pool.map(lambda _:self.request('/process', b'{"values":[1,2]}'), range(3)))
                wait_for((batch + 1) * 3)
        self.assertEqual(len(results), 30)
        self.assertTrue(all(r[0]==200 for r in results), [r[0] for r in results])
        values=dict(line.split() for line in self.request('/metrics')[1].splitlines() if not line.startswith('#'))
        self.assertEqual(values['fog_processed_values_total'], '60')
        self.assertEqual(values['fog_requests_total'], '31')

    def test_completed_responses_can_overlap_admission_cleanup(self):
        wait_for = self.track_worker_completion()
        release = threading.Event()
        condition = threading.Condition()
        arrived = [0]
        original = self.server.finish_request
        def hold_after_response(request, address):
            original(request, address)
            with condition:
                arrived[0] += 1
                condition.notify_all()
            if not release.wait(3):
                raise RuntimeError('test cleanup barrier expired')
        self.server.finish_request = hold_after_response
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.app.MAX_REQUESTS) as pool:
                results = list(pool.map(lambda _:self.request('/process', b'{"values":[1,2]}'),
                                        range(self.app.MAX_REQUESTS)))
            self.assertEqual([r[0] for r in results], [200] * self.app.MAX_REQUESTS)
            with condition:
                self.assertTrue(condition.wait_for(lambda: arrived[0] == self.app.MAX_REQUESTS, timeout=2))
            # All bodies arrived, but their admitted workers still hold slots.
            # Body-free probe avoids a client write racing the intentional close.
            self.assertEqual(self.request('/health')[0], 503)
            with self.app.counters_lock:
                self.assertEqual(self.app.counters, {'requests':self.app.MAX_REQUESTS,
                                                   'processed':2*self.app.MAX_REQUESTS})
        finally:
            release.set()
        wait_for(self.app.MAX_REQUESTS)
        self.server.finish_request = original
        self.assertEqual(self.request('/process', b'{"values":[3]}')[0], 200)
        wait_for(self.app.MAX_REQUESTS + 1)
        metrics = self.request('/metrics')[1]
        values = dict(line.split() for line in metrics.splitlines() if not line.startswith('#'))
        self.assertEqual(values['fog_requests_total'], str(self.app.MAX_REQUESTS + 2))
        self.assertEqual(values['fog_processed_values_total'], str(2*self.app.MAX_REQUESTS + 1))

    def test_admission_bound_and_shutdown_interrupt_slow_clients(self):
        sockets=[]
        try:
            for _ in range(self.app.MAX_REQUESTS):
                s=socket.create_connection(('127.0.0.1',self.server.server_port))
                s.sendall(b'POST /process HTTP/1.0\r\nContent-Length: 10\r\n\r\n');sockets.append(s)
            deadline=time.monotonic()+1
            while len(self.server.active)<4 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(len(self.server.active), 4)
            self.assertEqual(self.request('/metrics')[0], 503)
            self.assertLessEqual(len(self.server.executor._threads),4)
            self.server.shutdown();start=time.monotonic();self.server.server_close()
            self.assertLess(time.monotonic()-start,1)
        finally:
            for s in sockets:s.close()


if __name__=='__main__':unittest.main()
