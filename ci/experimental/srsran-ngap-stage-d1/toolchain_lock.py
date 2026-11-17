"""Bounded installed-package toolchain provenance. No scan/query occurs on import."""
import hashlib,json,os,re,stat,subprocess,sys,time
from pathlib import Path
from build_policy import Blocked,successful,verify_receipt
from build_receipts import atomic,stamp,command
MAX_FILES=40000;MAX_BYTES=2*1024**3;MAX_FILE_BYTES=256*1024**2
MAX_PACKAGES=512;MAX_DATABASE=10000;MAX_METADATA=16*1024**2
SCAN_SECONDS=180;QUERY_SECONDS=15;MAX_DIRECTORY_ENTRIES=200000
ROOT_PACKAGES=('build-essential','cmake','ninja-build','meson','pkg-config','bison','flex','libsctp-dev','libgnutls28-dev','libgcrypt20-dev','libssl-dev','libidn11-dev','libmongoc-dev','libbson-dev','libyaml-dev','libnghttp2-dev','libmicrohttpd-dev','libcurl4-gnutls-dev','libtalloc-dev','libmbedtls-dev','libyaml-cpp-dev','libfftw3-dev','libgtest-dev','python3-venv','binutils','bubblewrap','iproute2','e2fsprogs','util-linux','systemd','git','dpkg','coreutils','python3')
ROOTS=('/usr/bin/cc','/usr/bin/c++','/usr/bin/ld','/usr/bin/ar','/usr/bin/nm','/usr/bin/readelf','/usr/bin/objdump','/usr/bin/cmake','/usr/bin/meson','/usr/bin/ninja','/usr/bin/pkg-config','/usr/bin/bison','/usr/bin/flex','/usr/bin/git','/usr/bin/python3','/etc/ld.so.cache','/etc/ld.so.conf')
POLICY={'schema':2,'root_packages':list(ROOT_PACKAGES),'required_paths':list(ROOTS),'selection':'INSTALLED_PACKAGE_DEPENDENCY_CLOSURE_ALL_INSTALLED_ALTERNATIVES_AND_PROVIDERS','excluded_share_groups':['doc','man','info','locale','lintian','bash-completion','zsh'],'excluded_runtime_prefixes':['/usr/lib/ssl/private','/usr/lib/ssl/certs','/usr/lib/ssl/openssl.cnf','/usr/lib/systemd/system/','/usr/lib/systemd/user/','/usr/lib/systemd/system-preset/','/usr/lib/systemd/user-preset/','/usr/lib/systemd/resolv.conf','/usr/lib/tmpfiles.d/','/usr/lib/sysusers.d/','/usr/lib/udev/rules.d/'],'generated_cache_rule':'EXACT_SELECTED_MODULE___pycache___AND_HEADER_GCH_ONLY','file_limit':MAX_FILES,'bytes_limit':MAX_BYTES,'file_bytes_limit':MAX_FILE_BYTES,'package_limit':MAX_PACKAGES,'scan_seconds':SCAN_SECONDS,'query_seconds':QUERY_SECONDS}
ACQUISITION_DIAGNOSTICS=('toolchain-selection.json','toolchain-operation.log','toolchain-operation.receipt.json','toolchain-progress.json','toolchain-versions.log','toolchain-versions.receipt.json','toolchain-database.log','toolchain-database.receipt.json','toolchain-paths.log','toolchain-paths.receipt.json')
POLICY_SHA256=hashlib.sha256(json.dumps(POLICY,sort_keys=True).encode()).hexdigest()

def check_deadline(deadline):
    if deadline is not None and time.monotonic()>=deadline:raise Blocked('TOOLCHAIN_TIMEOUT')

def streaming_hash(path,deadline=None):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while True:
            check_deadline(deadline);data=f.read(1024*1024)
            if not data:break
            h.update(data)
    return h.hexdigest()

