"""Freeze installed public toolchain inputs during acquisition, reverify before offline configure.
No filesystem scan occurs on import. Runtime-resolved OS package versions are recorded, not claimed
as an immutable preapproved runner image or a bit-for-bit reproducibility guarantee.
"""
import hashlib,os,stat
from pathlib import Path
from build_policy import Blocked
MAX_FILES=200000;MAX_BYTES=24*1024**3
ROOTS=('/usr/include','/usr/lib','/usr/share','/usr/bin','/usr/sbin','/etc/alternatives','/etc/ld.so.cache','/etc/ld.so.conf','/etc/ld.so.conf.d')
def streaming_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()
def freeze(roots,package_versions):
    files={};total=0
    if not package_versions.strip():raise Blocked('MISSING_INSTALLED_PACKAGE_VERSION_LOCK')
    for root in map(Path,roots):
        if not root.exists():raise Blocked('MISSING_TOOLCHAIN_ROOT')
        entries=[root] if root.is_file() or root.is_symlink() else sorted(root.rglob('*'))
        for p in entries:
            if p.is_dir() and not p.is_symlink():continue
            if len(files)>=MAX_FILES:raise Blocked('TOOLCHAIN_FILE_BUDGET')
            meta=p.lstat()
            if stat.S_ISLNK(meta.st_mode):files[str(p)]={'symlink':os.readlink(p)}
            elif stat.S_ISREG(meta.st_mode):
                total+=meta.st_size
                if total>MAX_BYTES:raise Blocked('TOOLCHAIN_BYTE_BUDGET')
                files[str(p)]={'sha256':streaming_hash(p),'bytes':meta.st_size}
            else:raise Blocked('UNEXPECTED_TOOLCHAIN_OBJECT')
    if not files:raise Blocked('EMPTY_TOOLCHAIN_LOCK')
    return {'scope':'INSTALLED_PUBLIC_OS_INPUTS_FROZEN_AT_ACQUISITION','roots':list(map(str,roots)),'package_versions':package_versions,'files':files,'total_regular_bytes':total}
def verify(lock,versions):
    if not lock.get('files') or lock.get('package_versions')!=versions:raise Blocked('PACKAGE_VERSION_DRIFT')
    actual=set()
    for root in map(Path,lock['roots']):
        for p in ([root] if root.is_file() or root.is_symlink() else root.rglob('*')):
            if not p.is_dir() or p.is_symlink():actual.add(str(p))
    if actual!=set(lock['files']):raise Blocked('TOOLCHAIN_INPUT_SET_DRIFT')
    for name,item in lock['files'].items():
        p=Path(name)
        if 'symlink' in item:
            if not p.is_symlink() or os.readlink(p)!=item['symlink']:raise Blocked('TOOLCHAIN_SYMLINK_DRIFT')
        elif p.is_symlink() or not p.is_file() or p.stat().st_size!=item['bytes'] or streaming_hash(p)!=item['sha256']:raise Blocked('TOOLCHAIN_CONTENT_DRIFT')
    return True
