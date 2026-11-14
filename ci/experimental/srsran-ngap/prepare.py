import hashlib,json,subprocess,sys
from pathlib import Path
p=Path(__file__).resolve().parent
s=Path(sys.argv[1]).resolve()
m=json.loads((p/'source-manifest.json').read_text())
def run(*a): return subprocess.check_output(a,text=True).strip()
assert run('git','-C',str(s),'rev-parse','HEAD')==m['source_revision']
assert not run('git','-C',str(s),'status','--porcelain','--untracked-files=all')
assert hashlib.sha256((p/'stage-a-complete.patch').read_bytes()).hexdigest()==m['patch_sha256']
subprocess.run(['git','-C',str(s),'apply','--check',str(p/'stage-a-complete.patch')],check=True)
subprocess.run(['git','-C',str(s),'apply',str(p/'stage-a-complete.patch')],check=True)
for name,h in m['files'].items():
 assert hashlib.sha256((s/name).read_bytes()).hexdigest()==h,name
print('Verified exact revision and all ten patched source/test hashes')
