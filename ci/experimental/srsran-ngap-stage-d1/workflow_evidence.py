"""Offline-testable auxiliary admission and bounded preservation. No import-time execution."""
import datetime, hashlib, json, os, platform, resource, shutil, sys, time
from pathlib import Path
from build_policy import Blocked, digest, PHASES, successful
from build_receipts import atomic, command, stamp
from offline_python import PIN, inventory
from toolchain_lock import ACQUISITION_DIAGNOSTICS, POLICY as TOOLCHAIN_POLICY, POLICY_SHA256 as TOOLCHAIN_POLICY_SHA256
P = Path(__file__).resolve().parent
AUX = ('wheel_acquisition', 'python_setup')
LIMIT = 64 * 1024**2
INPUT_FILES=('dependency-command-policy.json','phase-receipt.schema.json','SHA256SUMS','source-manifest.json','dependency-lock.PROPOSED.json','installer-pin.PROPOSED.json','python-wheels.PROPOSED.json','python-requirements.PROPOSED.txt','auxiliary-receipt.schema.json')
POLICY = {'retries': 0, 'redirects': 0, 'request_timeout_seconds': 15,
          'wheel_bytes': 32*1024**2, 'metadata_bytes': 1024**2,
          'scope': 'AUXILIARY_NOT_NATIVE_CGROUP', 'step_deadline_seconds': 300,
          'supervisor_deadline_seconds': 285, 'command_deadline_seconds': 240}

def checked(root, name):
    p = Path(root)/name
    if Path(name).is_absolute() or '..' in Path(name).parts: raise Blocked('UNSAFE_EVIDENCE_PATH')
    for part in (p, *p.parents):
        if part.is_symlink(): raise Blocked('SYMLINK_EVIDENCE')
        if part == Path(root): break
    if not p.is_file() or p.stat().st_size > LIMIT: raise Blocked('MISSING_OR_OVERSIZED_EVIDENCE:'+name)
    return p

def timing(r):
    try:
        a,b = [datetime.datetime.fromisoformat(r[k]) for k in ('started_at','ended_at')]
        e=r['elapsed_seconds']
        if a.utcoffset()!=datetime.timedelta(0) or b.utcoffset()!=datetime.timedelta(0) or type(e) not in (int,float) or not __import__('math').isfinite(e) or e<0 or (b-a).total_seconds()<0 or abs((b-a).total_seconds()-e)>1: raise ValueError()
    except (KeyError,TypeError,ValueError): raise Blocked('INVALID_AUX_TIMING')
    return a,b

def expected_inner(phase):
    return ('l3-wheel-acquisition/acquisition.json', 'PASS_VERIFIED_PUBLIC_WHEEL_ACQUISITION') if phase=='wheel_acquisition' else ('l3-python-setup/setup.json','PASS_OFFLINE_PYTHON_SETUP')

