"""Verify/extract pinned pure-Python modules without pip or executable hooks."""
import hashlib,json,pathlib,sys,zipfile

def unpack(root,dest):
    dest.mkdir(parents=True,exist_ok=True)
    for item in json.loads((root/'fixture-dependencies.json').read_text()):
        p=root/'inputs'/item['filename']
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('wheel integrity')
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                q=pathlib.PurePosixPath(n)
                if q.is_absolute() or '..' in q.parts or '\\' in n:raise ValueError('unsafe wheel member')
                if '.data' in n:raise ValueError('wheel install hooks prohibited')
            z.extractall(dest)
if __name__=='__main__':unpack(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
