import json, unittest
from build_policy import Blocked, LIMITS
from cgroup_terminal_guard import validate_guard_status, validate_quiescent_capture, verify_guard_release_end

GUARD = {'schema':1,'guard_pid':313,'worker_pid':314,'worker_returncode':78}
PROPS={'ExecMainPID':'313','SubState':'running'}
FINAL={'ExecMainPID':'313','SubState':'failed','Result':'exit-code'}
def snapshot():
    return {'memory.max':str(LIMITS['memory.max']), 'memory.swap.max':'0',
            'cpu.max':LIMITS['cpu.max'],'pids.max':'256',
            'memory.peak':'65000','pids.peak':'5','cpu.stat':'usage_usec 123',
            'memory.events':'low 0\noom_kill 0','pids.events':'max 0','cgroup.events':'populated 1\nfrozen 0'}

class GuardTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(validate_guard_status(json.dumps(GUARD)+'\nCOMMITTED\n',313),GUARD)
    def test_guard_held_evidence(self):
        r=validate_quiescent_capture(snapshot(),GUARD,'313\n','1',[],PROPS)
        self.assertTrue(r['worker_accounting_complete'])
        self.assertFalse(r['terminal_cgroup_empty'])
        self.assertFalse(r['native_compilation_authorized'])
    def test_release_expected_failure(self):
        self.assertTrue(verify_guard_release_end(GUARD,FINAL,{'exit_code':78,'signal':None,'timeout':False}))
    def test_release_exit_drift(self):
        with self.assertRaises(Blocked):verify_guard_release_end(GUARD,FINAL,{'exit_code':0,'signal':None,'timeout':False})
    def test_extra_process_denied(self):
        with self.assertRaises(Blocked):validate_quiescent_capture(snapshot(),GUARD,'313\n314\n','2',[],PROPS)
    def test_foreign_pid_denied(self):
        with self.assertRaises(Blocked):validate_guard_status(json.dumps(GUARD)+'\nCOMMITTED\n',123)
    def test_thread_count_denied(self):
        with self.assertRaises(Blocked):validate_quiescent_capture(snapshot(),GUARD,'313\n','2',[],PROPS)
    def test_subgroup_denied(self):
        with self.assertRaises(Blocked):validate_quiescent_capture(snapshot(),GUARD,'313\n','1',['descendant'],PROPS)
    def test_cgroup_limit_drift(self):
        for key in ('memory.max','memory.swap.max','cpu.max','pids.max'):
            snap=snapshot();snap[key]='999'
            with self.subTest(key=key),self.assertRaises(Blocked):validate_quiescent_capture(snap,GUARD,'313\n','1',[],PROPS)
    def test_oom_denied(self):
        s=snapshot();s['memory.events']='oom_kill 1'
        with self.assertRaises(Blocked):validate_quiescent_capture(s,GUARD,'313\n','1',[],PROPS)
    def test_worker_signal_denied(self):
        with self.assertRaises(Blocked):verify_guard_release_end(GUARD,FINAL,{'exit_code':None,'signal':9,'timeout':False})
    def test_incomplete_counters_denied(self):
        s=snapshot();s.pop('memory.peak')
        with self.assertRaises(Blocked):validate_quiescent_capture(s,GUARD,'313\n','1',[],PROPS)
    def test_partial_status_denied(self):
        with self.assertRaisesRegex(Blocked,'NOT_COMMITTED'):
            validate_guard_status(json.dumps(GUARD),313)
    def test_pid_status_bad_schema_denied(self):
        st=dict(GUARD,schema=3)
        with self.assertRaises(Blocked):validate_guard_status(json.dumps(st)+'\nCOMMITTED\n',313)
    def test_bool_as_integer_denied(self):
        st=dict(GUARD,worker_returncode=True)
        with self.assertRaises(Blocked):validate_guard_status(json.dumps(st)+'\nCOMMITTED\n',313)

if __name__=='__main__':unittest.main()
