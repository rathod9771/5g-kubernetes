"""DISARMED without a separate, one-use runtime approval. Never invoked by build CI.
Intended only after sudo -n unshare --net on a disposable GitHub-hosted runner.
"""
import os,sys,json,hashlib,time,uuid,stat,signal,selectors,subprocess,pwd,resource,ctypes
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
class Blocked(RuntimeError):pass
sys.dont_write_bytecode=True
ROOT=Path(__file__).parent.resolve()
SRS='11c9bbabb69873752500d676f55e0034f6caa5c5';OGS='7dfd9a39649700c24c22f1978ed7a35541a72cca'

def private_file(p,uid,maximum=65536):
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=uid or st.st_mode&0o077 or st.st_size>maximum:raise Blocked('UNSAFE_PRIVATE_FILE')
        data=os.read(fd,maximum+1)
        if len(data)>maximum:raise Blocked('PRIVATE_FILE_SIZE')
        return data
    finally:os.close(fd)

def approval(data,now,package_hash):
    if set(data)!={'scope','nonce','approved_by','created','expires','package_sha256','srsran_revision','open5gs_revision','test_id','seconds','binaries','source_manifest_sha256'}:raise Blocked('APPROVAL_FIELDS')
    if data['scope']!='D1_ISOLATED_NGAP_RUNTIME' or not data['approved_by']:raise Blocked('RUNTIME_NOT_APPROVED')
    uuid.UUID(data['nonce'])
    if not data['created']<=now<data['expires'] or data['expires']-now<180 or not 0<data['expires']-data['created']<=900:raise Blocked('APPROVAL_EXPIRED')
    if data['package_sha256']!=package_hash or data['srsran_revision']!=SRS or data['open5gs_revision']!=OGS:raise Blocked('SOURCE_OR_PACKAGE_MISMATCH')
    if data['test_id'] not in ('D0-T1','D0-T2','D0-T3','D0-T7') or not 1<=data['seconds']<=120:raise Blocked('UNAPPROVED_RUNTIME_CASE')
    if set(data['binaries'])!={'cu','amf','nrf'}:raise Blocked('BINARY_SCOPE')

def verify_inputs(a,runner,uid):
    for d in [ROOT,*ROOT.parents]:
        if d==runner.parent:break
        st=d.lstat()
        if d.is_symlink() or st.st_uid not in (0,uid) or st.st_mode&0o022:raise Blocked('UNSAFE_PACKAGE_DIRECTORY')
    entries=(ROOT/'SHA256SUMS').read_text().splitlines()
    names={line.split('  ',1)[1] for line in entries}
    if len(names)!=len(entries):raise Blocked('DUPLICATE_PACKAGE_ENTRY')
    if {p.name for p in ROOT.iterdir()}!=names|{'SHA256SUMS'}:raise Blocked('UNEXPECTED_PACKAGE_ENTRY')
    for line in entries:
        h,name=line.split('  ',1)
        if Path(name).is_absolute() or len(Path(name).parts)!=1:raise Blocked('PACKAGE_PATH')
        p=ROOT/name
        st=p.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in (uid,0) or st.st_mode&0o022:raise Blocked('UNSAFE_PACKAGE_FILE')
        if Path(name).is_absolute() or '..' in Path(name).parts or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise Blocked('PACKAGE_DRIFT')
    for item in a['binaries'].values():
        if set(item)!={'path','sha256'}:raise Blocked('BINARY_FIELDS')
        p=Path(item['path']);st=p.lstat()
        if not p.resolve().is_relative_to(runner) or p.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid!=uid or st.st_mode&0o6022 or not st.st_mode&0o111 or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise Blocked('UNSAFE_OR_CHANGED_BINARY')
    m=ROOT/'source-manifest.json'
    if hashlib.sha256(m.read_bytes()).hexdigest()!=a['source_manifest_sha256']:raise Blocked('SOURCE_MANIFEST_DRIFT')
    pinned=json.loads(m.read_text())
    if pinned['srsran_revision']!=SRS or pinned['open5gs_revision']!=OGS:raise Blocked('SOURCE_PIN_DRIFT')