def validate_aux(root):
    root=Path(root); windows=[]
    for phase in AUX:
        r=json.loads(checked(root,phase+'.receipt.json').read_text())
        required={'schema','phase','classification','started_at','ended_at','elapsed_seconds','command','interpreter','installer','policy','resource_evidence','artifacts','source_sha256'}
        if set(r)!=required or r['schema']!=1 or r['phase']!=phase: raise Blocked('AUX_SCHEMA')
        if r['classification']!='PASS': raise Blocked('AUX_NOT_PASS:'+phase)
        windows.append(timing(r)); c=r['command']; timing(c); successful(c)
        if c.get('classification')!='PASS' or type(c.get('exit_code')) is not int or c['exit_code']!=0 or c.get('signal') is not None or c.get('timeout') is not False or c.get('forced') is not False or c.get('log_budget_exceeded') is not False: raise Blocked('AUX_COMMAND_NOT_PASS')
        script='wheelhouse_acquire.py' if phase=='wheel_acquisition' else 'offline_python.py'
        argv=c.get('argv',[])
        if len(argv) not in (4,6):raise Blocked('AUX_COMMAND_IDENTITY')
        if argv!=aux_argv(phase,P,Path(argv[4]).parent if phase=='python_setup' and len(argv)==6 else None) or Path(argv[2]).name!=script or r['source_sha256']!=digest(P/script): raise Blocked('AUX_COMMAND_IDENTITY')
        if not windows[-1][0]<=timing(c)[0]<=timing(c)[1]<=windows[-1][1]: raise Blocked('AUX_COMMAND_ORDER')
        if r['installer']!=PIN or r['policy']!=POLICY: raise Blocked('INSTALLER_OR_POLICY_DRIFT')
        if r['interpreter'].get('version',[])[:2]!=[3,10] or r['interpreter'].get('implementation')!='CPython': raise Blocked('AUX_INTERPRETER')
        usage=r['resource_evidence']
        accounting=usage.get('self_and_reaped_children',{})
        for field in ('child_user_seconds','child_system_seconds','child_maxrss_kib','self_user_seconds','self_system_seconds'):
            value=accounting.get(field)
            if type(value) not in (int,float) or not __import__('math').isfinite(value) or value<0:raise Blocked('MISSING_AUX_RESOURCE_ACCOUNTING')
        if usage.get('native_cgroup_claimed') is not False or usage.get('deadline_enforced') is not True or usage.get('scope')!=POLICY['scope'] or not isinstance(usage.get('self_and_reaped_children'),dict): raise Blocked('AUX_RESOURCE_EVIDENCE')
        files=r['artifacts']
        if not files or files.get(phase+'.log')!=c.get('log_sha256'): raise Blocked('AUX_LOG_BINDING')
        for name,h in files.items():
            if digest(checked(root,name))!=h: raise Blocked('AUX_ARTIFACT_HASH:'+name)
        inner,status=expected_inner(phase)
        if inner not in files: raise Blocked('MISSING_INNER_RECEIPT')
        data=json.loads(checked(root,inner).read_text());inner_window=timing(data)
        if not windows[-1][0]<=inner_window[0]<=inner_window[1]<=windows[-1][1]:raise Blocked('INNER_PHASE_ORDER')
        if data.get('status')!=status: raise Blocked('INNER_NOT_PASS')
        hashes={x['filename']:x['sha256'] for x in inventory()}
        if data.get('hashes' if phase=='wheel_acquisition' else 'wheel_hashes')!=hashes: raise Blocked('WHEEL_VERIFICATION_MISSING_OR_INVALID')
        if phase=='wheel_acquisition':
            if len(data.get('requests',[]))!=16 or data.get('retries')!=0 or data.get('redirects')!=0: raise Blocked('ACQUISITION_POLICY')
        else:
            from re import sub
            expected={sub('[-_.]+','-',x['name'].lower()):x['version'] for x in inventory()[:-1]}
            if data.get('installed')!=expected or data.get('installer_installed') is not False: raise Blocked('RESOLVED_VERSIONS')
            commands=data.get('commands',[])
            if len(commands)!=5: raise Blocked('SETUP_COMMAND_INVENTORY')
            previous=inner_window[0]
            for i,entry in enumerate(commands):
                begin,end=timing(entry);successful(entry)
                if begin<previous or end>inner_window[1]:raise Blocked('INNER_COMMAND_ORDER')
                previous=end
                if entry.get('exit_code')!=0 or entry.get('timeout') is not False or entry.get('signal') is not None or entry.get('classification')!='PASS': raise Blocked('SETUP_COMMAND_FAILED')
                log='l3-python-setup/'+str(i)+'.log'
                if files.get(log)!=entry.get('log_sha256'): raise Blocked('MISSING_SETUP_LOG')
    if windows[0][1]>windows[1][0]: raise Blocked('AUX_PHASE_ORDER')
    return windows

def upload_admission(outcome, artifact_id, artifact_digest, local_pass):
    if local_pass is not True or outcome!='success' or not str(artifact_id).isdigit() or int(artifact_id)<=0 or not __import__('re').fullmatch('[a-f0-9]{64}',artifact_digest or ''): raise Blocked('UPLOAD_INCOMPLETE_NOT_EVIDENCE_QUALIFIED')
    return {'classification':'PASS_BUILD_ONLY_EVIDENCE_UPLOADED','native_tests_executed':0}

def aux_argv(phase,package,temp=None):
    argv=['/usr/bin/python3','-B',str(Path(package)/('wheelhouse_acquire.py' if phase=='wheel_acquisition' else 'offline_python.py'))]
    return argv+(['--github-acquire-only'] if phase=='wheel_acquisition' else ['--github-offline-setup',str(Path(temp)/'l3-reviewed-wheelhouse'),str(Path(temp)/'d1-python')])

