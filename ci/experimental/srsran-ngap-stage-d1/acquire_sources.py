"""Explicit hosted-only public source acquisition. No configure or build operations."""
import json,os,subprocess,sys,time
from pathlib import Path
from build_policy import Blocked,digest,environment
from build_receipts import Recorder,atomic
from toolchain_lock import ROOTS,freeze

def main():
    if sys.argv[1:]!=['--github-acquire-only'] or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Blocked('HOSTED_ACQUISITION_ONLY')
    p=Path(__file__).resolve().parent;root=Path(os.environ['RUNNER_TEMP'])/'l3-d2a-acquired';root.mkdir(mode=0o700)
    home=root/'home';home.mkdir();tmp=root/'tmp';tmp.mkdir();env=environment(os.environ,home,tmp)
    rec=Recorder(root,env);pins=json.loads((p/'dependency-command-policy.json').read_text());records={}
    commands=[]
    for name,d in pins['sources'].items():
        dest=root/name
        commands += [['/usr/bin/git','init','-q',str(dest)],['/usr/bin/git','-C',str(dest),'-c','http.followRedirects=false','fetch','--depth','1',d['url'],d['revision']],['/usr/bin/git','-C',str(dest),'checkout','--detach','FETCH_HEAD']]
    def verify():
        for name,d in pins['sources'].items():
            dest=root/name
            if subprocess.check_output(['/usr/bin/git','-C',str(dest),'rev-parse','HEAD'],env=env,text=True).strip()!=d['revision']:raise Blocked('SOURCE_IDENTITY')
            archive=root/(name+'.tar')
            with archive.open('xb') as f:subprocess.run(['/usr/bin/git','-C',str(dest),'archive','--format=tar',d['revision']],env=env,stdout=f,check=True,timeout=60)
            if digest(archive)!=d['archive_sha256']:raise Blocked('SOURCE_ARCHIVE_HASH')
            records[name]={'revision':d['revision'],'archive_sha256':digest(archive)}
        atomic(root/'source-archives.json',records)
        versions=subprocess.check_output(['/usr/bin/dpkg-query','-W'],env=env,text=True,timeout=30)
        atomic(root/'toolchain-lock.json',freeze(ROOTS,versions))
    try:rec.phase('dependency_acquisition',commands,root,verify)
    finally:
        rp=root/'dependency_acquisition.receipt.json'
        if rp.exists():
            r=json.loads(rp.read_text());r['resource_limit_status']='NOT_APPLICABLE_ACQUISITION_ONLY';r['outputs']['source-archives.json']=digest(root/'source-archives.json') if (root/'source-archives.json').exists() else 'MISSING'
            r['outputs']['toolchain-lock.json']=digest(root/'toolchain-lock.json') if (root/'toolchain-lock.json').exists() else 'MISSING'
            rp.unlink();atomic(rp,r)
if __name__=='__main__':main()
