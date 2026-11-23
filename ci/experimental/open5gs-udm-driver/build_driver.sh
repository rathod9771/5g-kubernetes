#!/bin/bash
# Only on GitHub; config/header generation and one driver TU. No library/image rebuild.
set -euo pipefail
test "${L3_DISPOSABLE_DRIVER_BUILD:-}" = approved
umask 022
mkdir -p /work/source /out
(cd /native-inputs && sha256sum -c SHA256SUMS) > /out/native-input-verification.txt
python3 - <<'PY'
import hashlib,json,pathlib,subprocess
r=pathlib.Path('/native-inputs');s=pathlib.Path('/work/source')
assert hashlib.sha256((r/'inputs/source.tar.gz').read_bytes()).hexdigest()=='2657f22b164c471379418adbabc78b94e626a0efca58ef7b1b4c824ababe8f2b'
subprocess.run(['tar','xzf',str(r/'inputs/source.tar.gz'),'--strip-components=1','-C',str(s)],check=True)
assert hashlib.sha256((s/'lib/sbi/context.c').read_bytes()).hexdigest()=='0b5207bde626ba5282187e3c14721c20def84c9a53d9ab31ee6eb0e6c8e7ee93'
for name,item in json.loads((r/'dependency-lock.json').read_text()).items():
 p=r/'inputs'/(name+'.tar.gz');assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
 d=s/'subprojects'/name;d.mkdir();subprocess.run(['tar','xzf',str(p),'--strip-components=1','-C',str(d)],check=True)
PY
# Identical pinned toolchain snapshot, not an unpinned replacement.
cmp /package-versions.txt /artifact/package-versions.txt
cp /package-versions.txt /out/driver-package-versions.txt
{ cc --version; ld --version; meson --version; } > /out/driver-toolchain.txt
cd /work/source
meson setup build --prefix=/opt/open5gs --libdir=lib/x86_64-linux-gnu --wrap-mode=nodownload -Dbuildtype=debugoptimized > /out/driver-configure.log 2>&1
cd build
# All project ABI declarations come from the approved patched source; only the
# isolated driver is compiled. Link solely the six already-built artifact libs.
set -x
cc -std=gnu11 -O2 -g -Wall -Wextra -Werror -Wno-sign-compare -DOGS_SBI_COMPILATION \
 -I. -I.. -Ilib -I../lib -Ilib/core -I../lib/core -Ilib/app -I../lib/app \
 -Ilib/sbi -I../lib/sbi -Ilib/crypt -I../lib/crypt -Ilib/proto -I../lib/proto \
 -Ilib/sbi/openapi -I../lib/sbi/openapi -I/usr/include/p11-kit-1 -pthread \
 /ci/selection_driver.c -o /out/selection-driver \
 -Wl,-rpath,/opt/open5gs/lib/x86_64-linux-gnu \
 -Wl,-rpath-link,/artifact/root/opt/open5gs/lib/x86_64-linux-gnu \
 /artifact/root/opt/open5gs/lib/x86_64-linux-gnu/libogssbi.so.2.7.2 \
 /artifact/root/opt/open5gs/lib/x86_64-linux-gnu/libogsapp.so.2.7.2 \
 /artifact/root/opt/open5gs/lib/x86_64-linux-gnu/libogssbi-openapi.so.2.7.2 \
 /artifact/root/opt/open5gs/lib/x86_64-linux-gnu/libogscrypt.so.2.7.2 \
 /artifact/root/opt/open5gs/lib/x86_64-linux-gnu/libogsproto.so.2.7.2 \
 /artifact/root/opt/open5gs/lib/x86_64-linux-gnu/libogscore.so.2.7.2
set +x
readelf -d -V /out/selection-driver > /out/driver-elf.txt
sha256sum /out/selection-driver > /out/driver-sha256.txt
