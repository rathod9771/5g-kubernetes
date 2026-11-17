"""Proposed future offline Python setup. Import and plan tests perform no installation."""
import hashlib,json,os,platform,stat,subprocess,sys,time
from pathlib import Path
from build_receipts import command
from pip_acquisition import Blocked,atomic,clean_environment,digest,stamp
P=Path(__file__).resolve().parent
PIN=json.loads((P/'installer-pin.PROPOSED.json').read_text())
def compatibility(version,architecture):
    if tuple(version[:2])!=(3,10) or architecture!='x86_64':raise Blocked('UNREVIEWED_PYTHON_OR_PLATFORM')
    return True

def inventory():
    return [dict(x,filename=x['wheel']) for x in json.loads((P/'python-wheels.PROPOSED.json').read_text())]+[PIN]

def verify_wheelhouse(root,records):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise Blocked('MISSING_OR_UNSAFE_WHEELHOUSE')
    expected={x['filename'] for x in records}
    if len(expected)!=len(records) or {f.name for f in root.iterdir()}!=expected:raise Blocked('WHEELHOUSE_INVENTORY')
    for x in records:
        f=root/x['filename'];meta=f.lstat()
        if f.name!=x['filename'] or Path(x['filename']).name!=x['filename'] or not f.name.endswith('.whl') or not stat.S_ISREG(meta.st_mode) or meta.st_size>32*1024**2 or digest(f)!=x['sha256']:raise Blocked('WHEEL_HASH_OR_FILE_TYPE')
    return True

def commands(python,root,requirements,venv):
    root=Path(root).resolve();venv=Path(venv).resolve();vp=venv/'bin/python3';wheel=root/PIN['filename']
    flags=['--retries','0','--resume-retries','0','--timeout','15','--no-input','--disable-pip-version-check','--no-cache-dir']
    # __main__.py at the pinned pip source explicitly supports wheel/pip execution.
    prefix=[str(vp),'-I',str(wheel)+'/pip',*flags]
    return [[str(python),'-I','-m','venv','--without-pip',str(venv)],prefix+['--version'],prefix+['--help'],prefix+['install','--no-index','--find-links',str(root),'--require-hashes','--only-binary=:all:','--no-deps','--no-compile','-r',str(requirements)]]

def main():
    if len(sys.argv)!=4 or sys.argv[1]!='--github-offline-setup' or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('HOSTED_SETUP_ONLY')
    compatibility(sys.version_info,platform.machine())
    root=Path(sys.argv[2]);venv=Path(sys.argv[3]);temp=Path(os.environ['RUNNER_TEMP']).resolve()
    if root.resolve()!=temp/'l3-reviewed-wheelhouse' or venv!=temp/'d1-python' or venv.exists():raise Blocked('UNEXPECTED_SETUP_PATH_OR_COLLISION')
    evidence=temp/'l3-python-setup';evidence.mkdir(mode=0o700);home=evidence/'home';home.mkdir();tmp=evidence/'tmp';tmp.mkdir();env=clean_environment(home,tmp)
    start=time.monotonic();r={'started_at':stamp(),'status':'INCOMPLETE','native_execution':False,'installer':PIN,'commands':[]}
    try:
        records=inventory();verify_wheelhouse(root,records)
        req=P/'python-requirements.PROPOSED.txt';r['requirements_sha256']=digest(req)
        # CLI only uses hash-bound reviewed requirements, never supplied arbitrary requirements.
        argv=commands(sys.executable,root,req,venv)
        for i,cmd in enumerate(argv):
            verify_wheelhouse(root,records)
            log=evidence/(str(i)+'.log')
            entry=command(cmd,P,env,log,240);r['commands'].append(entry)
            if entry['classification']!='PASS':raise Blocked('SETUP_COMMAND_'+entry['classification'])
            if log.stat().st_size>1024**2:raise Blocked('PYTHON_SETUP_LOG_BUDGET')
            output=log.read_text()
            if i==1 and not output.startswith('pip '+PIN['version']+' '):raise Blocked('INSTALLER_VERSION_MISMATCH')
            if i==2:
                from pip_acquisition import admission,POLICY
                admission(PIN['version'],output,dict(POLICY,approved_pip_version=PIN['version']))
        # Do not install pip into the venv; record exact installed dependency metadata.
        code='import importlib.metadata,json,re; print(json.dumps({re.sub("[-_.]+","-",d.metadata["Name"].lower()):d.version for d in importlib.metadata.distributions()},sort_keys=True))'
        cmd=[str(venv/'bin/python3'),'-I','-c',code]
        entry=command(cmd,P,env,evidence/'4.log',15);r['commands'].append(entry)
        if entry['classification']!='PASS':raise Blocked('METADATA_COMMAND_'+entry['classification'])
        if (evidence/'4.log').stat().st_size>1024**2:raise Blocked('PYTHON_SETUP_LOG_BUDGET')
        installed=json.loads((evidence/'4.log').read_text())
        expected={__import__('re').sub('[-_.]+','-',x['name'].lower()):x['version'] for x in records[:-1]}
        if installed!=expected:raise Blocked('INSTALLED_DEPENDENCY_VERSION_OR_EXTRA_PACKAGE')
        r.update(status='PASS_OFFLINE_PYTHON_SETUP',installed=installed,installer_installed=False,wheel_hashes={x['filename']:x['sha256'] for x in records})
    except BaseException as e:
        r.update(status='BLOCKED' if isinstance(e,Blocked) else 'INCOMPLETE',reason=str(e) if isinstance(e,Blocked) else type(e).__name__);raise
    finally:r.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start);atomic(evidence/'setup.json',r)
if __name__=='__main__':main()
