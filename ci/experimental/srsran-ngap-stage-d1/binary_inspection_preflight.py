"""Bounded and fail-closed inspection candidate discovery. Never executes binaries."""
import os
import re
import stat
from pathlib import Path
from build_policy import Blocked
from build_receipts import atomic


# Exact linker-output basenames. A Meson *.so.symbols sidecar is a TEXT
# metadata file even when rglob('*.so*') happens to match it.
_SHARED_NAME = re.compile(r'[A-Za-z0-9_.+-]+\.so(?:\.[0-9]+)*\Z')
_SYMBOL_SIDECAR = re.compile(r'(?P<soname>[A-Za-z0-9_.+-]+\.so(?:\.[0-9]+)*)\.symbols\Z')

def select_shared_libraries(build_root):
    """Discover actual shared objects; reject unexpected *.so* file types.

    Known Meson <soname>.p/<soname>.symbols metadata is not a library.
    All actual *.so[.version] candidates still undergo ELF validation.
    """
    root = Path(build_root)
    libraries = []
    ignored_sidecars = []
    for path in root.rglob('*.so*'):
        if path.is_symlink() or not path.is_file():
            continue
        if _SHARED_NAME.fullmatch(path.name):
            libraries.append(path)
            continue
        match = _SYMBOL_SIDECAR.fullmatch(path.name)
        if match is None or path.parent.name != match.group('soname') + '.p':
            raise Blocked('UNREVIEWED_SHARED_LIBRARY_CANDIDATE:' + path.name)
        ignored_sidecars.append(path)
    if len(libraries) + len(ignored_sidecars) > 512:
        raise Blocked('SHARED_LIBRARY_DISCOVERY_BUDGET')
    return libraries, ignored_sidecars

def preflight(candidates, work, output, ignored_sidecars=()):
    if not isinstance(candidates, list) or not 5 <= len(candidates) <= 517:
        raise Blocked('BINARY_INSPECTION_CANDIDATE_COUNT')
    work=Path(work)
    rows=[]
    bad=[]
    for idx,raw in enumerate(candidates):
        p=Path(raw)
        try: name=p.relative_to(work).as_posix()
        except ValueError as e: raise Blocked('BINARY_INSPECTION_OUTSIDE_WORKSPACE') from e
        if len(name)>320 or '..' in Path(name).parts: raise Blocked('BINARY_INSPECTION_UNSAFE_PATH')
        st=p.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size<1:
            raise Blocked('BINARY_INSPECTION_UNSAFE_FILE')
        flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK
        fd=os.open(p,flags)
        try:
            if os.fstat(fd).st_ino!=st.st_ino: raise Blocked('BINARY_INSPECTION_RACED_FILE')
            head=os.read(fd,8)
        finally: os.close(fd)
        kind=('ELF' if head.startswith(b'\x7fELF') else
              'AR' if head==b'!<arch>\n' else
              'THIN_AR' if head==b'!<thin>\n' else 'UNRECOGNIZED')
        expected='ELF' if idx<5 or '.so' in p.name else 'ARCHIVE' if p.name.endswith('.a') else 'UNREVIEWED'
        ok=(kind=='ELF' if expected=='ELF' else kind in ('AR','THIN_AR') if expected=='ARCHIVE' else False)
        rows.append({'index':idx,'path':name,'size':st.st_size,'header_hex':head.hex(),
                     'format':kind,'expected':expected,'recognized':ok})
        if not ok: bad.append(idx)
    if len(ignored_sidecars)>512:
        raise Blocked('MESON_SIDECAR_BUDGET')
    sidecars=[]
    for path in ignored_sidecars:
        file=Path(path)
        try: relative=file.relative_to(work).as_posix()
        except ValueError as error: raise Blocked('MESON_SIDECAR_OUTSIDE_WORKSPACE') from error
        match=_SYMBOL_SIDECAR.fullmatch(file.name)
        if match is None or file.parent.name!=match.group('soname')+'.p':
            raise Blocked('MESON_SIDECAR_NOT_REVIEWED')
        sidecars.append(relative)
    if len(set(sidecars))!=len(sidecars):
        raise Blocked('MESON_SIDECAR_DUPLICATE')
    atomic(output,{'classification':'REVIEW_REQUIRED' if bad else 'FORMATS_RECOGNIZED',
                   'native_build_qualified':False,'rejected_indices':bad,
                   'ignored_meson_symbol_sidecars':sidecars,'candidates':rows})
    if bad:raise Blocked('BINARY_INSPECTION_UNRECOGNIZED_FORMAT:'+','.join(map(str,bad)))
    return rows
