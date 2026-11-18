"""Non-native fixtures and harmless Python children; no cgroups/mounts/network probes."""
import copy,json,os,signal,stat,sys,tempfile,unittest
from types import SimpleNamespace
from pathlib import Path
import build_policy as policy
from build_policy import Blocked
import build_receipts as receipts
import build_supervisor as supervisor
import finalize_build_evidence as finalizer
P=Path(__file__).parent
class Harness(unittest.TestCase):
    def limits(self):return {**{k:True for k in ('cgroup_v2','network_isolated','root_readonly','scratch_capacity_verified','unprivileged','no_capabilities','namespace_private','host_ipc_isolated')},**{k:policy.LIMITS[k] for k in ('memory.max','memory.swap.max','cpu.max','pids.max','disk_bytes')},'accounting':['memory.peak','pids.peak','cpu.stat','memory.events','pids.events','disk_used_peak']}
    def complete(self):
        r={k:{'phase':k,'classification':'PASS','exit_code':0,'signal':None,'started_at':'2026-11-17T00:00:00+00:00','ended_at':'2026-11-17T00:00:01+00:00','elapsed_seconds':1,'log':'phase.log','commands':[],'timeout':False,'validation_completed':True,'command_sha256':'a'*64,'log_sha256':'b'*64,'outputs':{'synthetic':'c'*64},'resource_limit_status':'NOT_APPLICABLE_ACQUISITION_ONLY' if k=='dependency_acquisition' else 'VERIFIED'} for k in policy.PHASES}
        for phase,count in {'dependency_acquisition':18,'patch_application':2,'cmake_configuration':1,'meson_configuration':1,'srsran_compilation':1,'open5gs_compilation':1}.items():
            r[phase]['commands']=[{'argv':['/usr/bin/synthetic-tool'],'started_at':'2026-11-17T00:00:00Z','ended_at':'2026-11-17T00:00:00Z','elapsed_seconds':0,'exit_code':0,'signal':None,'timeout':False,'forced':False,'log_budget_exceeded':False,'classification':'PASS','log':'synthetic.log','log_sha256':'a'*64} for _ in range(count)]
        return [r,{k:'SYNTHETIC_VERSION' for k in policy.VERSIONS},{'synthetic-elf':{'sha256':'a'*64,'readelf_sha256':'b'*64,'objdump_sha256':'c'*64}},{'exit_code':0,'signal':None,'timeout':False,'supervisor_completed':True,'systemd_result':'success','observed_properties':{'ExecMainCode':'1','ExecMainStatus':'0','SubState':'exited'},'started_at':'2026-11-17T00:00:00+00:00','ended_at':'2026-11-17T00:00:01+00:00','elapsed_seconds':1},{'memory_peak_bytes':10,'cpu_usage_usec':20,'pids_peak':2,'disk_used_peak_bytes':30,'oom_kill':0,'pids_limit_events':0}]
    def test_positive_limit_readbacks(self):self.assertTrue(policy.resource_admission(self.limits()))
    def test_missing_memory_limit(self):
        x=self.limits();x['memory.max']='max'
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_missing_disk_limit(self):
        x=self.limits();x['scratch_capacity_verified']=False
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_missing_cpu_limit(self):
        x=self.limits();x['cpu.max']='max 100000'
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_missing_pids_limit(self):
        x=self.limits();x['pids.max']='max'
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_missing_usage_evidence(self):
        x=self.complete();del x[4]['pids_peak']
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_missing_accounting_capability(self):
        x=self.limits();x['accounting'].remove('memory.peak')
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_missing_network_isolation_blocks(self):
        x=self.limits();x['network_isolated']=False
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_writable_root_blocks(self):
        x=self.limits();x['root_readonly']=False
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_complete_positive(self):self.assertTrue(policy.verify_complete(*self.complete()))
    def test_missing_compiler_version(self):
        x=self.complete();del x[1]['cc']
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_missing_linker_version(self):
        x=self.complete();del x[1]['ld']
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_missing_dependency_inventory(self):
        x=self.complete();del x[2]['synthetic-elf']['readelf_sha256']
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_missing_phase_receipt(self):
        x=self.complete();del x[0]['cmake_configuration']
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_nonzero_compiler_receipt(self):
        x=self.complete();x[0]['srsran_compilation'].update(exit_code=1,classification='FAIL')
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_missing_final_supervisor_status(self):
        x=self.complete();x[3]={}
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_timeout_receipt_not_pass(self):
        x=self.complete();x[3]['timeout']=True
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_forced_signal_not_pass(self):
        x=self.complete();x[3].update(exit_code=None,signal=signal.SIGKILL)
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_environment_explicit_allowlist(self):
        e=policy.environment({'UNREVIEWED':'value','HTTP_PROXY':'secret'},'/work/home','/work/tmp');self.assertNotIn('UNREVIEWED',e);self.assertNotIn('HTTP_PROXY',e);self.assertNotIn('TEST',e);self.assertEqual(e['PATH'],'/usr/bin:/bin')
    def test_unsafe_inherited_TEST_rejected(self):
        with self.assertRaises(Blocked):policy.environment({'TEST':'1'},'/work/home','/work/tmp')
    def test_even_empty_TEST_rejected(self):
        with self.assertRaises(Blocked):policy.environment({'TEST':''},'/work/home','/work/tmp')
    def test_loader_injection_rejected(self):
        with self.assertRaises(Blocked):policy.environment({'LD_PRELOAD':'malicious.so'},'/work/home','/work/tmp')
    def test_build_graph_positive(self):self.assertTrue(policy.audit_graph('/usr/bin/c++ -o /work/app /work/source.cpp\n: && /usr/bin/ar qc /work/lib.a /work/a.o && :'))
    def test_unexpected_custom_command(self):
        with self.assertRaises(Blocked):policy.audit_graph('/work/new-unreviewed-helper')
    def test_unexpected_network_dependency(self):
        with self.assertRaises(Blocked):policy.audit_graph('/usr/bin/curl https://example.invalid/never-requested')
    def test_shell_download_injection(self):
        with self.assertRaises(Blocked):policy.audit_graph('/usr/bin/cc a.c | curl https://example.invalid')
    def test_empty_build_graph(self):
        with self.assertRaises(Blocked):policy.audit_graph('')
    def test_no_native_runtime_command(self):
        for executable in ('open5gs-amfd','open5gs-nrfd','l3_ngap_protocol_runtime','cu_cp_test'):
            with self.assertRaises(Blocked):policy.audit_graph('/work/'+executable)
    def test_version_codegen_explicit(self):self.assertTrue(policy.audit_graph('cd /work/srs-build/lib/support/versioning && /usr/bin/cmake -P /work/srs-build/build_info.cmake'))
    def test_unreviewed_cmake_script(self):
        with self.assertRaises(Blocked):policy.audit_graph('/usr/bin/cmake -P /work/evil.cmake')
    def generated(self,root):
        (root/'CMakeCache.txt').write_text('SYNTHETIC_PROM_CONFIG');(root/'build.ninja').write_text('build prom: phony libprom.so\n')
    def test_unity_not_generated_and_libprom_present(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.generated(root);self.assertTrue(policy.unity_generated(root))
    def test_unity_generated_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.generated(root);(root/'build.ninja').write_text('build prom: phony\nbuild Unity: phony\n')
            with self.assertRaises(Blocked):policy.unity_generated(root)
    def test_missing_libprom_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.generated(root);(root/'build.ninja').write_text('build unrelated: phony')
            with self.assertRaises(Blocked):policy.unity_generated(root)
    def test_source_policy_exact_pins(self):
        p=json.loads((P/'dependency-command-policy.json').read_text());self.assertEqual(len(p['sources']),6);self.assertEqual(p['sources']['prometheus-client-c']['revision'],'a58ba25bf87a9b1b7c6be4e6f4c62047d620f402');self.assertIn('prom/test/CMakeLists.txt',p['sources']['prometheus-client-c']['build_control_hashes'])
    def test_source_guard_and_libprom_unchanged(self):
        # Pure static policy artifact expectations; authentic source validation is a separate check.
        p=json.loads((P/'dependency-command-policy.json').read_text());self.assertEqual(p['selected_dependency_paths']['prometheus-client-c'],'CMake root -> prom ONLY; TEST absent')
    def test_non_native_child_normal(self):
        with tempfile.TemporaryDirectory() as d:
            r=receipts.command([sys.executable,'-c','print("SYNTHETIC_ONLY")'],d,{},Path(d)/'log',2);self.assertEqual(r['classification'],'PASS');self.assertEqual(r['exit_code'],0);self.assertEqual(r['log_sha256'],policy.digest(Path(d)/'log'))
    def test_non_native_child_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            r=receipts.command([sys.executable,'-c','raise SystemExit(7)'],d,{},Path(d)/'log',2);self.assertEqual(r['exit_code'],7);self.assertEqual(r['classification'],'FAIL')
    def test_non_native_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            r=receipts.command([sys.executable,'-c','import time;time.sleep(10)'],d,{},Path(d)/'log',.05);self.assertTrue(r['timeout']);self.assertNotEqual(r['classification'],'PASS')
    def test_forced_non_native_termination(self):
        import subprocess,time
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            ready=Path(d)/'ready';popen=subprocess.Popen
            def ready_popen(*args,**kwargs):
                proc=popen(*args,**kwargs)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if ready.is_file() and ready.read_text()=='READY':return proc
                    if proc.poll() is not None:break
                    time.sleep(.01)
                receipts.terminate_group(proc)
                self.fail('Child did not acknowledge installed SIGTERM handler within 5 seconds')
            code='import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("PARTIAL",flush=True);pathlib.Path('+repr(str(ready))+').write_text("READY");time.sleep(10)'
            with patch.object(receipts.subprocess,'Popen',side_effect=ready_popen):
                r=receipts.command([sys.executable,'-c',code],d,{},Path(d)/'log',.15)
            self.assertTrue(r['forced']);self.assertTrue(r['timeout']);self.assertEqual(r['signal'],signal.SIGKILL);self.assertIn('PARTIAL',(Path(d)/'log').read_text())
    def test_atomic_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.json';receipts.atomic(p,{'classification':'INCOMPLETE'});self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertFalse(p.with_name('r.json.tmp').exists())
    def test_phase_receipt_schema(self):
        import jsonschema
        with tempfile.TemporaryDirectory() as d:
            r=receipts.Recorder(d,{});r.phase('source_verification',[],d);value=json.loads((Path(d)/'source_verification.receipt.json').read_text());jsonschema.validate(value,json.loads((P/'phase-receipt.schema.json').read_text()));self.assertEqual(value['classification'],'PASS')
    def test_phase_failure_preserves_log(self):
        with tempfile.TemporaryDirectory() as d:
            r=receipts.Recorder(d,{})
            with self.assertRaises(receipts.PhaseError):r.phase('srsran_compilation',[[sys.executable,'-c','print("BEFORE_ERROR");raise SystemExit(3)']],d)
            value=json.loads((Path(d)/'srsran_compilation.receipt.json').read_text());self.assertEqual(value['classification'],'FAIL');self.assertEqual(value['exit_code'],3)
    def test_finalizer_missing_status(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Blocked):finalizer.validate(d)
    def test_exit_status_not_invented(self):self.assertIsNone(supervisor.observe_exit({},False)['exit_code'])
    def test_signal_status_preserved(self):self.assertEqual(supervisor.observe_exit({'ExecMainCode':'2','ExecMainStatus':'9'},False)['signal'],9)
    def test_unit_must_have_started(self):self.assertFalse(supervisor.check_terminal({'SubState':'dead','ExecMainStartTimestampMonotonic':'0'}))
    def test_pipeline_no_test_or_nf_execution(self):
        src=(P/'build_worker.py').read_text();self.assertNotIn("['ctest'",src);self.assertIn("['/usr/bin/readelf'",src);self.assertNotIn("['/usr/bin/ldd'",src);self.assertIn('audit_graph(graph)',src)
    def test_user_namespace_required_for_custom_uid_gid(self):
        src=(P/'build_supervisor.py').read_text()
        self.assertIn("argv=['/usr/bin/bwrap','--unshare-user','--unshare-net'",src)
        self.assertIn("'--uid',str(uid),'--gid',str(gid),'--cap-drop','ALL'",src)
        self.assertIn("'--property=NoNewPrivileges=yes'",src)
        self.assertNotIn("'--unshare-user-try'",src)
        self.assertNotIn("'--not-a-security-boundary'",src)
        self.assertIn("'--unshare-ipc'",src)
    def test_supervisor_isolated_hard_limits(self):
        src=(P/'build_supervisor.py').read_text()
        for flag in ('--unshare-net','--ro-bind','--clearenv','NoNewPrivileges=yes','KillMode=control-group','MemoryMax=','CPUQuota=200%','TasksMax=','RuntimeMaxSec=4500s'):self.assertIn(flag,src)
    def test_workflow_never_runs_native_directly(self):
        src=(P/'native-build.WORKFLOW.PROPOSED.yml').read_text();self.assertIn('unshare --mount --propagation private',src);self.assertNotIn('runtime_launcher',src);self.assertIn('contents: read',src);self.assertIn('acquire_sources.py',src);self.assertIn('finalize_build_evidence.py',src)
    def test_corrupt_partial_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'artifact';f.write_text('ORIGINAL_SYNTHETIC');h=policy.digest(f);f.write_text('CORRUPTED');
            with self.assertRaises(Blocked):policy.verify_artifacts(d,{'artifact':h})
    def test_interrupted_log_finalizer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);w=root/'worker';w.mkdir();log=w/'one.log';log.write_text('complete');original=policy.digest(log)
            for phase in policy.PHASES:
                r={'classification':'PASS','exit_code':0,'log':'/work/evidence/one.log','log_sha256':original}
                receipts.atomic(w/(phase+'.receipt.json'),r)
            log.write_text('truncated')
            manifest={str(f.relative_to(root)):policy.digest(f) for f in root.rglob('*') if f.is_file()}
            receipts.atomic(root/'supervisor-final.json',{'status':'PASS_BUILD_ONLY_ZERO_NATIVE_TESTS','exit_code':0,'timeout':False,'signal':None,'evidence_sha256':manifest})
            with self.assertRaisesRegex(Blocked,'CORRUPT_LOG'):finalizer.validate(root)
    def test_background_shell_operation_rejected(self):
        with self.assertRaises(Blocked):policy.audit_graph('/usr/bin/cc source.c & /work/unapproved')
    def test_substituted_compiler_path_rejected(self):
        with self.assertRaises(Blocked):policy.audit_graph('/work/cc source.c')
    def test_linking_curl_is_not_running_curl(self):
        self.assertTrue(policy.audit_graph('/usr/bin/cc -o /work/amf /work/amf.o -lcurl'))
    def test_cmake_unity_compilation_is_not_Unity_library(self):
        self.assertTrue(policy.audit_graph('/usr/bin/c++ -c /work/CMakeFiles/cu_cp.dir/Unity/unity_0_cxx.cxx -o /work/a.o'))
    def test_archive_local_cleanup_only(self):
        self.assertTrue(policy.audit_graph('/usr/bin/cmake -E rm -f lib/libone.a'))
        with self.assertRaises(Blocked):policy.audit_graph('/usr/bin/cmake -E rm -f ../../unrelated.a')
    def test_missing_phase_output_stops(self):
        with tempfile.TemporaryDirectory() as d:
            r=receipts.Recorder(d,{})
            with self.assertRaises(receipts.PhaseError):r.phase('binary_inspection',[],d,outputs=[Path(d)/'absent'])
            value=json.loads((Path(d)/'binary_inspection.receipt.json').read_text());self.assertEqual(value['classification'],'INCOMPLETE')
    def test_log_cap_is_not_fabricated_timeout(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d,patch.object(receipts,'MAX_LOG',1):
            r=receipts.command([sys.executable,'-c','import time;print("OUTPUT",flush=True);time.sleep(1)'],d,{},Path(d)/'log',5)
            self.assertTrue(r['log_budget_exceeded']);self.assertFalse(r['timeout']);self.assertEqual(r['classification'],'BLOCKED')
    def test_supervisor_joins_private_mount_before_worker(self):
        src=(P/'build_supervisor.py').read_text();self.assertIn("'/usr/bin/nsenter','--mount=/proc/'",src);self.assertIn('SUPERVISOR_INTERRUPTED',src)
    def test_finalizer_detects_corrupt_manifest_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);f=root/'diagnostic';f.write_text('original');manifest={'diagnostic':policy.digest(f)};f.write_text('corrupted')
            receipts.atomic(root/'supervisor-final.json',{'status':'PASS_BUILD_ONLY_ZERO_NATIVE_TESTS','exit_code':0,'timeout':False,'signal':None,'evidence_sha256':manifest})
            with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION'):finalizer.validate(root)
    def test_null_version_not_evidence(self):
        x=self.complete();x[1]['ld']=None
        with self.assertRaises(Blocked):policy.verify_complete(*x)
    def test_host_ipc_must_be_isolated(self):
        x=self.limits();x['host_ipc_isolated']=False
        with self.assertRaises(Blocked):policy.resource_admission(x)
    def test_toolchain_lock_positive_fixture(self):
        import toolchain_lock as t
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic-tool';p.write_bytes(b'SYNTHETIC_NOT_A_BINARY')
            lock=t.freeze([d],'synthetic-package 1.0');self.assertTrue(t.verify(lock,'synthetic-package 1.0'))
    def test_toolchain_lock_corruption_blocks(self):
        import toolchain_lock as t
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic-library';p.write_bytes(b'BEFORE');lock=t.freeze([d],'package 1.0');p.write_bytes(b'AFTER')
            with self.assertRaises(Blocked):t.verify(lock,'package 1.0')
    def test_toolchain_version_drift_blocks(self):
        import toolchain_lock as t
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic-header';p.write_text('x');lock=t.freeze([d],'package 1.0')
            with self.assertRaises(Blocked):t.verify(lock,'package 2.0')
    def test_phase_timeout_remains_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            r=receipts.Recorder(d,{})
            with self.assertRaises(receipts.PhaseError) as caught:r.phase('srsran_compilation',[[sys.executable,'-c','import time;time.sleep(10)']],d,timeout=.05)
            self.assertEqual(caught.exception.classification,'INCOMPLETE');self.assertEqual(json.loads((Path(d)/'srsran_compilation.receipt.json').read_text())['classification'],'INCOMPLETE')

