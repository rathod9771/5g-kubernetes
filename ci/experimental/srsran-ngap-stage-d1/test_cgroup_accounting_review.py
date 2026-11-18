"""Simulated cgroup turnover; never touches a host cgroup or invokes systemd."""
import unittest
from build_policy import Blocked, LIMITS
from cgroup_accounting_review import select_review_accounting,REQUIRED


def valid():
    return dict(zip(REQUIRED, (str(LIMITS['memory.max']), str(LIMITS['memory.swap.max']), LIMITS['cpu.max'], str(LIMITS['pids.max']), '609038336', '10', 'usage_usec 12', 'oom_kill 0', 'max 0', 'populated 1\nfrozen 0')))

class AccountingTests(unittest.TestCase):
    def call(self,snapshot=None,**kw):
        params={'terminal_group_exists':False,'last_live_snapshot':valid() if snapshot is None else snapshot,
                'last_live_path':'/sys/fs/cgroup/system.slice/l3-d2a-review.service',
                'last_properties':{'Result':'exit-code','ExecMainCode':'1','ExecMainStatus':'78'},
                'exit_code':78,'signal':None,'timeout':False}
        params.update(kw)
        return select_review_accounting(**params)
    def test_expected_review_stop_is_explicitly_non_terminal(self):
        snap,mode=self.call();self.assertEqual(snap,valid());self.assertIn('NO_NATIVE_ADMISSION',mode)
    def test_success_never_accepts_stale_readback(self):
        with self.assertRaisesRegex(Blocked,'FINAL_CGROUP'):self.call(exit_code=0)
    def test_different_worker_error_rejected(self):
        with self.assertRaisesRegex(Blocked,'FINAL_CGROUP'):self.call(exit_code=77)
    def test_signal_rejected(self):
        with self.assertRaises(Blocked):self.call(signal=9)
    def test_timeout_rejected(self):
        with self.assertRaises(Blocked):self.call(timeout=True)
    def test_cgroup_present_requires_fresh_sample(self):
        with self.assertRaisesRegex(Blocked,'FRESH_CAPTURE'):self.call(terminal_group_exists=True)
    def test_no_sample_rejected(self):
        with self.assertRaises(Blocked):self.call(snapshot={})
    def test_empty_path_rejected(self):
        with self.assertRaises(Blocked):self.call(last_live_path=None)
    def test_nonpopulated_sample_rejected(self):
        snap=valid();snap['cgroup.events']='populated 0'
        with self.assertRaises(Blocked):self.call(snapshot=snap)
    def test_resource_limits_must_match(self):
        for key in ('memory.max','memory.swap.max','cpu.max','pids.max'):
            with self.subTest(key=key):
                snap=valid();snap[key]='12345'
                with self.assertRaises(Blocked):self.call(snapshot=snap)
    def test_oom_rejected(self):
        snap=valid();snap['memory.events']='oom_kill 1'
        with self.assertRaises(Blocked):self.call(snapshot=snap)
    def test_pids_limit_rejected(self):
        snap=valid();snap['pids.events']='max 1'
        with self.assertRaises(Blocked):self.call(snapshot=snap)
    def test_missing_expected_unit_properties_rejected(self):
        with self.assertRaises(Blocked):self.call(last_properties={'Result':'success'})

if __name__=='__main__': unittest.main()
