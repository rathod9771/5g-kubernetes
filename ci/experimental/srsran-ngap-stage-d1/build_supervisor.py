"""EXTERNAL hosted-runner-only supervisor. Preparation never calls main.
Requires a separately approved root invocation in a private mount namespace.
No native build command starts unless kernel limits and filesystem confinement read back correctly.
"""
import json,os,shutil,signal,stat,subprocess,sys,tempfile,time,uuid
from pathlib import Path
from build_policy import Blocked,LIMITS,digest,environment,verify_complete
from build_receipts import atomic,stamp

CONTROL_LOG=None
SCRATCH_MIN_FREE_BYTES=18*1024**3
SCRATCH_DISK_FILESYSTEMS=frozenset(('ext2/ext3','ext4','xfs','btrfs'))

def validate_scratch_storage(temp,private,filesystem_type,available_bytes,temp_device,private_device):
    """Fail closed unless the image is on the approved disk-backed runner scratch filesystem."""
    if Path(private).parent!=Path(temp):raise Blocked('SCRATCH_PARENT_MISMATCH')
    if filesystem_type not in SCRATCH_DISK_FILESYSTEMS:raise Blocked('UNAPPROVED_SCRATCH_FILESYSTEM')
    if temp_device!=private_device:raise Blocked('SCRATCH_FILESYSTEM_MISMATCH')
    if type(available_bytes) is not int or available_bytes<SCRATCH_MIN_FREE_BYTES:raise Blocked('DISK_HEADROOM')
    return {'filesystem_type':filesystem_type,'available_bytes':available_bytes,
            'required_free_bytes':SCRATCH_MIN_FREE_BYTES,'disk_limit_bytes':LIMITS['disk_bytes']}
def ctl(argv,timeout=15):
    start=time.monotonic();record={'argv':argv,'started_at':stamp()}
    try:
        result=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,check=False)
        record.update(exit_code=result.returncode if result.returncode>=0 else None,signal=-result.returncode if result.returncode<0 else None,output=result.stdout[:1048576])
        result.check_returncode();return result.stdout
    except BaseException as error:
        record.update(error=type(error).__name__);raise
    finally:
        record.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start)
        if CONTROL_LOG is not None:
            with CONTROL_LOG.open('a') as f:f.write(json.dumps(record)+'\n')
def values(text):return dict(x.split('=',1) for x in text.splitlines() if '=' in x)
def check_terminal(properties):
    if not properties.get('ExecMainStartTimestampMonotonic') or properties.get('ExecMainStartTimestampMonotonic')=='0':return False
    return properties.get('SubState') in {'exited','failed','dead'}
def observe_exit(properties,timed):
    code=int(properties.get('ExecMainCode','0'));status=int(properties.get('ExecMainStatus','-1'))
    if code==1:return {'exit_code':status,'signal':None,'timeout':timed}
    if code in (2,3):return {'exit_code':None,'signal':status,'timeout':timed}
    return {'exit_code':None,'signal':None,'timeout':timed}
