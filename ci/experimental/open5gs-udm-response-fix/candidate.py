"""Record and bind NEW image provenance; old artifact is never overwritten."""
import hashlib,json,pathlib,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'open5gs-udm-runtime'))
from docker_to_oci import convert
out=pathlib.Path(sys.argv[1])
x=json.loads(subprocess.check_output(['docker','image','inspect','l3-open5gs-udm:2.7.2-guard.2']))[0]
old='sha256:9560fbf2a7e53f8976fcdecbce5d4e6a86cdbf74466f5b94d871ec4323c2dc12'
assert x['Id']!=old
receipt=convert(out/'experimental-image.docker.tar.gz',out/'oci')
assert receipt['config_digest']==x['Id']
assert receipt['oci_manifest_digest']!='sha256:9735e0a099b5002658e0e8cf6348079894ea4bc3e6befbf70bef5710ce509bde'
root=pathlib.Path(__file__).parent
result={**receipt,'image_id':x['Id'],'version':'2.7.2-guard.2','source':json.loads((root/'source-pins.json').read_text()),'patch_sha256':hashlib.sha256((root/'upstream-response.patch').read_bytes()).hexdigest(),'repository_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'archive_sha256':hashlib.sha256((out/'experimental-image.docker.tar.gz').read_bytes()).hexdigest(),'original_image_preserved':old}
(out/'candidate.json').write_text(json.dumps(result,indent=2)+'\n')