def entries(roots,deadline):
    # Used for small fixture directories; the hosted plan supplies individual package paths.
    queue=list(map(Path,roots));count=0
    while queue:
        check_deadline(deadline);p=queue.pop();count+=1
        if count>MAX_DIRECTORY_ENTRIES:raise Blocked('TOOLCHAIN_DIRECTORY_BUDGET')
        if p.is_dir() and not p.is_symlink():
            with os.scandir(p) as stream:
                for row in stream:
                    queue.append(Path(row.path))
                    if len(queue)+count>MAX_DIRECTORY_ENTRIES:raise Blocked('TOOLCHAIN_DIRECTORY_BUDGET')
        else:yield p

def freeze(roots,package_versions,progress=None,deadline=None,approved=False):
    deadline=time.monotonic()+SCAN_SECONDS if deadline is None else deadline
    if not package_versions.strip():raise Blocked('MISSING_INSTALLED_PACKAGE_VERSION_LOCK')
    if progress:progress('filesystem_enumeration',roots=len(roots))
    files={};total=0;queue=list(entries(roots,deadline));seen=set();directories={};last=0;directory_entries=0;snapshots={}
    while queue:
        check_deadline(deadline);p=queue.pop();name=str(p)
        if approved and not allowed_file(name) and name not in ('/usr/lib','/usr/lib64','/usr/bin','/usr/sbin'):raise Blocked('UNREVIEWED_TOOLCHAIN_SYMLINK_TARGET:'+name)
        if name in seen:continue
        seen.add(name)
        if len(files)>=MAX_FILES:raise Blocked('TOOLCHAIN_FILE_BUDGET')
        if progress and (len(files)%128==0 or time.monotonic()-last>1):progress('filesystem_enumeration',files=len(files),bytes=total,current_path=name);last=time.monotonic()
        try:meta=p.lstat()
        except FileNotFoundError:raise Blocked('MISSING_TOOLCHAIN_INPUT:'+name) from None
        if stat.S_ISLNK(meta.st_mode):
            try:p.resolve(strict=True)
            except (OSError,RuntimeError):raise Blocked('UNRESOLVED_TOOLCHAIN_SYMLINK:'+name) from None
            link=os.readlink(p);files[name]={'symlink':link};target=Path(os.path.normpath(str(p.parent/link))) if not os.path.isabs(link) else Path(link)
            queue.append(target)
        elif stat.S_ISREG(meta.st_mode):
            total+=meta.st_size
            if meta.st_size>MAX_FILE_BYTES or total>MAX_BYTES:raise Blocked('TOOLCHAIN_BYTE_BUDGET')
            files[name]={'sha256':None,'bytes':meta.st_size};snapshots[name]=meta
        elif stat.S_ISDIR(meta.st_mode):files[name]={'directory':True}
        else:raise Blocked('UNEXPECTED_TOOLCHAIN_OBJECT:'+name)
        # Record search-directory membership, including unselected names; detect additions.
        if str(p.parent) not in directories:
            names=[]
            with os.scandir(p.parent) as it:
                for row in it:
                    check_deadline(deadline);names.append(row.name);directory_entries+=1
                    if directory_entries>MAX_DIRECTORY_ENTRIES:raise Blocked('TOOLCHAIN_DIRECTORY_BUDGET')
            directories[str(p.parent)]=sorted(names)
    if not files:raise Blocked('EMPTY_TOOLCHAIN_LOCK')
    last=0
    if progress:progress('file_hashing',files=len(files),bytes=total,hashed_files=0)
    for index,(name,meta) in enumerate(snapshots.items()):
        check_deadline(deadline)
        if progress and (index%128==0 or time.monotonic()-last>1):progress('file_hashing',files=len(files),bytes=total,hashed_files=index,current_path=name);last=time.monotonic()
        p=Path(name);files[name]['sha256']=streaming_hash(p,deadline);after=p.lstat()
        if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(meta.st_dev,meta.st_ino,meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns) or p.is_symlink():raise Blocked('TOOLCHAIN_CHANGED_DURING_HASH')
    return {'approved_selection':approved,'schema':2,'policy_sha256':POLICY_SHA256,'scope':'EXPLICIT_INSTALLED_BUILD_PACKAGE_CLOSURE_NOT_ENTIRE_HOST','roots':list(map(str,roots)),'package_versions':package_versions,'files':files,'directories':directories,'total_regular_bytes':total}