class WorkerNamespaceSourceAccess(unittest.TestCase):
    def allowed(self):
        private=SimpleNamespace(st_mode=stat.S_IFDIR|0o711,st_uid=0)
        image=SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1)
        volume=SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=1001,st_gid=1001)
        return private,image,volume

    def test_restricted_worker_scratch_permissions_pass(self):
        self.assertTrue(supervisor.validate_worker_scratch_permissions(*self.allowed(),1001,1001))

    def test_private_parent_must_remain_non_listable_and_root_owned(self):
        for mode,uid in [(0o700,0),(0o755,0),(0o711,1001)]:
            private,image,volume=self.allowed()
            private.st_mode=stat.S_IFDIR|mode;private.st_uid=uid
            with self.subTest(mode=mode,uid=uid),self.assertRaisesRegex(Blocked,'PRIVATE_SCRATCH_PARENT_PERMISSIONS'):
                supervisor.validate_worker_scratch_permissions(private,image,volume,1001,1001)

    def test_backing_image_root_only_and_not_linked(self):
        for mode,uid,nlink in [(0o644,0,1),(0o600,1001,1),(0o600,0,2)]:
            private,image,volume=self.allowed()
            image.st_mode=stat.S_IFREG|mode;image.st_uid=uid;image.st_nlink=nlink
            with self.subTest(mode=mode,uid=uid,nlink=nlink),self.assertRaisesRegex(Blocked,'PRIVATE_SCRATCH_IMAGE_PERMISSIONS'):
                supervisor.validate_worker_scratch_permissions(private,image,volume,1001,1001)

    def test_volume_must_be_exact_runner_owned_and_private(self):
        for mode,uid,gid in [(0o755,1001,1001),(0o700,0,0),(0o700,1001,0)]:
            private,image,volume=self.allowed()
            volume.st_mode=stat.S_IFDIR|mode;volume.st_uid=uid;volume.st_gid=gid
            with self.subTest(mode=mode,uid=uid,gid=gid),self.assertRaisesRegex(Blocked,'PRIVATE_SCRATCH_VOLUME_PERMISSIONS'):
                supervisor.validate_worker_scratch_permissions(private,image,volume,1001,1001)

    def test_privilege_drop_is_after_nsenter_and_before_bwrap(self):
        src=(P/'build_supervisor.py').read_text()
        self.assertIn("'--','/usr/bin/nsenter','--mount=/proc/'",src)
        self.assertIn("'/usr/bin/setpriv','--reuid',str(uid),'--regid',str(gid),'--clear-groups',*argv",src)
        self.assertIn("argv=['/usr/bin/bwrap','--unshare-user','--unshare-net'",src)
        self.assertIn("'--property=NoNewPrivileges=yes'",src)
        self.assertIn("'--cap-drop','ALL'",src)
        self.assertIn("os.chmod(image,0o600);os.chmod(private,0o711)",src)
        self.assertEqual(policy.LIMITS['disk_bytes'],12*1024**3)

