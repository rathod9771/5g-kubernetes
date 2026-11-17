"""Future acquisition only. Imports/tests do not install or contact package indexes."""
import datetime,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
# Standalone: -I removes the script directory from sys.path. Import no local modules.
class Blocked(RuntimeError):pass
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def atomic(path,value):
    temp=path.with_name(path.name+'.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
POLICY={'approved_pip_version':None,'connection_retries':0,'resume_retries':0,'socket_timeout_seconds':15,'interactive':False,'version_check':False,'configuration':'EMPTY_ENV_AND_PIP_CONFIG_FILE_DEVNULL','index_url':'https://pypi.org/simple','native_downloads':False}
# There was no approved pip pin in D2A. Do not silently bootstrap/upgrade one.
OPTIONS=('--retries','--resume-retries','--timeout','--no-input','--disable-pip-version-check','--no-cache-dir')
def admission(version,help_text,policy):
    if not policy.get('approved_pip_version'):raise Blocked('APPROVED_PIP_VERSION_REQUIRED')
    if version!=policy['approved_pip_version']:raise Blocked('PIP_VERSION_MISMATCH')
    if any(not re.search(r'(?<![\w-])'+re.escape(opt)+r'(?![\w-])',help_text) for opt in OPTIONS):raise Blocked('PIP_REQUIRED_OPTION_UNSUPPORTED')
    if policy!=dict(POLICY,approved_pip_version=policy['approved_pip_version']):raise Blocked('PIP_ACQUISITION_POLICY_DRIFT')
    return True

def clean_environment(home,tmp):
    return {'PATH':'/usr/bin:/bin','HOME':str(home),'TMPDIR':str(tmp),'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PIP_CONFIG_FILE':os.devnull}
def install_command(python,requirements):
    return [str(python),'-I','-m','pip','--retries','0','--resume-retries','0','--timeout','15','--no-input','--disable-pip-version-check','--no-cache-dir','install','--require-hashes','--only-binary=:all:','--no-deps','--index-url',POLICY['index_url'],'-r',str(requirements)]
def main():
    if sys.argv[1:]!=['--github-acquire-only'] or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('HOSTED_ACQUISITION_ONLY')
    root=Path(os.environ['RUNNER_TEMP'])/'l3-pip-acquisition';root.mkdir(mode=0o700)
    home=root/'home';home.mkdir(mode=0o700);tmp=root/'tmp';tmp.mkdir(mode=0o700)
    env=clean_environment(home,tmp);p=Path(__file__).resolve().parent;start=time.monotonic()
    record={'policy':POLICY,'started_at':stamp(),'classification':'BLOCKED','installed':False,'requirements_sha256':digest(p/'python-requirements.PROPOSED.txt')}
    try:
        # Absence of an approved installer version is a stop, not permission to upgrade pip.
        if not POLICY['approved_pip_version']:raise Blocked('APPROVED_PIP_VERSION_REQUIRED')
        version=subprocess.check_output([sys.executable,'-I','-m','pip','--version'],env=env,text=True,timeout=15)
        match=re.fullmatch(r'pip ([0-9][^ ]*) from .+ \(python [^)]+\)\n?',version)
        if not match:raise Blocked('PIP_VERSION_UNPARSEABLE')
        help_text=subprocess.check_output([sys.executable,'-I','-m','pip','--help'],env=env,text=True,timeout=15)
        admission(match[1],help_text,POLICY)
        record.update(actual_version=match[1],option_inventory_sha256=__import__('hashlib').sha256(help_text.encode()).hexdigest())
        argv=install_command(sys.executable,p/'python-requirements.PROPOSED.txt');record['command']=argv
        record['classification']='INCOMPLETE'
        subprocess.run(argv,env=env,check=True,timeout=240)
        record.update(classification='PASS',installed=True)
    except BaseException as e:
        record['reason']=str(e) if isinstance(e,Blocked) else type(e).__name__
        record['classification']='BLOCKED' if isinstance(e,Blocked) else 'FAIL' if isinstance(e,subprocess.CalledProcessError) else 'INCOMPLETE'
        raise
    finally:
        record.update(ended_at=stamp(),elapsed_seconds=time.monotonic()-start);atomic(root/'pip-acquisition.json',record)
if __name__=='__main__':main()
