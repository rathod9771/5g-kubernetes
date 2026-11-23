import pathlib,re,shutil,hashlib,sys

def collect(roots,destination):
    inventory={}
    for root in roots:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink() or not re.fullmatch(r'[^/]+\.so(?:\.[0-9]+)*',path.name):continue
            with path.open('rb') as stream:
                if stream.read(4)!=b'\x7fELF':raise ValueError('selected library is not ELF: '+path.name)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in inventory and inventory[path.name]!=digest:raise ValueError('library name collision')
            inventory[path.name]=digest
            shutil.copyfile(path,destination/path.name)
    if not inventory:raise ValueError('empty library inventory')
    return inventory
if __name__=='__main__':
    collect([pathlib.Path('/work/source/build/lib'),pathlib.Path('/work/source/build/subprojects')],pathlib.Path('/out/root/opt/open5gs/lib/x86_64-linux-gnu'))
