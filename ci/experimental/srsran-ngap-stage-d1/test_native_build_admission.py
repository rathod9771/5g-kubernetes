"""Offline exact-pin and native terminal-cgroup admission regressions."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from build_policy import Blocked
import native_build_admission as n

def fixture():
    graph='reviewed commands\n'
    targets=['lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5','lib/sbi/openapi/libogssbi-openapi.so.2.7.5']
    pins={};records={}
    for i,target in enumerate(targets):
        command='cc -o '+('one.so' if i==0 else 'two.so')+' foo.o -shared -fPIC'
        pins[target]={'command_sha256':n._sha(command),'argv_sha256':n._sha(json.dumps(command.split(),ensure_ascii=True,separators=(',',':'))),'object_count':1}
        records[target]={'command_sha256':pins[target]['command_sha256'],'argv_sha256':pins[target]['argv_sha256'],'object_count':1,'resolved_command':command,'classification':'INDEPENDENT_REVIEW_REQUIRED'}
    review={'classification':'REVIEW_REQUIRED','native_compilation_authorized':False,'build_ninja_sha256':'1'*64,'targets':dict.fromkeys(targets,{})}
    expanded={'classification':'INDEPENDENT_REVIEW_REQUIRED','native_compilation_authorized':False,'build_ninja_sha256':'1'*64,'source':'ninja -t compdb -x c_LINKER_RSP (read-only)','tool_stdout_sha256':'2'*64,'targets':records}
    return graph,review,expanded,pins

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.graph,self.review,self.expanded,pins=fixture()
        for name,value in [('PINNED_OPEN5GS_GRAPH',n._sha(self.graph)),('PINNED_EXPANSION_STDOUT','2'*64),('PINNED_COMMANDS',pins)]:
            p=patch.object(n,name,value);p.start();self.addCleanup(p.stop)
    def admit(self):return n.admit_reviewed_open5gs_graph(self.graph,self.review,self.expanded)
    def test_exact_only_build_admission(self):
        r=self.admit();self.assertEqual(r['classification'],'PINNED_COMMANDS_APPROVED_FOR_ONE_BUILD_ONLY');self.assertFalse(r['protocol_runtime_authorized']);self.assertFalse(r['native_tests_authorized'])
    def test_graph_drift(self):
        with self.assertRaises(Blocked):n.admit_reviewed_open5gs_graph(self.graph+'x',self.review,self.expanded)
    def test_target_set_drift(self):
        self.expanded['targets']['rogue']={}
        with self.assertRaises(Blocked):self.admit()
    def test_tool_sha_drift(self):
        self.expanded['tool_stdout_sha256']='bad'
        with self.assertRaises(Blocked):self.admit()
    def test_wrong_source(self):
        self.expanded['source']='external script'
        with self.assertRaises(Blocked):self.admit()
    def test_manifest_mismatch(self):
        self.expanded['build_ninja_sha256']='0'*64
        with self.assertRaises(Blocked):self.admit()
    def test_status_altered(self):
        self.expanded['native_compilation_authorized']=True
        with self.assertRaises(Blocked):self.admit()
    def test_link_command_mutation(self):
        t=next(iter(self.expanded['targets']));self.expanded['targets'][t]['resolved_command']+=' -fplugin=bad'
        with self.assertRaises(Blocked):self.admit()
    def test_argv_sha_mutation(self):
        t=next(iter(self.expanded['targets']));self.expanded['targets'][t]['argv_sha256']='bad'
        with self.assertRaises(Blocked):self.admit()
    def test_object_count_mutation(self):
        t=next(iter(self.expanded['targets']));self.expanded['targets'][t]['object_count']=99
        with self.assertRaises(Blocked):self.admit()
    def test_review_status_drift(self):
        self.review['classification']='PASS'
        with self.assertRaises(Blocked):self.admit()
    def test_invalid_types(self):
        with self.assertRaises(Blocked):n.admit_reviewed_open5gs_graph(None,self.review,self.expanded)
    def test_published_pins(self):
        self.assertEqual(len(n.PINNED_COMMANDS),2)
        self.assertEqual(n.PINNED_OPEN5GS_GRAPH,'9f086ee1c67d2716a69b5ccf45379231897c29a059f2c51d2d3bc5d257e8fd6c')

class TerminalTests(unittest.TestCase):
    def make(self,dirname):
        p=Path(dirname)/'post-worker-guard-accounting.json'
        obj={'classification':'POST_WORKER_SNAPSHOT_REQUIRES_TERMINAL_CONFIRMATION',
             'proof':{'worker_exit_code':0,'mode':'POST_WORKER_QUIESCENT_GUARD_HELD','worker_accounting_complete':True,
                      'terminal_cgroup_empty':False,'native_compilation_authorized':False}}
        p.write_text(json.dumps(obj))
        f={'exit_code':0,'signal':None,'timeout':False,'systemd_result':'success',
           'evidence_sha256':{'post-worker-guard-accounting.json':hashlib.sha256(p.read_bytes()).hexdigest()}}
        usage={'accounting_mode':'POST_WORKER_QUIESCENT_GUARD_HELD','terminal_accounting_complete':True,
               'guard_only_at_capture':True,'capture_boundary':'POST_WORKER_PRE_GUARD_EXIT',
               'usage':{'oom_kill':0,'pids_limit_events':0}}
        return p,f,usage
    def test_quiescent_pass(self):
        with tempfile.TemporaryDirectory() as d:
            _,f,u=self.make(d);self.assertTrue(n.verify_native_terminal_guard(d,f,u))
    def test_missing_proof_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p,f,u=self.make(d);p.unlink()
            with self.assertRaises(Blocked):n.verify_native_terminal_guard(d,f,u)
    def test_unbound_proof_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p,f,u=self.make(d);p.write_text('{}')
            with self.assertRaises(Blocked):n.verify_native_terminal_guard(d,f,u)
    def test_last_live_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p,f,u=self.make(d);u['accounting_mode']='LAST_LIVE_ONLY__TERMINAL_MISSING__NO_NATIVE_ADMISSION'
            with self.assertRaises(Blocked):n.verify_native_terminal_guard(d,f,u)
    def test_wrong_worker_exit_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p,f,u=self.make(d);f['exit_code']=78
            with self.assertRaises(Blocked):n.verify_native_terminal_guard(d,f,u)
    def test_resource_hit_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p,f,u=self.make(d);u['usage']['oom_kill']=1
            with self.assertRaises(Blocked):n.verify_native_terminal_guard(d,f,u)
