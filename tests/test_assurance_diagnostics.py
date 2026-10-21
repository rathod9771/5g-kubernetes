"""Offline bounded subprocess failures and sanitized diagnostics."""
import contextlib,errno,json,os,select,signal,sqlite3,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.kubernetes import KubernetesReader,KubernetesReadError
from assurance.diagnostics import diagnostic
from assurance.periodic import Trial
from assurance.store import Store
from assurance.locking import exclusive

def exited_by(pid, deadline):
    """True if a killed non-child process exits (zombie or reaped) before deadline.

    Its /proc state changes asynchronously (R while exiting, Z, X, then gone), so
    wait on a pidfd instead of sampling /proc. Callers set the deadline well before
    the process could exit on its own, so only cleanup can satisfy it."""
    try:fd=os.pidfd_open(pid)
    except OSError as error:
        # ESRCH: reaped. EINVAL: the PID no longer has a process leader (being released).
        if error.errno not in (errno.ESRCH,errno.EINVAL):raise
        return time.monotonic()<deadline
    try:return bool(select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]) and time.monotonic()<deadline
    finally:os.close(fd)

class DiagnosticTests(unittest.TestCase):
    def run_failure(self,code=None,command=None,**kwargs):
        reader=KubernetesReader({},'',**kwargs)
        with self.assertRaises(KubernetesReadError) as caught:reader.run_json(command or [sys.executable,'-c',code])
        report=caught.exception.diagnostic
        self.assertIs(reader.stats['diagnostics'][-1],report)
        self.assertLess(len(json.dumps(report)),2048)
        return report
    def test_startup(self):
        d=self.run_failure(command=['/nonexistent/offline-reader']);self.assertEqual(d['stage'],'startup');self.assertIsNone(d['exit_code'])
    def test_nonzero_exit(self):
        d=self.run_failure('import sys;sys.exit(7)');self.assertEqual(d['exit_code'],7);self.assertEqual(d['stage'],'child_exit')
    def test_python_exception(self):
        d=self.run_failure('raise ValueError("subscriber-secret")');self.assertEqual(d['stderr_excerpt'],'Python exception class: ValueError');self.assertNotIn('subscriber-secret',json.dumps(d))
    def test_timeout(self):
        d=self.run_failure('import time;time.sleep(20)',timeout=.05);self.assertTrue(d['timeout']);self.assertEqual(d['cleanup_signal'],9)
    def test_child_sigterm(self):
        d=self.run_failure('import os,signal;os.kill(os.getpid(),signal.SIGTERM)');self.assertEqual(d['terminating_signal'],15)
    def test_child_sigint(self):
        d=self.run_failure('import os,signal;os.kill(os.getpid(),signal.SIGINT)');self.assertEqual(d['terminating_signal'],2)
    def test_rss_watchdog(self):
        d=self.run_failure('import time;x=bytearray(40*1024*1024);time.sleep(3)',max_rss=24*1024*1024);self.assertEqual(d['watchdog'],'rss_watchdog')
    def test_cpu_signal_classification(self):
        d=self.run_failure('import resource\nresource.setrlimit(resource.RLIMIT_CPU,(1,2))\nwhile True:pass',timeout=5)
        self.assertEqual(d['terminating_signal'],signal.SIGXCPU)
        self.assertEqual(d['watchdog'],'cpu_limit_signal')
    def test_missing_output(self):self.assertEqual(self.run_failure('pass')['stage'],'decode_output')
    def test_malformed_output(self):self.assertEqual(self.run_failure('print("bad-json-secret")')['exception_class'],'JSONDecodeError')
    def test_output_watchdog(self):
        d=self.run_failure('print("secret"*10000)',max_output=1000);self.assertEqual(d['watchdog'],'output_watchdog');self.assertNotIn('secret',json.dumps(d))
    def test_parent_cancellation(self):
        event=threading.Event();event.set();reader=KubernetesReader({},'')
        with self.assertRaises(KubernetesReadError) as caught:reader.run_json([sys.executable,'-c','import time;time.sleep(30)'],event)
        self.assertEqual(caught.exception.diagnostic['shutdown_reason'],'parent_cancellation')
    def test_measurement_fail_closed(self):
        with patch('assurance.kubernetes.tree_rss',side_effect=KubernetesReadError('RSS unavailable for live process')):
            d=self.run_failure('import time;time.sleep(30)')
        self.assertEqual(d['watchdog'],'memory_measurement')
    def test_sanitization_unknown_content(self):
        secret='Bearer password=123 IMSI-999 SUPI-456 https://user:pass@host'
        d=diagnostic(RuntimeError(secret),'collection',stderr=(secret*10000).encode());self.assertNotIn(secret,json.dumps(d));self.assertEqual(d['stderr_inspection_limit'],4096)
    def test_cleanup_descendant(self):
        with tempfile.TemporaryDirectory() as folder:
            pidfile=Path(folder)/'pid';partial=Path(folder)/'pid.tmp'
            code='import os,subprocess,sys,time,pathlib\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])\npathlib.Path('+repr(str(partial))+').write_text(str(p.pid))\nos.replace('+repr(str(partial))+','+repr(str(pidfile))+')\ntime.sleep(30)'
            # On a loaded host the 0.3 s timeout can expire before the descendant is
            # recorded; that run proves nothing, so repeat (bounded) until one is conclusive.
            for _ in range(3):
                pidfile.unlink(missing_ok=True)
                started=time.monotonic();self.run_failure(code,timeout=.3)
                if pidfile.exists():break
            else:self.fail('descendant never started before the reader timeout')
            self.assertTrue(exited_by(int(pidfile.read_text()),started+15))  # sleeps 30 s unless killed
    def test_trial_error_record_and_lock_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'assurance.sqlite')
            with store.transaction() as db:store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            def fail(_):raise ValueError('password=secret')
            result=Trial(root,root/'evidence','http://offline.invalid',max_cycles=2,execute=fail).run()
            self.assertEqual(len(result['cycles']),1);self.assertEqual(result['cycles'][0]['diagnostic']['stage'],'collection');self.assertNotIn('password=secret',json.dumps(result))
            with exclusive(root/'.l3-periodic.lock'):pass
    def test_required_source_failure_stops_without_backoff(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'assurance.sqlite')
            with store.transaction() as db:store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            result=Trial(root,root/'evidence','http://offline.invalid',max_cycles=2,
                         execute=lambda _: {},stop_on_source_error=True).run()
            self.assertEqual(len(result['cycles']),1)
            self.assertEqual(result['cycles'][0]['status'],'SOURCE_UNAVAILABLE')
            self.assertEqual(result['reason'],'process_failure')
    def test_bounded_error_propagation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'assurance.sqlite')
            with store.transaction() as db:store.put(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            def fail(_):
                KubernetesReader({},'').run_json([sys.executable,'-c','raise RuntimeError("private")'])
            result=Trial(root,root/'evidence','http://offline.invalid',execute=fail).run()
            self.assertEqual(result['cycles'][0]['status'],'BOUNDED_PROCESS_FAILED');self.assertEqual(result['cycles'][0]['diagnostic']['exit_code'],1)

if __name__=='__main__':unittest.main()
