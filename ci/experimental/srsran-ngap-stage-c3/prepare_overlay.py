"""Apply the proposed clock diff ONLY to a fresh private test-header overlay."""
import hashlib,json,subprocess,sys
from pathlib import Path


def prepare(source, overlay):
    here=Path(__file__).resolve().parent
    manifest=json.loads((here/'source-manifest.json').read_text())
    source=Path(source).resolve(); overlay=Path(overlay).absolute()
    assert not overlay.exists() and not overlay.is_symlink(), 'Overlay destination must be unused'
    assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==manifest['upstream_revision']
    old=source/manifest['header_path']; original=old.read_bytes()
    sha=lambda data:hashlib.sha256(data).hexdigest()
    assert sha(original)==manifest['original_header_sha256'], 'Unreviewed original header'
    patch=here/'source-clock-seam.PROPOSED.patch'
    assert sha(patch.read_bytes())==manifest['proposed_patch_sha256']
    overlay.mkdir(mode=0o700,parents=False)
    header=overlay/manifest['header_path']; header.parent.mkdir(parents=True); header.write_bytes(original)
    subprocess.run(['git','apply','--check',str(patch)],cwd=overlay,check=True)
    subprocess.run(['git','apply',str(patch)],cwd=overlay,check=True)
    assert sha(header.read_bytes())==manifest['proposed_header_sha256']
    assert old.read_bytes()==original, 'Approved source header must remain untouched'
    return {'original_header_sha256':sha(original),'overlay_header_sha256':sha(header.read_bytes()),
            'source_revision':manifest['upstream_revision'],'original_source_modified':False}

if __name__=='__main__':
    print(json.dumps(prepare(sys.argv[1],sys.argv[2]),indent=2))