def verify(lock,versions,progress=None,require_approved=False):
    if lock.get('schema')!=2 or lock.get('policy_sha256')!=POLICY_SHA256:raise Blocked('UNAPPROVED_TOOLCHAIN_POLICY')
    if not lock.get('files') or lock.get('package_versions')!=versions:raise Blocked('PACKAGE_VERSION_DRIFT')
    if require_approved:
        if lock.get('approved_selection') is not True or not lock.get('selected_packages'):raise Blocked('UNAPPROVED_TOOLCHAIN_SELECTION')
        installed={parts[0]:parts[1] for line in versions.splitlines() if len(parts:=line.split())==2}
        if any(installed.get(pkg)!=version for pkg,version in lock['selected_packages'].items()):raise Blocked('SELECTED_PACKAGE_VERSION_DRIFT')
        if not set(ROOTS)<=set(lock['roots']):raise Blocked('MISSING_REQUIRED_TOOLCHAIN_PATH')
    fresh=freeze(lock['roots'],versions,progress,approved=lock.get('approved_selection',False))
    for field in ('files','directories','total_regular_bytes'):
        if fresh[field]!=lock.get(field):raise Blocked('TOOLCHAIN_CONTENT_OR_INPUT_SET_DRIFT')
    return True

def parse_database(text):
    records={}
    for line in text.splitlines():
        parts=line.split('\t')
        if len(parts)!=5:raise Blocked('MALFORMED_PACKAGE_DATABASE')
        name,version,status,deps,provides=parts
        if status!='ii ':continue
        if not re.fullmatch('[a-z0-9][a-z0-9+.:~-]*',name) or not version or name in records:raise Blocked('PACKAGE_DATABASE_IDENTITY')
        records[name]={'version':version,'depends':deps,'provides':provides}
        if len(records)>MAX_DATABASE:raise Blocked('PACKAGE_DATABASE_BUDGET')
    if not records:raise Blocked('EMPTY_PACKAGE_DATABASE')
    return records

def dependency_closure(records,roots=ROOT_PACKAGES):
    providers={}
    def add(name,pkg):providers.setdefault(name,set()).add(pkg)
    for pkg,row in records.items():
        add(pkg,pkg);add(pkg.split(':')[0],pkg)
        for value in row['provides'].split(','):
            token=value.strip().split(' ')[0]
            if token:add(token,pkg)
    selected=set();queue=list(roots)
    while queue:
        dep=queue.pop();matches=providers.get(dep) or providers.get(dep.removesuffix(':any').removesuffix(':native'))
        if not matches:raise Blocked('UNRESOLVED_TOOLCHAIN_PACKAGE:'+dep)
        for pkg in matches:
            if pkg in selected:continue
            selected.add(pkg)
            if len(selected)>MAX_PACKAGES:raise Blocked('TOOLCHAIN_PACKAGE_BUDGET')
            for group in records[pkg]['depends'].split(','):
                if not group.strip():continue
                available=[]
                for alt in group.split('|'):
                    token=alt.strip().split(' ')[0];key=token.removesuffix(':any').removesuffix(':native')
                    if key in providers:available.append(key)
                if not available:raise Blocked('UNRESOLVED_TOOLCHAIN_DEPENDENCY:'+group)
                queue.extend(available)
    return sorted(selected)

def allowed_file(name):
    p=Path(name)
    if not p.is_absolute() or '..' in p.parts:return False
    if name.startswith(tuple(POLICY['excluded_runtime_prefixes'])):return False
    if name.startswith(('/usr/bin/','/usr/sbin/','/bin/','/sbin/','/usr/include/','/usr/lib/','/usr/libexec/','/lib/','/lib64/','/usr/src/googletest/','/usr/src/gtest/')):return True
    if name.startswith('/usr/share/'):
        return p.parts[3] not in POLICY['excluded_share_groups'] if len(p.parts)>3 else False
    return name.startswith('/etc/alternatives/') or name in ROOTS or name.startswith('/etc/ld.so.conf.d/')