def run(a,host,uid,gid):
    overall_deadline=time.monotonic()+170
    if os.getuid()!=0 or uid==0 or gid==0:raise Blocked('SETUP_CONTEXT')
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('NOT_DISPOSABLE_GITHUB_RUNNER')
    runner=Path(os.environ['RUNNER_TEMP']).resolve()
    if str(runner).startswith('/home/rclab') or not ROOT.is_relative_to(runner):raise Blocked('PACKAGE_NOT_IN_RUNNER_SCRATCH')
    if os.stat('/proc/self/ns/net').st_ino==host:raise Blocked('HOST_NETNS')
    # Verify only lo exists BEFORE this new namespace's loopback/address setup.
    links=json.loads(subprocess.run(['/usr/sbin/ip','-j','link','show'],check=True,capture_output=True,text=True,timeout=3).stdout)
    if len(links)!=1 or links[0]['ifname']!='lo':raise Blocked('NETWORK_NOT_ISOLATED')
    verify_inputs(a,runner,uid)
    # Import experiment helpers only after source/package identity and permissions pass.
    from preflight import collect,Blocked as AdmissionBlocked
    from protocol_evidence import capture_command,project_log,decode
    from timing import clock_uncertainty
    out=runner/('l3-d1-'+a['nonce']);out.mkdir(mode=0o700);os.chown(out,uid,gid)
    # Exclusive consumption in new output dir; an existing dir is a hard stop, never reuse/delete.
    with (out/'approval-consumed.json').open('x') as f:json.dump({'nonce':a['nonce'],'test_id':a['test_id'],'scope':a['scope'],'approved_by':a['approved_by'],'created':a['created'],'expires':a['expires'],'consumed_at':time.time()},f)
    os.chmod(out/'approval-consumed.json',0o600);os.chown(out/'approval-consumed.json',uid,gid)
    subprocess.run(['/usr/sbin/ip','link','set','dev','lo','up'],check=True,timeout=3)
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):raise Blocked('PIDFD_UNSUPPORTED')
    probe=os.pidfd_open(os.getpid());os.close(probe)
    for address in ('127.0.0.10/32','127.0.0.20/32','127.0.0.30/32'):
        subprocess.run(['/usr/sbin/ip','address','add',address,'dev','lo'],check=True,timeout=3)
    # Actual capture capability proof, no packets injected or external route created.
    import socket
    with socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3)) as raw:raw.bind(('lo',0))
    try:p=collect(host,True,True,True)
    except AdmissionBlocked as e:raise Blocked(str(e)) from None
    (out/'preflight.json').write_text(json.dumps(p,indent=2));os.chmod(out/'preflight.json',0o600);os.chown(out/'preflight.json',uid,gid)
    def anchor():
        before=time.time_ns();mono=time.monotonic_ns();after=time.time_ns();return {'wall_before_ns':before,'monotonic_ns':mono,'wall_after_ns':after}
    anchors=[anchor()]
    env={k:os.environ[k] for k in ('RUNNER_TEMP','GITHUB_ACTIONS','RUNNER_ENVIRONMENT')};env.update(PATH='/usr/bin:/bin',HOME=str(out),LANG='C',TZ='UTC')
    children=[];sel=selectors.DefaultSelector();logs=[];counts={};ready=set();deadline=overall_deadline-40
    def demote():
        os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_CPU,(120,120));resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024**2,8*1024**2))
        os.setgroups([]);os.setgid(gid);os.setuid(uid)
        if ctypes.CDLL(None).prctl(38,1,0,0,0):os._exit(77) # PR_SET_NO_NEW_PRIVS
    def spawn(source,argv,capture=False):
        c=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,preexec_fn=(None if capture else demote))
        fd=os.pidfd_open(c.pid)
        if os.stat('/proc/'+str(c.pid)+'/ns/net').st_ino!=os.stat('/proc/self/ns/net').st_ino:signal.pidfd_send_signal(fd,signal.SIGKILL);c.wait(timeout=2);os.close(fd);raise Blocked('CHILD_NAMESPACE_MISMATCH')
        children.append((source,c,fd));counts[source]=0;os.set_blocking(c.stdout.fileno(),False);sel.register(c.stdout,selectors.EVENT_READ,(source,bytearray()))
        return c
    def pump():
        if time.monotonic()>deadline:raise Blocked('RUNTIME_WALL_TIMEOUT')
        for key,_ in sel.select(0.05):
            source,buf=key.data;chunk=os.read(key.fd,4096)
            if not chunk:sel.unregister(key.fileobj);continue
            counts[source]+=len(chunk)
            if counts[source]>1024**2:raise Blocked('LOG_BUDGET')
            buf.extend(chunk)
            if len(buf)>8192 and b'\n' not in buf:raise Blocked('LINE_BUDGET')
            while b'\n' in buf:
                line,_,rest=buf.partition(b'\n');buf[:]=rest
                if len(line)>4096:raise Blocked('LINE_BUDGET')
                text=line.decode(errors='replace')
                if source=='capture':
                    if 'listening on lo' in text:ready.add('CAPTURE_READY')
                else:
                    marks=project_log(source,text,time.time_ns(),time.monotonic_ns())
                    for mark in marks:mark['pid']=next(c.pid for name,c,_ in children if name==source)
                    logs.extend(marks);ready.update(x['marker'] for x in marks)
                if len(logs)>5000:raise Blocked('MARKER_BUDGET')
    def wait_for(marker,seconds=10):
        end=time.monotonic()+seconds
        while marker not in ready:
            pump()
            if time.monotonic()>end or any(c.poll() is not None for _,c,_ in children):raise Blocked('READINESS_INCOMPLETE')
    error=None;cu=None
    try:
        cap=spawn('capture',capture_command(out/'n2.pcap',pwd.getpwuid(uid).pw_name),True);wait_for('CAPTURE_READY')
        spawn('nrf',[a['binaries']['nrf']['path'],'-c',str(ROOT/'nrf.yaml')]);wait_for('NRF_INITIALIZED')
        if a['test_id']!='D0-T1':
            config='amf-reject.yaml' if a['test_id']=='D0-T3' else 'amf.yaml'
            spawn('amf',[a['binaries']['amf']['path'],'-c',str(ROOT/config)]);wait_for('AMF_INITIALIZED')
        if deadline-time.monotonic()<a['seconds']+5:raise Blocked('INSUFFICIENT_REMAINING_TRIAL_BUDGET')
        cu=spawn('cu',[a['binaries']['cu']['path'],'--execute-isolated','--host-netns',str(host),'--seconds',str(a['seconds']),'--output',str(out/'snapshots.jsonl')])
        while cu.poll() is None:
            pump()
            if cap.poll() is not None:raise Blocked('CAPTURE_ENDED_EARLY')
            if any(c.poll() is not None for name,c,_ in children if name in ('amf','nrf')):raise Blocked('UNEXPECTED_NF_EXIT')
        if cu.returncode!=0:raise Blocked('DRIVER_FAILED')
        # Drain available final marker lines; no extra protocol operations.
        for _ in range(5):pump()
    except Exception as e:
        import re
        error={'type':type(e).__name__,'reason':str(e) if isinstance(e,Blocked) and re.fullmatch('[A-Z0-9_]+',str(e)) else 'REDACTED'}
    finally:
        # Exact child pidfds only; no process-name killing or host service control.
        if any(name!='cu' and c.poll() is not None for name,c,_ in children):error=error or {'type':'Blocked','reason':'UNEXPECTED_NF_OR_CAPTURE_EXIT'}
        for _,c,fd in reversed(children):
            if c.poll() is None:
                signal.pidfd_send_signal(fd,signal.SIGTERM)
                try:c.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    signal.pidfd_send_signal(fd,signal.SIGKILL);c.wait(timeout=2);error=error or {'type':'TimeoutExpired','reason':'CLEANUP_FORCED_TERMINATION'}
            if c.returncode!=0:error=error or {'type':'Blocked','reason':'UNEXPECTED_CLEANUP_EXIT'}
            os.close(fd)
        anchors.append(anchor())
        samples=[]
        sf=out/'snapshots.jsonl'
        if sf.exists():
            blob=private_file(sf,uid,8*1024**2)
            if any(len(line)>8192 for line in blob.splitlines()) or len(blob.splitlines())>5000:raise Blocked('SNAPSHOT_EVIDENCE_BUDGET')
            samples=[json.loads(line) for line in blob.splitlines()]
        (out/'log-markers.json').write_text(json.dumps(logs));(out/'execution.json').write_text(json.dumps({'status':'INCOMPLETE' if error else 'DRIVER_COMPLETED_NOT_PROTOCOL_PASS','error_type':error,'children':[{'source':n,'pid':c.pid,'exit':c.returncode} for n,c,_ in children],'log_bytes':counts,'host_netns':host,'runtime_netns':os.stat('/proc/self/ns/net').st_ino,'test_id':a['test_id'],'experiment_id':a['nonce'],'synthetic':False,'amf_started':a['test_id']!='D0-T1','timing':{'anchors':anchors,'uncertainty_ns':clock_uncertainty(samples,anchors),'method':'empirical same-kernel clock consistency; not absolute clock-accuracy proof'},'provenance':{'srsran_revision':SRS,'open5gs_revision':OGS,'source_manifest_sha256':a['source_manifest_sha256'],'package_sha256':a['package_sha256'],'binaries':a['binaries'],'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}}))
        for f in out.iterdir():
            if f.is_symlink() or not stat.S_ISREG(f.lstat().st_mode):raise Blocked('UNEXPECTED_EVIDENCE_ENTRY')
            os.chmod(f,0o600);os.chown(f,uid,gid,follow_symlinks=False)
    if error:raise Blocked('RUNTIME_INCOMPLETE')
    # Decoder after cleanup; projected evidence only is eligible for publication.
    verify_inputs(a,runner,uid)
    projected=decode(out/'n2.pcap',uid,gid,max(0,overall_deadline-time.monotonic()));(out/'protocol-events.json').write_text(json.dumps(projected));os.chmod(out/'protocol-events.json',0o600);os.chown(out/'protocol-events.json',uid,gid)
    hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in out.iterdir() if f.is_file() and not f.is_symlink()}
    (out/'evidence-sha256.json').write_text(json.dumps(hashes,indent=2));os.chmod(out/'evidence-sha256.json',0o600);os.chown(out/'evidence-sha256.json',uid,gid)
    return out

def main():
    if len(sys.argv)!=7 or sys.argv[1]!='--execute-isolated':raise Blocked('EXPLICIT_RUNTIME_APPROVAL_REQUIRED')
    uid,gid,host=map(int,sys.argv[3:6]);p=Path(sys.argv[2]);data=json.loads(private_file(p,uid));package_hash=hashlib.sha256((ROOT/'SHA256SUMS').read_bytes()).hexdigest()
    approval(data,time.time(),package_hash)
    # Last argument is a fixed scope marker, never a shell command.
    if sys.argv[6]!='D1-NGAP-ONLY':raise Blocked('SCOPE')
    run(data,host,uid,gid)
if __name__=='__main__':
    try:main()
    except Exception as e:
        import re
        reason=str(e) if isinstance(e,Blocked) and re.fullmatch('[A-Z0-9_]+',str(e)) else 'UNEXPECTED_ERROR_REDACTED'
        raise SystemExit('BLOCKED: '+reason+'; no automatic retry')