def run_aux(phase):
    # Execution is an explicit future hosted-only operation, never called by preparation tests.
    if phase not in AUX or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted': raise Blocked('HOSTED_AUX_ONLY')
    temp=Path(os.environ['RUNNER_TEMP']).resolve(); out=temp/'l3-aux-evidence';out.mkdir(mode=0o700,exist_ok=True)
    if (out/(phase+'.receipt.json')).exists(): raise Blocked('AUX_COLLISION')
    script='wheelhouse_acquire.py' if phase=='wheel_acquisition' else 'offline_python.py'
    argv=aux_argv(phase,P,temp)
    env={k:os.environ[k] for k in ('RUNNER_TEMP','GITHUB_WORKSPACE','GITHUB_ACTIONS','RUNNER_ENVIRONMENT')};env.update(PATH='/usr/bin:/bin',LANG='C.UTF-8',LC_ALL='C.UTF-8',HOME=str(temp))
    start=time.monotonic();r={'schema':1,'phase':phase,'classification':'INCOMPLETE','started_at':stamp(),'interpreter':{'version':list(sys.version_info[:3]),'implementation':platform.python_implementation()},'installer':PIN,'policy':POLICY,'source_sha256':digest(P/script),'artifacts':{},'command':{},'resource_evidence':{}}
    try:
        r['command']=command(argv,P,env,out/(phase+'.log'),285)
        r['classification']=r['command']['classification']
    finally:
        usage=resource.getrusage(resource.RUSAGE_CHILDREN)
        r.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start)
        r['resource_evidence']={'scope':POLICY['scope'],'native_cgroup_claimed':False,'deadline_enforced':True,'self_and_reaped_children':{'child_user_seconds':usage.ru_utime,'child_system_seconds':usage.ru_stime,'child_maxrss_kib':usage.ru_maxrss,'self_user_seconds':resource.getrusage(resource.RUSAGE_SELF).ru_utime,'self_system_seconds':resource.getrusage(resource.RUSAGE_SELF).ru_stime}}
        candidates=[out/(phase+'.log')]
        folder=temp/('l3-wheel-acquisition' if phase=='wheel_acquisition' else 'l3-python-setup')
        names=['acquisition.json'] if phase=='wheel_acquisition' else ['setup.json',*[str(i)+'.log' for i in range(5)]]
        for name in names:
            source=folder/name
            if source.is_file() and not source.is_symlink():
                dest=out/folder.name;dest.mkdir(mode=0o700,exist_ok=True);shutil.copyfile(checked(folder,name),dest/name);(dest/name).chmod(0o600);candidates.append(dest/name)
        r['artifacts']={str(x.relative_to(out)):digest(x) for x in candidates if x.is_file()}
        atomic(out/(phase+'.receipt.json'),r)
    if r['classification']!='PASS': raise Blocked('AUX_EXECUTION_NOT_PASS')