def include_generated_caches(paths):
    result=set(paths)
    for name in paths:
        p=Path(name);candidates=[]
        if p.suffix=='.py':candidates.append(p.parent/'__pycache__')
        if p.suffix in ('.h','.hh','.hpp'):candidates.append(Path(str(p)+'.gch'))
        for cache in candidates:
            if cache.is_symlink():raise Blocked('UNREVIEWED_GENERATED_CACHE_SYMLINK')
            if cache.exists():result.add(str(cache))
        if len(result)>MAX_FILES:raise Blocked('TOOLCHAIN_FILE_BUDGET')
    return result

class Diagnostics:
    def __init__(self,root):self.root=Path(root);self.start=time.monotonic();self.events=[];self.state={'classification':'INCOMPLETE','started_at':stamp(),'events':self.events,'policy':POLICY}
    def __call__(self,stage,**fields):
        self.state.update(stage=stage,updated_at=stamp(),elapsed_seconds=time.monotonic()-self.start,**fields)
        self.events.append({'stage':stage,'at':stamp(),**fields})
        if len(self.events)>2048:self.events=self.events[-2048:];self.state['events']=self.events
        atomic(self.root/'toolchain-progress.json',self.state)
    def finish(self,classification,reason=None):
        self.state.update(classification=classification,ended_at=stamp(),reason=reason,last_operation=self.state.get('stage'));self('completed' if classification=='PASS' else 'aborted')

def query(argv,root,name,progress):
    # dpkg-query does not spawn children. Inherit the outer process group for hard kill.
    progress(name,command=argv);start=time.monotonic();log=Path(root)/(name+'.log');p=None;receipt={'argv':argv,'started_at':stamp(),'classification':'INCOMPLETE','exit_code':None,'signal':None,'timeout':False,'ended_at':None,'elapsed_seconds':None,'log':log.name,'log_sha256':None}
    atomic(Path(root)/(name+'.receipt.json'),receipt)
    try:
        with log.open('xb') as stream:
            p=subprocess.Popen(argv,stdout=stream,stderr=subprocess.STDOUT)
            try:
                while p.poll() is None:
                    if time.monotonic()-start>=QUERY_SECONDS or log.stat().st_size>MAX_METADATA:
                        receipt['timeout']=time.monotonic()-start>=QUERY_SECONDS;p.kill();p.wait();raise Blocked('TOOLCHAIN_QUERY_TIMEOUT_OR_SIZE')
                    time.sleep(.01)
            finally:
                if p.poll() is None:p.kill();p.wait()
        receipt.update(exit_code=p.returncode if p.returncode>=0 else None,signal=-p.returncode if p.returncode<0 else None)
        if p.returncode!=0 or log.stat().st_size>MAX_METADATA:raise Blocked('TOOLCHAIN_QUERY_FAILED')
        receipt['classification']='PASS';return log.read_text()
    finally:
        if p is not None and p.returncode is not None:
            receipt.update(exit_code=p.returncode if p.returncode>=0 else None,signal=-p.returncode if p.returncode<0 else None)
            if p.returncode and receipt['timeout'] is False:receipt['classification']='FAIL'
        receipt.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start,log=log.name,log_sha256=streaming_hash(log) if log.exists() else None)
        atomic(Path(root)/(name+'.receipt.json'),receipt)

