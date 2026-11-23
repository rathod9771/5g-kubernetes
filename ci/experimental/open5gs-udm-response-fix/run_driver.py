"""Run preserved subscriber-free cases against freshly verified candidate libs."""
import json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'open5gs-udm-driver'))
import run_driver
out=pathlib.Path(sys.argv[1]);candidate=json.loads((out/'candidate.json').read_text())
run_driver.IMAGE=candidate['image_id']
loader=run_driver.docker('run','--rm','--network','none','--cpus','0.5','--memory','128m','--pids-limit','16','--cap-drop','ALL','--read-only','--security-opt','no-new-privileges','-v',str((out/'selection-driver').resolve())+':/driver:ro','--entrypoint','/usr/bin/ldd',run_driver.IMAGE,'-v','/driver')
(out/'driver-loader.txt').write_bytes(loader)
if any(x in loader for x in (b'not found',b'OPENSSL_3',b'libssl.so.3',b'libcrypto.so.3')):raise RuntimeError('driver loader mismatch')
sys.exit(run_driver.run(out))
