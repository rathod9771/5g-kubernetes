"""Controlled /proc trees and real local allocations; no platform calls."""
import json,mmap,os,resource,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance.memory import tree_memory,process_memory,MemoryMeasurementError,cgroup_memory
from assurance.kubernetes import KubernetesReader,KubernetesReadError

class MemoryTests(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);return Path(tmp.name)
    def proc(self,root,pid,parent,children='',birth='10',rss=100):
        base=root/str(pid);(base/'task'/str(pid)).mkdir(parents=True,exist_ok=True)
        fields=['S',str(parent)]+['0']*17+[birth]
        (base/'stat').write_text(str(pid)+' (name) '+' '.join(fields))
        (base/'status').write_text('State:\tS\nVmRSS:\t%s kB\nVmHWM:\t%s kB\nVmSize:\t10000 kB\n'%(rss,rss))
        (base/'task'/str(pid)/'children').write_text(children)
        return base
    def test_parent_children_shared_rss_pss(self):
        root=self.fixture();a=self.proc(root,1,0,'2 3');b=self.proc(root,2,1);c=self.proc(root,3,1)
        for base in (a,b,c):(base/'smaps_rollup').write_text('Pss:\t60 kB\n')
        result=tree_memory(1,proc=root,pss=True)
        self.assertEqual(result['rss_sum_bytes'],300*1024);self.assertEqual(result['pss_sum_bytes'],180*1024)
    def test_nonleader_thread_children(self):
        root=self.fixture();a=self.proc(root,1,0);self.proc(root,2,1)
        (a/'task'/'20').mkdir();(a/'task'/'20'/'children').write_text('2')
        self.assertEqual(len(tree_memory(1,proc=root)['processes']),2)
    def test_process_exit_explicit(self):
        root=self.fixture();self.proc(root,1,0,'2')
        result=tree_memory(1,proc=root);self.assertEqual(result['exited_during_sample'],[2])
    def test_pid_reuse_cannot_replace_identity(self):
        root=self.fixture();self.proc(root,1,0,birth='new');identities={1:'old'}
        with self.assertRaisesRegex(MemoryMeasurementError,'PID reuse'):tree_memory(1,identities,proc=root)
        self.assertEqual(identities[1],'old')
    def test_wrong_parent_excluded_fail_closed(self):
        root=self.fixture();self.proc(root,1,0,'2');self.proc(root,2,99)
        with self.assertRaisesRegex(MemoryMeasurementError,'parent changed'):tree_memory(1,proc=root)
    def test_missing_rss_not_zero(self):
        root=self.fixture();p=self.proc(root,1,0);(p/'status').write_text('State: S\n')
        with self.assertRaisesRegex(MemoryMeasurementError,'RSS unavailable'):tree_memory(1,proc=root)
    def test_exit_flag_proves_missing_address_space(self):
        root=self.fixture();p=self.proc(root,1,0);(p/'status').write_text('State: R\n')
        fields=(p/'stat').read_text().rsplit(')',1)[1].split();fields[6]='4'
        (p/'stat').write_text('1 (name) '+' '.join(fields))
        result=process_memory(1,proc=root)
        self.assertEqual(result['rss_bytes'],0)
        self.assertEqual(result['measurement_status'],'EXITING_NO_ADDRESS_SPACE')
    def test_negative_rss_fails_closed(self):
        root=self.fixture();p=self.proc(root,1,0);(p/'status').write_text('State: S\nVmRSS: -1 kB\n')
        with self.assertRaises(MemoryMeasurementError):tree_memory(1,proc=root)
    def test_permission_error_is_measurement_error(self):
        with patch('assurance.memory.process_memory',side_effect=PermissionError()):
            with self.assertRaises(MemoryMeasurementError):tree_memory(os.getpid())
    def test_process_and_thread_budgets(self):
        root=self.fixture();self.proc(root,1,0,'2');self.proc(root,2,1)
        for args in ({'max_processes':1},{'max_tasks':1}):
            with self.assertRaises(MemoryMeasurementError):tree_memory(1,proc=root,**args)
    def test_optional_pss_explicit_unavailable(self):
        root=self.fixture();self.proc(root,1,0)
        result=tree_memory(1,proc=root,pss=True);self.assertIsNone(result['pss_sum_bytes']);self.assertEqual(result['processes'][0]['pss_status'],'UNAVAILABLE')
    def test_real_shared_fork_mapping(self):
        size=8*1024*1024;mapping=mmap.mmap(-1,size)
        for offset in range(0,size,4096):mapping[offset]=1
        pid=os.fork()
        if pid==0:
            for offset in range(0,size,4096):unused=mapping[offset]
            time.sleep(5);os._exit(0)
        try:
            time.sleep(.05);rows=[process_memory(os.getpid(),pss=True),process_memory(pid,pss=True)]
            if all(r['pss_bytes'] is not None for r in rows):self.assertGreater(sum(r['rss_bytes'] for r in rows),sum(r['pss_bytes'] for r in rows))
        finally:os.kill(pid,9);os.waitpid(pid,0);mapping.close()
    def test_spike_watchdog_violation(self):
        reader=KubernetesReader({},'',max_rss=32*1024*1024)
        code='import time\nx=bytearray(48*1024*1024)\ntime.sleep(2)'
        with self.assertRaisesRegex(KubernetesReadError,'RSS budget'):reader.run_json([sys.executable,'-c',code])
    def test_measurement_failure_does_not_disable_watchdog(self):
        with patch('assurance.memory.tree_memory',side_effect=MemoryMeasurementError('inaccessible')):
            with self.assertRaisesRegex(KubernetesReadError,'Private exception details suppressed') as caught:
                KubernetesReader({},'').run_json([sys.executable,'-c','import time;time.sleep(3)'])
            self.assertEqual(caught.exception.diagnostic['watchdog'],'memory_measurement')
    def test_cancellation(self):
        stop=threading.Event();timer=threading.Timer(.1,stop.set);timer.start()
        try:
            with self.assertRaisesRegex(KubernetesReadError,'shutdown'):
                KubernetesReader({},'').run_json([sys.executable,'-c','import time;time.sleep(3)'],stop_event=stop)
        finally:timer.cancel()
    def test_cgroup_not_available_explicit(self):
        root=self.fixture();self.assertEqual(cgroup_memory(1,root=root,proc=root)['status'],'UNAVAILABLE')
    def test_exec_retains_lifetime_max_not_image_rss(self):
        inner="import json,resource;from pathlib import Path;print(json.dumps({'legacy':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'rss':int(next(l.split()[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('VmRSS:')))}))"
        code='import os,sys\nx=bytearray(64*1024*1024)\nos.execv(sys.executable,[sys.executable,"-c",'+repr(inner)+'])'
        result=json.loads(subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=5,check=True).stdout)
        self.assertGreater(result['legacy'],result['rss']+32*1024)

if __name__=='__main__':unittest.main()