def main():
    if sys.argv[1:]!=['--github-build-only'] or os.getuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('APPROVED_HOSTED_ROOT_SUPERVISOR_ONLY')
    uid=int(os.environ['SUDO_UID']);gid=int(os.environ['SUDO_GID']);repo=Path(os.environ['GITHUB_WORKSPACE']).resolve();temp=Path(os.environ['RUNNER_TEMP']).resolve();p=Path(__file__).resolve().parent
    if uid==0 or not str(temp).startswith('/home/runner/') or not p.is_relative_to(repo):raise Blocked('UNEXPECTED_RUNNER_PATH_OR_UID')
    # Root payload runs only from a reviewed package. No credential-bearing environment reaches it.
    for line in (p/'SHA256SUMS').read_text().splitlines():
        h,name=line.split('  ',1)
        if '/' in name or digest(p/name)!=h:raise Blocked('INPUT_INTEGRITY')
    environment(os.environ,'/work/home','/work/tmp') # reject unsafe inherited control settings before any configuration
    sources=temp/'l3-d2a-acquired';results=repo/'stage-d1-build-results';results.mkdir(mode=0o700)
    global CONTROL_LOG
    CONTROL_LOG=results/'supervisor-control.jsonl'
    unit='l3-d2a-'+uuid.uuid4().hex;private=Path(tempfile.mkdtemp(prefix='l3-d2a-',dir=temp));image=private/'build.ext4';mount=private/'volume';mount.mkdir()
    start=time.monotonic();final={'status':'INCOMPLETE','started_at':stamp(),'exit_code':None,'signal':None,'timeout':False,'native_tests_executed':0,'runtime_executed':False};mounted=False;launched=False;accounting={};disk_peak=0
    def interrupted(signum,frame):
        signal.signal(signum,signal.SIG_IGN);final['supervisor_signal']=signum
        raise InterruptedError('SUPERVISOR_INTERRUPTED')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    def show():
        q=values(ctl(['/usr/bin/systemctl','show',unit,'--property=Description,ActiveState,SubState,Result,ExecMainPID,ExecMainCode,ExecMainStatus,ExecMainStartTimestampMonotonic,ControlGroup']))
        if q.get('Description')!='Layer3 D2A '+unit:raise Blocked('UNEXPECTED_UNIT_IDENTITY')
        return q
    def capture(cg):
        result={}
        for name in ('memory.max','memory.swap.max','cpu.max','pids.max','memory.peak','pids.peak','cpu.stat','memory.events','pids.events','cgroup.events'):
            if not (cg/name).is_file():raise Blocked('ACCOUNTING_UNAVAILABLE:'+name)
            result[name]=(cg/name).read_text().strip()
        return result
    def stop_all():
        # systemd addresses this exact randomly named transient unit, never process names.
        ctl(['/usr/bin/systemctl','kill','--kill-who=all','--signal=TERM',unit]);deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            q=show();cg=Path('/sys/fs/cgroup')/q['ControlGroup'].lstrip('/')
            if (cg/'cgroup.events').is_file() and 'populated 0' in (cg/'cgroup.events').read_text():return
            time.sleep(.1)
        ctl(['/usr/bin/systemctl','kill','--kill-who=all','--signal=KILL',unit]);deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            q=show();cg=Path('/sys/fs/cgroup')/q['ControlGroup'].lstrip('/')
            if (cg/'cgroup.events').is_file() and 'populated 0' in (cg/'cgroup.events').read_text():return
            time.sleep(.1)
        raise Blocked('UNIT_NOT_EMPTY_AFTER_KILL')
    try:
        if os.stat('/proc/self/ns/mnt').st_ino==os.stat('/proc/1/ns/mnt').st_ino:raise Blocked('PRIVATE_MOUNT_NAMESPACE_REQUIRED')
        for tool in ('systemd-run','systemctl','journalctl','bwrap','stat','fallocate','mkfs.ext4','mount','umount','nsenter','ip','readelf','objdump'):
            if not shutil.which(tool):raise Blocked('MISSING_ENFORCEMENT_TOOL:'+tool)
        scratch_stat=os.lstat(private)
        if not stat.S_ISDIR(scratch_stat.st_mode) or scratch_stat.st_uid!=0 or stat.S_IMODE(scratch_stat.st_mode)!=0o700:
            raise Blocked('SCRATCH_DIRECTORY_PERMISSIONS')
        scratch_type=ctl(['/usr/bin/stat','-f','-c','%T',str(private)]).strip()
        scratch_free=shutil.disk_usage(private).free
        temp_device=os.stat(temp).st_dev;private_device=scratch_stat.st_dev
        with CONTROL_LOG.open('a') as f:
            f.write(json.dumps({'event':'scratch_storage_preflight','filesystem_type':scratch_type,
                'available_bytes':scratch_free,'required_free_bytes':SCRATCH_MIN_FREE_BYTES,
                'disk_limit_bytes':LIMITS['disk_bytes'],'same_device':temp_device==private_device})+'\n')
        validate_scratch_storage(temp,private,scratch_type,scratch_free,temp_device,private_device)
        memory=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
        current=Path('/sys/fs/cgroup')/next(x.split(':',2)[2].lstrip('/') for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
        if not current.resolve().is_relative_to(Path('/sys/fs/cgroup')):raise Blocked('UNEXPECTED_CGROUP_PATH')
        while current.is_relative_to(Path('/sys/fs/cgroup')):
            if (current/'memory.max').is_file():
                cap=(current/'memory.max').read_text().strip()
                if cap!='max':memory=min(memory,max(0,int(cap)-int((current/'memory.current').read_text())))
            if current==Path('/sys/fs/cgroup'):break
            current=current.parent
        if memory<LIMITS['memory.max'] or os.cpu_count()<2:raise Blocked('RESOURCE_HEADROOM')
        if not (Path('/sys/fs/cgroup')/'cgroup.controllers').is_file():raise Blocked('CGROUP_V2_REQUIRED')
        ctl(['/usr/bin/fallocate','-l',str(LIMITS['disk_bytes']),str(image)],60)
        if image.stat().st_size!=LIMITS['disk_bytes'] or image.stat().st_blocks*512<LIMITS['disk_bytes']:raise Blocked('DISK_RESERVATION_NOT_ENFORCED')
        ctl(['/usr/sbin/mkfs.ext4','-q','-F','-m','0',str(image)],60)
        ctl(['/usr/bin/mount','-o','loop,nosuid,nodev',str(image),str(mount)]);mounted=True
        os.chown(mount,uid,gid);os.chmod(mount,0o700)
        spec={'uid':uid,'gid':gid,'host_netns_inode':os.stat('/proc/self/ns/net').st_ino,'host_mount_inode':os.stat('/proc/self/ns/mnt').st_ino,'disk_image_bytes':image.stat().st_size,'acquired':'/sources','repository':'/repo'}
        atomic(mount/'spec.json',spec);os.chown(mount/'spec.json',uid,gid)
        (mount/'platform-packages.txt').write_text(ctl(['/usr/bin/dpkg-query','-W'],30));os.chown(mount/'platform-packages.txt',uid,gid)
        # Explicit bwrap environment. No inherited TEST, proxies, tokens or compiler overrides.
        env=environment({},'/work/home','/work/tmp');argv=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--unshare-ipc','--unshare-uts','--new-session','--die-with-parent']
        # Empty sandbox root: no host IPC socket directories or homes are inherited.
        for directory in ('/usr','/bin','/sbin','/lib','/lib64','/etc','/sys'):
            if not Path(directory).is_dir():raise Blocked('MISSING_READONLY_TOOLCHAIN_DIRECTORY')
            argv+=['--ro-bind',directory,directory]
        argv+=['--tmpfs','/usr/local','--remount-ro','/usr/local']
        argv+=['--ro-bind',str(repo),'/repo','--ro-bind',str(sources),'/sources','--bind',str(mount),'/work','--proc','/proc','--remount-ro','/proc','--dev','/dev']
        for directory in ('/run','/tmp','/var','/home','/root'):argv+=['--dir',directory]
        argv+=['--remount-ro','/','--clearenv']
        for k,v in env.items():argv+=['--setenv',k,v]
        argv+=['--uid',str(uid),'--gid',str(gid),'--cap-drop','ALL','--chdir','/work','/usr/bin/python3','-B',str(Path('/repo')/p.relative_to(repo)/'build_worker.py'),'--isolated-build-only']
        if ctl(['/usr/bin/systemctl','show',unit,'--property=LoadState','--value']).strip()!='not-found':raise Blocked('UNIT_COLLISION')
        command=['/usr/bin/systemd-run','--quiet','--unit='+unit,'--description=Layer3 D2A '+unit,'--service-type=exec','--property=RemainAfterExit=yes','--property=MemoryMax='+str(LIMITS['memory.max']),'--property=MemorySwapMax=0','--property=CPUQuota=200%','--property=CPUQuotaPeriodSec=100ms','--property=TasksMax='+str(LIMITS['pids.max']),'--property=MemoryAccounting=yes','--property=CPUAccounting=yes','--property=TasksAccounting=yes','--property=OOMPolicy=kill','--property=NoNewPrivileges=yes','--property=KillMode=control-group','--property=TimeoutStopSec=10s','--property=RuntimeMaxSec=4500s','--','/usr/bin/nsenter','--mount=/proc/'+str(os.getpid())+'/ns/mnt','--',*argv]
        atomic(results/'supervisor-start.json',{'unit':unit,'argv':command,'limits':LIMITS,'runner_image':{k:os.environ.get(k) for k in ('ImageOS','ImageVersion')},'started_at':stamp()})
        launched=True;ctl(command,20);cg=None;last={}
        while True:
            q=show()
            if q.get('ControlGroup'):
                cg=Path('/sys/fs/cgroup')/q['ControlGroup'].lstrip('/')
                if cg.exists():accounting=capture(cg)
            fs=os.statvfs(mount);disk_peak=max(disk_peak,(fs.f_blocks-fs.f_bfree)*fs.f_frsize)
            if check_terminal(q):last=q;break
            if time.monotonic()-start>=LIMITS['wall_seconds']:
                final['timeout']=True;stop_all();last=show();break
            time.sleep(.1)
        journal=ctl(['/usr/bin/journalctl','--unit='+unit,'--no-pager','--output=short-iso-precise','--lines=200'])
        if len(journal.encode())>256*1024:raise Blocked('UNIT_DIAGNOSTIC_BUDGET')
        (results/'unit-startup.log').write_text(journal)
        final.update(observe_exit(last,final['timeout']));final['systemd_result']=last.get('Result');final['observed_properties']=last
        if cg is None or not cg.exists():raise Blocked('FINAL_CGROUP_ACCOUNTING_UNAVAILABLE')
        accounting=capture(cg)
        if cg and 'populated 0' not in accounting.get('cgroup.events',''):
            stop_all();accounting=capture(cg);raise Blocked('UNEXPECTED_RESIDUAL_PROCESSES')
        # Copy evidence while the bounded filesystem is still mounted. Never copy binaries.
        ev=mount/'evidence'
        if ev.exists():
            for f in ev.iterdir():
                if f.is_symlink() or not f.is_file():raise Blocked('UNEXPECTED_EVIDENCE_OBJECT')
                if f.stat().st_size>64*1024**2:raise Blocked('EVIDENCE_FILE_BUDGET')
            if sum(f.stat().st_size for f in ev.iterdir())>256*1024**2:raise Blocked('EVIDENCE_TOTAL_BUDGET')
            shutil.copytree(ev,results/'worker')
        def counters(name):return dict((k,int(v)) for k,v in (x.split() for x in accounting[name].splitlines()))
        usage={'memory_peak_bytes':int(accounting['memory.peak']),'cpu_usage_usec':counters('cpu.stat')['usage_usec'],'pids_peak':int(accounting['pids.peak']),'disk_used_peak_bytes':disk_peak,'disk_used_final_bytes':(os.statvfs(mount).f_blocks-os.statvfs(mount).f_bfree)*os.statvfs(mount).f_frsize,'oom_kill':counters('memory.events')['oom_kill'],'pids_limit_events':counters('pids.events')['max']}
        atomic(results/'resource-usage.json',{'limits':LIMITS,'readback':accounting,'usage':usage,'disk_peak_method':'100ms statvfs samples; capacity is hard, peak is a sampled lower bound'})
        worker=results/'worker'
        if final['exit_code']!=0 or final['signal'] is not None or final['timeout']:
            classification='INCOMPLETE' if final['timeout'] or final['signal'] is not None or final['exit_code'] is None else 'FAIL'
            if not (worker/'enforcement.json').is_file() and classification=='FAIL':classification='BLOCKED'
            if (worker/'worker-status.json').is_file():classification=json.loads((worker/'worker-status.json').read_text())['status']
            final['status']=classification
            raise RuntimeError('BUILD_OPERATION_NOT_COMPLETED')
        if last.get('Result')!='success':
            final['status']='FAIL';raise RuntimeError('SYSTEMD_UNIT_NOT_SUCCESS')
        final.update(supervisor_completed=True,ended_at=stamp(),elapsed_seconds=time.monotonic()-start)
        receipts={f.name.removesuffix('.receipt.json'):json.loads(f.read_text()) for f in worker.glob('*.receipt.json')}
        verify_complete(receipts,json.loads((worker/'toolchain.json').read_text()),json.loads((worker/'binary-inventory.json').read_text()),final,usage)
        for name,item in json.loads((worker/'binary-inventory.json').read_text()).items():
            if Path(name).is_absolute() or '..' in Path(name).parts:raise Blocked('UNSAFE_BINARY_REFERENCE')
            if digest(mount/name)!=item['sha256']:raise Blocked('BINARY_CORRUPTION')
        # Detect corruption of every referenced log; reject missing output hashes as well.
        for r in receipts.values():
            for entry in r['commands']:
                f=worker/Path(entry['log']).name
                if not f.is_file() or digest(f)!=entry['log_sha256']:raise Blocked('PARTIAL_LOG_CORRUPTION')
            if digest(worker/Path(r['log']).name)!=r['log_sha256']:raise Blocked('PHASE_LOG_CORRUPTION')
            for name,h in r['outputs'].items():
                if name.startswith('/work/'):source=mount/Path(name).relative_to('/work')
                elif not Path(name).is_absolute() and '..' not in Path(name).parts:source=worker/name
                else:raise Blocked('UNEXPECTED_ARTIFACT_REFERENCE')
                if not source.is_file() or digest(source)!=h:raise Blocked('ARTIFACT_CORRUPTION')
        final['status']='PASS_BUILD_ONLY_ZERO_NATIVE_TESTS'
    except Blocked as e:final.update(status='BLOCKED',reason=str(e))
    except (OSError,subprocess.CalledProcessError) as e:
        final.update(status='BLOCKED' if not launched else 'INCOMPLETE',reason='ENFORCEMENT_OR_CONTROL_OPERATION_FAILED:'+type(e).__name__)
    except BaseException as e:
        if final['status'] not in {'FAIL','BLOCKED'}:final['status']='INCOMPLETE'
        final['reason']=type(e).__name__
    finally:
        if launched:
            try:
                q=show()
                if q.get('ControlGroup') and (Path('/sys/fs/cgroup')/q['ControlGroup'].lstrip('/')/'cgroup.events').exists():
                    if 'populated 0' not in (Path('/sys/fs/cgroup')/q['ControlGroup'].lstrip('/')/'cgroup.events').read_text():stop_all()
                q=show();final['cleanup_observed_properties']=q
                if final['exit_code'] is None and final['signal'] is None:final.update(observe_exit(q,final['timeout']))
                ctl(['/usr/bin/systemctl','stop',unit])
            except BaseException:final.update(status='INCOMPLETE',cleanup='UNIT_TERMINATION_UNVERIFIED')
        if mounted:
            try:
                ev=mount/'evidence'
                if ev.is_dir() and not (results/'worker').exists():
                    partial=results/'worker';partial.mkdir(mode=0o700);total=0
                    for f in ev.iterdir():
                        if not f.is_symlink() and f.is_file() and f.stat().st_size<=64*1024**2:
                            total+=f.stat().st_size
                            if total>256*1024**2:raise Blocked('PARTIAL_EVIDENCE_BUDGET')
                            shutil.copy2(f,partial/f.name)
            except BaseException:final.update(status='INCOMPLETE',partial_diagnostics='COPY_INCOMPLETE')
            try:ctl(['/usr/bin/umount',str(mount)])
            except BaseException:final.update(status='INCOMPLETE',cleanup='UNMOUNT_FAILED')
        if accounting and not (results/'resource-usage.json').exists():atomic(results/'partial-accounting.json',{'readback':accounting,'disk_used_peak_sampled_bytes':disk_peak,'classification':'INCOMPLETE'})
        final.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start)
        final['evidence_sha256']={str(f.relative_to(results)):digest(f) for f in results.rglob('*') if f.is_file() and not f.is_symlink() and f.name!='supervisor-final.json'}
        atomic(results/'supervisor-final.json',final)
        for f in results.rglob('*'):
            if f.is_dir():os.chown(f,uid,gid);os.chmod(f,0o700)
            elif f.is_file():os.chown(f,uid,gid);os.chmod(f,0o600)
        os.chown(results,uid,gid)
    if final['status']!='PASS_BUILD_ONLY_ZERO_NATIVE_TESTS':raise SystemExit(1)
if __name__=='__main__':main()
