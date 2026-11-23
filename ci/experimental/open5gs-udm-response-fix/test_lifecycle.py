"""Compile the exact corrected handler with ownership-checking mocks, in CI only."""
import hashlib,json,pathlib,subprocess,sys
source=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);root=pathlib.Path('/fix')
d=pathlib.Path('/work/lifecycle');d.mkdir()
handler=(source/'src/udm/nnrf-handler.c').read_bytes()
assert hashlib.sha256(handler).hexdigest()==json.loads((root/'source-pins.json').read_text())['corrected_handler_sha256']
(d/'handler.c').write_bytes(handler)
(d/'sbi-path.h').write_bytes((root/'mock-api.h').read_bytes());(d/'nnrf-handler.h').write_text('')
for sanitizer in ('address','undefined'):
 binary=d/('lifecycle-'+sanitizer)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-g','-O1','-fno-omit-frame-pointer','-fsanitize='+sanitizer,'-I'+str(d),str(d/'handler.c'),str(root/'lifecycle.c'),'-o',str(binary)],check=True)
 with (out/('lifecycle-'+sanitizer+'.log')).open('wb') as f:
  subprocess.run(['timeout','-k','2','20',str(binary)],stdout=f,stderr=subprocess.STDOUT,check=True)
 assert (out/('lifecycle-'+sanitizer+'.log')).read_text().count(' PASS\n')==8
 (out/('lifecycle-'+sanitizer+'.sha256')).write_text(hashlib.sha256(binary.read_bytes()).hexdigest()+'\n')
