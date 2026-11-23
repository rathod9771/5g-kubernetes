"""Apply exactly one pinned caller correction to disposable guarded source."""
import hashlib,json,pathlib,subprocess
s=pathlib.Path('/work/source');r=pathlib.Path('/fix')
p=json.loads((r/'source-pins.json').read_text())
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
assert sha(s/'src/udm/nnrf-handler.c')==p['original_handler_sha256']
assert sha(s/'lib/sbi/context.c')==p['guard_context_sha256']
subprocess.run(['git','apply','--check',str(r/'upstream-response.patch')],cwd=s,check=True)
subprocess.run(['git','apply',str(r/'upstream-response.patch')],cwd=s,check=True)
assert sha(s/'src/udm/nnrf-handler.c')==p['corrected_handler_sha256']
assert sha(s/'lib/sbi/context.c')==p['guard_context_sha256']
(s/'src/udm/selection_driver.c').write_bytes(pathlib.Path('/driver/selection_driver.c').read_bytes())
with (s/'src/udm/meson.build').open('a') as f:
 f.write("\nexecutable('selection-driver', sources: 'selection_driver.c', c_args: sbi_cc_flags, include_directories: srcinc, dependencies: libudm_dep, install: false)\n")
