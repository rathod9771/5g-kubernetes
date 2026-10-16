"""Offline bounded subprocess failures and sanitized diagnostics."""
import contextlib,json,os,signal,sqlite3,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.kubernetes import KubernetesReader,KubernetesReadError
from assurance.diagnostics import diagnostic
from assurance.periodic import Trial
from assurance.store import Store
from assurance.locking import exclusive

def descendant_state(stat_path):
    """Read a /proc/<pid>/stat process-state char, tolerating the pid vanishing.

    Both FileNotFoundError (ENOENT: the /proc/<pid> entry is already gone) and
    ProcessLookupError (ESRCH: the kernel can still raise this from a read() on
    /proc/<pid>/stat if the task is reaped during the read itself, not just
    between an earlier exists() check and this read) mean the process is gone,
    which is a legitimate cleanup outcome -- not a test failure. Any other
    exception is a real, unrelated problem and must still propagate.
    """
    try:
        return stat_path.read_text().rsplit(')', 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


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
            pidfile=Path(folder)/'pid'
            code='import subprocess,sys,time,pathlib\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])\npathlib.Path('+repr(str(pidfile))+').write_text(str(p.pid))\ntime.sleep(30)'
            self.run_failure(code,timeout=.3)
            pid=int(pidfile.read_text());p=Path('/proc',str(pid),'stat')
            state=descendant_state(p)
            # Both outcomes below are legitimate: the killed descendant may
            # already be fully reaped (state is None), or still present as a
            # zombie awaiting reaping. A verified surviving, running
            # descendant (any other state) still fails this assertion.
            if state is not None:self.assertEqual(state,'Z')

    def test_descendant_state_toctou_and_survivor_handling(self):
        # Deterministic coverage for descendant_state()'s race tolerance
        # (test_cleanup_descendant above depends on a real kernel-timing race
        # that may not reproduce on every run/host).
        class StatStub:
            def __init__(self,outcome):self.outcome=outcome
            def read_text(self):
                if isinstance(self.outcome,BaseException):raise self.outcome
                return self.outcome
        # Already reaped before the read (the pre-existing exists()-then-read
        # gap): ENOENT.
        self.assertIsNone(descendant_state(StatStub(FileNotFoundError())))
        # Reaped during the read itself (the TOCTOU race this fix targets): ESRCH.
        self.assertIsNone(descendant_state(StatStub(ProcessLookupError())))
        # Zombie, awaiting reaping: also a legitimate cleanup outcome.
        self.assertEqual(descendant_state(StatStub('123 (python3) Z 1 0 0 0 -1 0 0')),'Z')
        # A verified surviving, still-running descendant must be reported,
        # never conflated with the reaped/zombie cases above.
        self.assertEqual(descendant_state(StatStub('123 (python3) R 1 0 0 0 -1 0 0')),'R')
        # Unrelated exceptions must never be hidden.
        with self.assertRaises(PermissionError):descendant_state(StatStub(PermissionError()))
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