def collect(temp,root):
    """Only explicit auxiliary paths; native supervisor already owns its bounded export."""
    root=Path(root);root.mkdir(mode=0o700,exist_ok=True);src=Path(temp)/'l3-aux-evidence';dest=root/'auxiliary';dest.mkdir(mode=0o700,exist_ok=True)
    names=[p+'.receipt.json' for p in AUX]+[p+'.log' for p in AUX]+['l3-wheel-acquisition/acquisition.json','l3-python-setup/setup.json']+['l3-python-setup/'+str(i)+'.log' for i in range(5)]
    errors=[];total=0
    for name in names:
        try:
            f=checked(src,name);total+=f.stat().st_size
            if total>256*1024**2: raise Blocked('AUX_AGGREGATE_BUDGET')
            target=dest/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if target.exists(): raise Blocked('PRESERVATION_COLLISION')
            shutil.copyfile(f,target);target.chmod(0o600)
        except Blocked as e: errors.append(str(e))
    # Source acquisition precedes the supervisor; preserve its partial diagnostics
    # even when failure prevents the supervisor from ever being started.
    source_root=Path(temp)/'l3-d2a-acquired';worker=root/'worker';worker.mkdir(mode=0o700,exist_ok=True)
    source_names=list(ACQUISITION_DIAGNOSTICS)+['dependency_acquisition.receipt.json','dependency_acquisition.log','source-archives.json','toolchain-lock.json']+['dependency_acquisition.'+str(i)+'.log' for i in range(18)]
    for name in source_names:
        try:
            f=checked(source_root,name);total+=f.stat().st_size
            if total>256*1024**2:raise Blocked('COLLECTION_TOTAL_BUDGET')
            target=worker/('acquisition-'+name if name in ACQUISITION_DIAGNOSTICS else name)
            if target.exists():
                if digest(checked(worker,target.name))!=digest(f):raise Blocked('SOURCE_DIAGNOSTIC_COLLISION')
            else:shutil.copyfile(f,target);target.chmod(0o600)
        except Blocked as e:errors.append(str(e))
    inputs=root/'inputs';inputs.mkdir(mode=0o700,exist_ok=True)
    for name in INPUT_FILES:
        try:
            f=checked(P,name);total+=f.stat().st_size
            if total>256*1024**2:raise Blocked('COLLECTION_TOTAL_BUDGET')
            if (inputs/name).exists():raise Blocked('INPUT_EVIDENCE_COLLISION')
            shutil.copyfile(f,inputs/name);(inputs/name).chmod(0o600)
        except Blocked as e:errors.append(str(e))
    atomic(root/'preservation.json',{'classification':'INCOMPLETE' if errors else 'PASS','errors':errors,'auxiliary_allowlist':names,'source_allowlist':source_names,'input_allowlist':list(INPUT_FILES),'bytes':total})
    return not errors

ROOT_FILES={'supervisor-final.json','supervisor-start.json','supervisor-control.jsonl','unit-startup.log','resource-usage.json','partial-accounting.json','preservation.json','ci-evidence-status.json'}
WORKER_FILES={'toolchain.json','binary-inventory.json','enforcement.json','source-archives.json','toolchain-lock.json','verified-source-archives.json','applied-patches.json','os-release.txt','packages.txt','srsran-commands.txt','open5gs-commands.txt','open5gs-rsp-review.json','open5gs-rsp-expanded.json','srs-compile-commands.json','ogs-compile-commands.json','CMakeCache.txt','meson-log.txt','worker-completion.json','worker-status.json'}|{n+'.deps.txt' for n in ('runtime_driver.cpp','snapshot_observer.cpp','cu_cp_impl.cpp','cu_cp_controller.cpp','amf_connection_manager.cpp')}
INSPECTION_EXTENSIONS=('readelf','objdump','symbols')
INSPECTION_MAX_BINARIES=517  # Five executables plus the existing 512-library budget.
INSPECTION_TOTAL_LIMIT=256*1024**2  # Existing native evidence aggregate limit.
def inspection_manifest(root):
    """Admit exact inventory-derived text artifacts, never a suffix wildcard."""
    import re,stat
    root=Path(root);inventory_path=checked(root,'worker/binary-inventory.json')
    inventory=json.loads(inventory_path.read_text())
    if not isinstance(inventory,dict) or not 1<=len(inventory)<=INSPECTION_MAX_BINARIES:raise Blocked('INSPECTION_BINARY_COUNT')
    phase=json.loads(checked(root,'worker/binary_inspection.receipt.json').read_text())
    supervisor=json.loads(checked(root,'supervisor-final.json').read_text())
    inventory_hash=digest(inventory_path)
    if phase.get('outputs',{}).get('/work/evidence/binary-inventory.json')!=inventory_hash or supervisor.get('evidence_sha256',{}).get('worker/binary-inventory.json')!=inventory_hash:raise Blocked('INSPECTION_INVENTORY_BINDING')
    result={};total=0
    for index,(binary,row) in enumerate(inventory.items()):
        if not isinstance(binary,str) or not re.fullmatch(r'(?:srs-build|ogs-build)/(?:[A-Za-z0-9_.+-]+/)*[A-Za-z0-9_.+-]+',binary) or any(part in ('.','..') for part in binary.split('/')):raise Blocked('INSPECTION_BINARY_PATH')
        if not isinstance(row,dict) or set(row)!={'sha256','readelf_sha256','objdump_sha256','symbols_sha256','inspection_artifacts'} or not isinstance(row['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',row['sha256']):raise Blocked('INSPECTION_BINARY_SCHEMA')
        artifacts=row.get('inspection_artifacts')
        if not isinstance(artifacts,dict) or set(artifacts)!=set(INSPECTION_EXTENSIONS):raise Blocked('INSPECTION_ARTIFACT_INVENTORY')
        for ext in INSPECTION_EXTENSIONS:
            name=str(index)+'-'+Path(binary).name+'.'+ext;entry=artifacts[ext]
            if not isinstance(entry,dict) or set(entry)!={'path','sha256'} or entry['path']!=name:raise Blocked('INSPECTION_ARTIFACT_PATH')
            h=entry['sha256']
            if not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) or row.get(ext+'_sha256')!=h:raise Blocked('INSPECTION_ARTIFACT_HASH_BINDING')
            path='worker/'+name;f=checked(root,path);meta=f.lstat()
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid() or meta.st_nlink!=1 or meta.st_mode&0o022:raise Blocked('INSPECTION_FILE_OWNERSHIP_OR_MODE')
            total+=meta.st_size
            if total>INSPECTION_TOTAL_LIMIT:raise Blocked('INSPECTION_TOTAL_BUDGET')
            if digest(f)!=h:raise Blocked('INSPECTION_ARTIFACT_CORRUPTION')
            if phase.get('outputs',{}).get(str(f))!=h and phase.get('outputs',{}).get('/work/evidence/'+name)!=h:raise Blocked('INSPECTION_RECEIPT_BINDING')
            if supervisor.get('evidence_sha256',{}).get(path)!=h:raise Blocked('INSPECTION_SUPERVISOR_BINDING')
            result[path]=h
    actual={str(f.relative_to(root)) for f in (root/'worker').iterdir() if f.suffix in {'.readelf','.objdump','.symbols'}}
    if actual!=set(result):raise Blocked('UNEXPECTED_INSPECTION_OUTPUT')
    return result

