"""Local monitoring resource controls; no cluster or lifecycle operations."""
import concurrent.futures
import importlib.util
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ran-selector'))
import monitoring


class MonitoringTests(unittest.TestCase):
    def test_expiration(self):
        now = [0]
        cache = monitoring.Cache(clock=lambda: now[0])
        calls = []
        def read():
            calls.append(1)
            return len(calls)
        self.assertEqual(cache.get('x', 5, read), 1)
        self.assertEqual(cache.get('x', 5, read), 1)
        now[0] = 5
        self.assertEqual(cache.get('x', 5, read), 2)

    def test_concurrent_identical_requests(self):
        cache = monitoring.Cache()
        calls = []
        def read():
            calls.append(1)
            time.sleep(.1)
            return 'real result'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: cache.get('x', 5, read), range(8)))
        self.assertEqual(calls, [1])
        self.assertEqual(results, ['real result']*8)

    def test_exception_not_cached(self):
        cache = monitoring.Cache()
        with self.assertRaises(ValueError):
            cache.get('x', 5, lambda: (_ for _ in ()).throw(ValueError()))
        self.assertEqual(cache.get('x', 5, lambda: 'recovered'), 'recovered')

    def test_authorization_keys_separate(self):
        cache = monitoring.Cache()
        self.assertEqual(cache.get(('user-a', 'x'), 5, lambda: 'a'), 'a')
        self.assertEqual(cache.get(('user-b', 'x'), 5, lambda: 'b'), 'b')

    def test_invalidation_during_read(self):
        cache = monitoring.Cache()
        def read():
            cache.clear()
            return 'old'
        cache.get('x', 5, read)
        self.assertEqual(cache.get('x', 5, lambda: 'new'), 'new')

    def test_timeout_kills_pipe_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'child'
            code = 'echo $$ > '+str(path)+'; sleep 30'
            import shlex
            result = monitoring.run(shlex.join(['sh', '-c', code])+' | cat', shell=True, timeout=1)
            self.assertEqual(result.returncode, 124)
            pid = int(path.read_text())
            # A killed descendant can briefly remain a zombie, but cannot run.
            stat = Path('/proc')/str(pid)/'stat'
            self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z')
            self.assertNotIn(code, result.stderr)

    def test_output_is_bounded_without_returning_payload(self):
        result=monitoring.run([sys.executable,'-c','print("PRIVATE_PAYLOAD"*200000)'])
        self.assertEqual(result.returncode,124)
        self.assertEqual(result.stdout,'')
        self.assertIn('output limit',result.stderr)
        self.assertNotIn('PRIVATE_PAYLOAD',result.stderr)

    def test_success(self):
        result = monitoring.run([sys.executable, '-c', 'print("ok")'])
        self.assertEqual((result.returncode, result.stdout), (0, 'ok\n'))

    def test_no_lifecycle_subprocess_replacement(self):
        source = (ROOT/'ran-selector/backend.py').read_text()
        lifecycle = source[source.index('def _deploy_verified'):source.index('@app.route("/api/verify-clean")')]
        self.assertNotIn('monitoring.run', lifecycle)
        self.assertIn('wait_for_op(op_id, timeout=180)', lifecycle)

    def test_frontend_guards(self):
        source = (ROOT/'ran-selector/index.html').read_text()
        self.assertIn('document.hidden||running', source)
        self.assertIn('panel?.minimized', source)
        self.assertIn('clearInterval(timer)', source)
        self.assertIn('pendingRequests.has(key)', source)

    def test_historical_logs_not_current_state(self):
        import test_pending_adoption as adoption
        from unittest import mock
        b = adoption.backend
        with b.app.test_request_context('/api/ue-status'), mock.patch.object(b, '_namespace', return_value='test'), \
             mock.patch.object(b, 'run', side_effect=[(0,'ue-pod',''),(1,'',''),(0,'Registration complete\nReceived PDU Session Establishment Accept','')]), \
             mock.patch.object(b, '_prom_query', return_value=None), mock.patch.object(b, '_monitor_scope', return_value='test-history'):
            data = b._ue_status_payload()
        self.assertFalse(data['registered'])
        self.assertFalse(data['pdu_session'])
        self.assertTrue(data['historical_registration'])
        self.assertEqual(data['registration_state'], 'unknown')
        self.assertTrue({'registered','pdu_session','tun','pod','radio_connected','rsrp_dbm'} <= data.keys())

    def test_browser_polling_and_fetch_coalescing(self):
        import subprocess, json, shutil
        if not shutil.which('node'):
            self.skipTest('Node unavailable')
        html = (ROOT/'ran-selector/index.html').read_text()
        source = html[html.index('// Bound automatic monitoring'):html.index('// Persistent read-only functionality workspace.')]
        harness = r"""
const vm=require('vm'),assert=require('assert');let tick,calls=0,release;
const panel={minimized:false,timers:new Set()};
const sandbox={AbortController,Promise,console,setTimeout,clearTimeout,
 document:{hidden:false,addEventListener(){}},loadPods(){},refreshLiveBar(){},
 Workspace:{panels:new Map([['x',panel]])},
 setInterval(fn){tick=fn;return 1;},
 fetch(){calls++;return Promise.resolve({clone(){return {ok:true};}});}};
sandbox.window=sandbox;vm.createContext(sandbox);vm.runInContext(JSON.parse(require('fs').readFileSync(0,'utf8')),sandbox);
(async()=>{
 const a=sandbox.fetch('/api/pods'),b=sandbox.fetch('/api/pods');await Promise.all([a,b]);assert.equal(calls,1);
 await Promise.all([sandbox.fetch('/api/deploy',{method:'POST'}),sandbox.fetch('/api/deploy',{method:'POST'})]);assert.equal(calls,3);
 let runs=0;sandbox.setInterval(()=>{runs++;return new Promise(r=>release=r);},5000);panel.timers.add(1);
 const pending=tick();await tick();assert.equal(runs,1);release();await pending;
 sandbox.document.hidden=true;await tick();assert.equal(runs,1);
 sandbox.document.hidden=false;panel.minimized=true;await tick();assert.equal(runs,1);
 panel.minimized=false;const resumed=tick();assert.equal(runs,2);release();await resumed;
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node','-e',harness], input=json.dumps(source), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_cache_bounded(self):
        cache = monitoring.Cache(limit=2)
        for n in range(5):
            cache.get(n, 5, lambda: n)
        self.assertEqual(len(cache.values), 2)

    def test_capacity_returns_safe_error(self):
        from unittest import mock
        with mock.patch.object(monitoring.slots, 'acquire', return_value=False), mock.patch.object(monitoring.subprocess,'Popen') as popen:
            result = monitoring.run(['kubectl','get','pods'])
        self.assertEqual(result.returncode,124)
        self.assertIn('capacity',result.stderr)
        popen.assert_not_called()

    def test_cache_invalidated_after_mutation_response(self):
        import test_pending_adoption as adoption
        b=adoption.backend
        monitoring.cache.clear()
        monitoring.cache.get('before',10,lambda:'old')
        with b.app.test_request_context('/api/deploy',method='POST'):
            b.invalidate_monitoring(b.app.response_class('{}'))
        self.assertNotIn('before',monitoring.cache.values)

    def test_request_capacity_does_not_limit_deployment(self):
        import test_pending_adoption as adoption
        b=adoption.backend
        with b.app.test_request_context('/api/deploy',method='POST'):
            self.assertIsNone(b.monitoring_capacity())
        held=[]
        try:
            while b._monitor_requests.acquire(blocking=False):
                held.append(1)
            with b.app.test_request_context('/api/pods'):
                response=b.monitoring_capacity()
                self.assertEqual(response[1],503)
        finally:
            for _ in held:b._monitor_requests.release()
