"""Bounded and fail-closed inspection candidate discovery. Never executes binaries."""
import os
import stat
from pathlib import Path
from build_policy import Blocked
from build_receipts import atomic

def preflight(candidates, work, output):
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
    atomic(output,{'classification':'REVIEW_REQUIRED' if bad else 'FORMATS_RECOGNIZED',
                   'native_build_qualified':False,'rejected_indices':bad,'candidates':rows})
    if bad:raise Blocked('BINARY_INSPECTION_UNRECOGNIZED_FORMAT:'+','.join(map(str,bad)))
    return rows