def validate_toolchain_diagnostics(root):
    root=Path(root);r=json.loads(checked(root,'worker/dependency_acquisition.receipt.json').read_text())
    for name in ACQUISITION_DIAGNOSTICS:
        f=checked(root,'worker/acquisition-'+name)
        if digest(f)!=r.get('outputs',{}).get(name):raise Blocked('UNBOUND_TOOLCHAIN_DIAGNOSTIC')
    lockfile=checked(root,'worker/toolchain-lock.json');lock=json.loads(lockfile.read_text())
    if lock.get('policy_sha256')!=TOOLCHAIN_POLICY_SHA256 or lock.get('approved_selection') is not True:raise Blocked('UNAPPROVED_TOOLCHAIN_LOCK')
    if r['outputs'].get('toolchain-lock.json')!=digest(lockfile):raise Blocked('UNBOUND_TOOLCHAIN_LOCK')
    for prefix,operation in [('acquisition-','toolchain-operation'),('','toolchain-verification')]:
        entry=json.loads(checked(root,'worker/'+prefix+operation+'.receipt.json').read_text());successful(entry);timing(entry)
        if entry.get('forced') is not False or entry.get('log_budget_exceeded') is not False:raise Blocked('INCOMPLETE_TOOLCHAIN_OPERATION')
        if digest(checked(root,'worker/'+prefix+operation+'.log'))!=entry['log_sha256']:raise Blocked('TOOLCHAIN_LOG_HASH')
        diag=json.loads(checked(root,'worker/'+prefix+'toolchain-progress.json').read_text())
        if diag.get('classification')!='PASS' or diag.get('stage')!='completed' or diag.get('policy')!=TOOLCHAIN_POLICY:raise Blocked('INCOMPLETE_TOOLCHAIN_PROGRESS')
    for name in ('toolchain-versions','toolchain-database','toolchain-paths'):
        entry=json.loads(checked(root,'worker/acquisition-'+name+'.receipt.json').read_text());successful(entry);timing(entry)
        if digest(checked(root,'worker/acquisition-'+name+'.log'))!=entry['log_sha256']:raise Blocked('TOOLCHAIN_QUERY_LOG_HASH')

