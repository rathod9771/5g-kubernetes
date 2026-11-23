"""Verify the original artifact before loading or executing any image."""
import hashlib, json, pathlib, sys
from docker_to_oci import convert

RUN = 45121234959
COMMIT = '217cbb274c5a86aa4a92054847992113b65df58b'
ARCHIVE = '8747a38bcc978ab3ccafa14c3a97989cc9b72914d9a7b8e8c1844dda2f19dee5'
IMAGE = 'sha256:9560fbf2a7e53f8976fcdecbce5d4e6a86cdbf74466f5b94d871ec4323c2dc12'
OCI = 'sha256:9735e0a099b5002658e0e8cf6348079894ea4bc3e6befbf70bef5710ce509bde'
SOURCE = '43fa4857cce8af6b6ec3c8e3b0cbf99444948f76'

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def verify(root, out, run):
    if (run.get('id'), run.get('head_sha'), run.get('conclusion'), run.get('status')) != (RUN, COMMIT, 'success', 'completed'):
        raise ValueError('native run provenance mismatch')
    pins = json.loads((pathlib.Path(__file__).parent/'artifact-pins.json').read_text())
    for name, expected in pins.items():
        p = root/name
        if not p.is_file() or p.is_symlink() or digest(p) != expected: raise ValueError('artifact pin mismatch: '+name)
    if digest(root/'experimental-image.docker.tar.gz') != ARCHIVE: raise ValueError('archive mismatch')
    info = json.loads((root/'experimental-image.json').read_text())
    if len(info) != 1 or info[0]['Id'] != IMAGE or info[0]['Config']['Labels']['org.opencontainers.image.revision'] != SOURCE:
        raise ValueError('image provenance mismatch')
    result = convert(root/'experimental-image.docker.tar.gz', out/'verified.oci')
    if result['oci_manifest_digest'] != OCI or result['config_digest'] != IMAGE: raise ValueError('OCI identity mismatch')
    (out/'artifact-verification.json').write_text(json.dumps({'status':'PASS','native_run':RUN,'source':SOURCE,'archive_sha256':ARCHIVE,**result},indent=2)+'\n')

if __name__ == '__main__':
    verify(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), json.loads(pathlib.Path(sys.argv[3]).read_text()))
