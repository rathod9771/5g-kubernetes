"""Bounded command execution and atomic receipts, no commands run on import."""
import datetime,hashlib,json,os,signal,subprocess,time
from pathlib import Path
from build_policy import Blocked,digest
MAX_LOG=64*1024**2
class PhaseError(RuntimeError):
    def __init__(self,message,classification='FAIL'):
        super().__init__(message);self.classification=classification
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def atomic(path,value):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
def terminate_group(proc,grace=2):
    try:os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    deadline=time.monotonic()+grace
    while time.monotonic()<deadline:
        try:os.killpg(proc.pid,0)
        except ProcessLookupError:break
        time.sleep(.02)
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    try:proc.wait(timeout=grace+1)
    except subprocess.TimeoutExpired:raise PhaseError('PROCESS_GROUP_NOT_REAPED','INCOMPLETE')

def command(argv,cwd,env,log,timeout):
    started=stamp();start=time.monotonic();timed=False;forced=False;log_exceeded=False
    with Path(log).open('xb') as stream:
        proc=subprocess.Popen(argv,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while proc.poll() is None:
                if time.monotonic()-start>=timeout or Path(log).stat().st_size>MAX_LOG:
                    timed=time.monotonic()-start>=timeout;log_exceeded=Path(log).stat().st_size>MAX_LOG;forced=True;terminate_group(proc);break
                time.sleep(.025)
        except BaseException:
            terminate_group(proc);raise
    code=proc.wait();return {'argv':argv,'started_at':started,'ended_at':stamp(),'elapsed_seconds':time.monotonic()-start,'exit_code':code if code>=0 else None,'signal':-code if code<0 else None,'timeout':timed,'forced':forced,'log_budget_exceeded':log_exceeded,'classification':'BLOCKED' if log_exceeded else 'PASS' if code==0 and not timed else 'INCOMPLETE' if timed else 'FAIL','log':str(log),'log_sha256':digest(log)}

class Recorder:
    def __init__(self,root,env):self.root=Path(root);self.env=env;self.receipts={}
    def phase(self,name,commands,cwd,action=None,outputs=(),timeout=120):
        start=time.monotonic();r={'phase':name,'started_at':stamp(),'ended_at':None,'elapsed_seconds':None,'command_sha256':hashlib.sha256(json.dumps({'phase':name,'commands':commands,'source_file_sha256':digest(action.__code__.co_filename) if action else digest(__file__)},sort_keys=True).encode()).hexdigest(),'exit_code':None,'signal':None,'resource_limit_status':'VERIFIED','classification':'INCOMPLETE','timeout':False,'validation_completed':False,'outputs':{},'log':str(self.root/(name+'.log')),'log_sha256':None,'commands':[]}
        log=Path(r['log']);log.write_text('')
        try:
            for i,argv in enumerate(commands):
                entry=command(argv,cwd,self.env,self.root/(name+f'.{i}.log'),timeout);r['commands'].append(entry)
                with log.open('a') as f:f.write(json.dumps(entry)+'\n')
                if entry['classification']!='PASS':
                    r.update(exit_code=entry['exit_code'],signal=entry['signal'],timeout=entry['timeout'],classification=entry['classification']);raise PhaseError(name,entry['classification'])
            if action:action()
            r.update(exit_code=0,classification='PASS',validation_completed=True)
        except Blocked as e:r.update(classification='BLOCKED',reason=str(e));raise
        except BaseException as e:
            if r['classification']=='INCOMPLETE':r['reason']=type(e).__name__
            raise
        finally:
            r['ended_at']=stamp();r['elapsed_seconds']=time.monotonic()-start;r['log_sha256']=digest(log)
            # Each phase binds its command receipt logs even if it produces no native artifact.
            r['outputs']={Path(x['log']).name:x['log_sha256'] for x in r['commands']}
            if not r['outputs']:r['outputs']={log.name:r['log_sha256']}
            for output in outputs:
                output=Path(output)
                if output.is_file():r['outputs'][str(output)]=digest(output)
                elif r['classification']=='PASS':r.update(classification='INCOMPLETE',reason='MISSING_OUTPUT_ARTIFACT')
            atomic(self.root/(name+'.receipt.json'),r);self.receipts[name]=r
        if r['classification']!='PASS':raise PhaseError('INCOMPLETE_PHASE_OUTPUT:'+name,'INCOMPLETE')