def export_bundle(root,bundle):
    # Upload only reviewed evidence paths. Never upload a workspace or source tree.
    root=Path(root);bundle=Path(bundle)
    if bundle.exists():raise Blocked('UPLOAD_BUNDLE_COLLISION')
    bundle.mkdir(mode=0o700)
    names=set(ROOT_FILES)|{'inputs/'+name for name in INPUT_FILES}
    names|={'worker/'+n for n in WORKER_FILES}
    names|={'worker/acquisition-'+n for n in ACQUISITION_DIAGNOSTICS}
    names|={'worker/'+n for n in ('toolchain-verification.log','toolchain-verification.receipt.json','toolchain-progress.json')}
    for phase in PHASES:
        names|={'worker/'+phase+'.receipt.json','worker/'+phase+'.log'}
        names|={'worker/'+phase+'.'+str(i)+'.log' for i in range(18)}
    auxnames=[x+'.receipt.json' for x in AUX]+[x+'.log' for x in AUX]+['l3-wheel-acquisition/acquisition.json','l3-python-setup/setup.json']+['l3-python-setup/'+str(i)+'.log' for i in range(5)]
    names|={'auxiliary/'+n for n in auxnames}
    hashes={};errors=[];total=0
    if (root/'worker/binary-inventory.json').exists():
        try:names|=set(inspection_manifest(root))
        except (Blocked,ValueError,TypeError,KeyError) as e:errors.append('INVALID_INSPECTION_MANIFEST:'+str(e))
    for name in sorted(names):
        source=root/name
        if not source.exists() and not source.is_symlink():continue
        try:
            f=checked(root,name);total+=f.stat().st_size
            if total>512*1024**2:raise Blocked('UPLOAD_TOTAL_BUDGET')
            dst=bundle/name;dst.parent.mkdir(mode=0o700,parents=True,exist_ok=True);shutil.copyfile(f,dst);dst.chmod(0o600);hashes[name]=digest(dst)
        except Blocked as e:errors.append(str(e))
    # Refuse unnoticed additions, rather than accidentally exporting arbitrary content.
    unexpected=[str(f.relative_to(root)) for f in root.rglob('*') if (f.is_file() or f.is_symlink()) and str(f.relative_to(root)) not in names]
    errors+=['UNREVIEWED_EXPORT_PATH:'+n for n in unexpected]
    atomic(bundle/'upload-manifest.json',{'classification':'INCOMPLETE' if errors else 'PASS','sha256':hashes,'errors':errors,'bytes':total})
    return not errors

def qualify_upload(root,outcome,artifact_id,artifact_digest):
    root=Path(root);m=json.loads(checked(root,'upload-manifest.json').read_text())
    if m.get('classification')!='PASS' or m.get('errors')!=[] or not m.get('sha256'):raise Blocked('UPLOAD_MANIFEST_INCOMPLETE')
    actual={str(f.relative_to(root)) for f in root.rglob('*') if f.is_file()}
    if actual!=set(m['sha256'])|{'upload-manifest.json'}:raise Blocked('UPLOAD_INVENTORY_DRIFT')
    for name,h in m['sha256'].items():
        if digest(checked(root,name))!=h:raise Blocked('UPLOAD_HASH_MISMATCH')
    # Revalidate whole evidence; a forged earlier PASS status never substitutes.
    from finalize_build_evidence import validate
    validate(root)
    return upload_admission(outcome,artifact_id,artifact_digest,True)

if __name__=='__main__':
    if sys.argv[1:2]==['run'] and len(sys.argv)==3: run_aux(sys.argv[2])
    elif sys.argv[1:]==['collect']: raise SystemExit(0 if collect(os.environ['RUNNER_TEMP'],'stage-d1-build-results') else 1)
    elif sys.argv[1:]==['export']: raise SystemExit(0 if export_bundle('stage-d1-build-results','stage-d1-reviewed-evidence') else 1)
    elif sys.argv[1:2]==['upload'] and len(sys.argv)==6:
        if os.environ.get('PRIOR_JOB_STATUS')!='success':raise Blocked('EARLIER_WORKFLOW_STEP_FAILED_OR_CANCELLED')
        print(json.dumps(qualify_upload(sys.argv[5],sys.argv[2],sys.argv[3],sys.argv[4])))
    else: raise SystemExit('UNAPPROVED_EVIDENCE_OPERATION')