class ScratchStorageAdmission(unittest.TestCase):
    def checked(self,**overrides):
        root=Path('/home/runner/work/_temp')
        args={'temp':root,'private':root/'l3-d2a-test','filesystem_type':'ext2/ext3',
              'available_bytes':18*1024**3,'temp_device':42,'private_device':42}
        args.update(overrides)
        return supervisor.validate_scratch_storage(**args)

    def test_disk_backed_exact_headroom_allowed(self):
        result=self.checked()
        self.assertEqual(result['disk_limit_bytes'],12*1024**3)
        self.assertEqual(result['required_free_bytes'],18*1024**3)

    def test_insufficient_free_space_fails_closed(self):
        with self.assertRaisesRegex(Blocked,'DISK_HEADROOM'):
            self.checked(available_bytes=18*1024**3-1)

    def test_tmpfs_fails_closed(self):
        with self.assertRaisesRegex(Blocked,'UNAPPROVED_SCRATCH_FILESYSTEM'):
            self.checked(filesystem_type='tmpfs')

    def test_unreviewed_filesystem_fails_closed(self):
        with self.assertRaisesRegex(Blocked,'UNAPPROVED_SCRATCH_FILESYSTEM'):
            self.checked(filesystem_type='overlayfs')

    def test_cross_device_scratch_fails_closed(self):
        with self.assertRaisesRegex(Blocked,'SCRATCH_FILESYSTEM_MISMATCH'):
            self.checked(private_device=43)

    def test_unexpected_parent_fails_closed(self):
        with self.assertRaisesRegex(Blocked,'SCRATCH_PARENT_MISMATCH'):
            self.checked(private=Path('/run/l3-d2a-test'))

    def test_invalid_space_measurement_fails_closed(self):
        with self.assertRaisesRegex(Blocked,'DISK_HEADROOM'):
            self.checked(available_bytes=True)

    def test_supervisor_uses_measured_allocation_filesystem(self):
        source=(P/'build_supervisor.py').read_text()
        self.assertIn("dir=temp",source)
        self.assertIn('shutil.disk_usage(private).free',source)
        self.assertNotIn("dir='/run'",source)
        self.assertNotIn('shutil.disk_usage(temp).free',source)
        self.assertIn("'/usr/bin/fallocate','-l',str(LIMITS['disk_bytes'])",source)
        self.assertIn("'/usr/bin/mount','-o','loop,nosuid,nodev'",source)
        self.assertEqual(policy.LIMITS['disk_bytes'],12*1024**3)

if __name__=='__main__':unittest.main(verbosity=2)