def acquire(root):
    root=Path(root);d=Diagnostics(root)
    try:
        versions=query(['/usr/bin/dpkg-query','-W'],root,'toolchain-versions',d)
        fmt='${binary:Package}\t${Version}\t${db:Status-Abbrev}\t${Pre-Depends}, ${Depends}\t${Provides}\n'
        db=parse_database(query(['/usr/bin/dpkg-query','-W','-f='+fmt],root,'toolchain-database',d))
        d('package_selection');packages=dependency_closure(db)
        listing=query(['/usr/bin/dpkg-query','-L',*packages],root,'toolchain-paths',d)
        d('file_selection',packages=len(packages));paths=set(ROOTS);excluded=[]
        for name in listing.splitlines():
            if not name.startswith('/'):
                if name and not name.startswith('diversion '):raise Blocked('UNEXPECTED_PACKAGE_PATH_RECORD')
                continue
            if allowed_file(name):
                p=Path(name)
                if p.is_symlink() or not p.is_dir():paths.add(name)
            else:excluded.append(name)
        # ldconfig search configuration is public, bounded and not package-list recursive.
        directory=Path('/etc/ld.so.conf.d')
        if not directory.is_dir():raise Blocked('MISSING_LOADER_CONFIGURATION_DIRECTORY')
        for p in directory.iterdir():
            if len(paths)>MAX_FILES:raise Blocked('TOOLCHAIN_FILE_BUDGET')
            if not p.name.endswith('.conf'):raise Blocked('UNREVIEWED_LOADER_CONFIG_ENTRY')
            paths.add(str(p))
        if len(paths)>MAX_FILES:raise Blocked('TOOLCHAIN_FILE_BUDGET')
        paths=include_generated_caches(paths)
        atomic(root/'toolchain-selection.json',{'policy_sha256':POLICY_SHA256,'root_packages':list(ROOT_PACKAGES),'selected_packages':{pkg:db[pkg]['version'] for pkg in packages},'selected_paths':sorted(paths),'excluded_package_paths':excluded,'exclusion_policy':POLICY})
        d('file_enumeration',selected_paths=len(paths),excluded_package_paths=len(excluded))
        lock=freeze(sorted(paths),versions,d,approved=True)
        lock['selected_packages']={p:db[p]['version'] for p in packages}
        atomic(root/'toolchain-lock.json',lock);d.finish('PASS')
    except BaseException as e:d.finish('INCOMPLETE' if 'TIMEOUT' in str(e) else 'BLOCKED',str(e) if isinstance(e,Blocked) else type(e).__name__);raise

def verify_acquisition(root):
    root=Path(root);r=json.loads((root/'dependency_acquisition.receipt.json').read_text());verify_receipt(r,'dependency_acquisition')
    for name in (*ACQUISITION_DIAGNOSTICS,'toolchain-lock.json'):
        f=root/name
        if not f.is_file() or f.is_symlink() or streaming_hash(f)!=r['outputs'].get(name):raise Blocked('UNBOUND_TOOLCHAIN_ACQUISITION_INPUT')
    successful(json.loads((root/'toolchain-operation.receipt.json').read_text()))
    diag=json.loads((root/'toolchain-progress.json').read_text())
    if diag.get('classification')!='PASS' or diag.get('policy')!=POLICY:raise Blocked('INCOMPLETE_TOOLCHAIN_ACQUISITION')

def run_bounded(mode,root,env,cwd,versions_path=None):
    root=Path(root);argv=['/usr/bin/python3','-B',str(Path(__file__).resolve()),mode,str(root)]
    if versions_path is not None:argv.append(str(versions_path))
    name='toolchain-operation' if mode=='--acquire' else 'toolchain-verification'
    receipt=command(argv,cwd,env,root/(name+'.log'),SCAN_SECONDS)
    atomic(root/(name+'.receipt.json'),receipt)
    if receipt['classification']!='PASS':raise Blocked('TOOLCHAIN_OPERATION_'+receipt['classification'])
    return receipt

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--acquire':
        if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('HOSTED_TOOLCHAIN_ACQUISITION_ONLY')
        acquire(sys.argv[2])
    elif len(sys.argv)==4 and sys.argv[1]=='--verify':
        root=Path(sys.argv[2]);d=Diagnostics(root)
        try:verify(json.loads((root/'toolchain-lock.json').read_text()),Path(sys.argv[3]).read_text(),d,require_approved=True);d.finish('PASS')
        except BaseException as e:d.finish('BLOCKED',str(e) if isinstance(e,Blocked) else type(e).__name__);raise
    else:raise SystemExit('UNAPPROVED_TOOLCHAIN_OPERATION')
