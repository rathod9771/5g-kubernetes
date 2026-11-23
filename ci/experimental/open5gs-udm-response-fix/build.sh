#!/bin/bash
set -euo pipefail
umask 022
unset TEST CC CXX CFLAGS CXXFLAGS LDFLAGS HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
mkdir -p /out/root/opt/open5gs/lib/x86_64-linux-gnu /out/root/opt/open5gs/bin /work/source
cp /package-versions.txt /apt-index-sha256.txt /package-sha256.txt /out/
{ cc --version; ld --version; meson --version; ninja --version; openssl version; } > /out/toolchain.txt
cd /ci
sha256sum -c SHA256SUMS
python3 - <<'PY'
import hashlib,json,pathlib,subprocess
r=pathlib.Path('/ci');archive=r/'inputs/source.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='2657f22b164c471379418adbabc78b94e626a0efca58ef7b1b4c824ababe8f2b'
subprocess.run(['tar','xzf',str(archive),'--strip-components=1','-C','/work/source'],check=True)
s=pathlib.Path('/work/source')
assert hashlib.sha256((s/'lib/sbi/context.c').read_bytes()).hexdigest()=='0b5207bde626ba5282187e3c14721c20def84c9a53d9ab31ee6eb0e6c8e7ee93'
for name,item in json.loads((r/'dependency-lock.json').read_text()).items():
 p=r/'inputs'/(name+'.tar.gz');assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
 d=s/'subprojects'/name;d.mkdir();subprocess.run(['tar','xzf',str(p),'--strip-components=1','-C',str(d)],check=True)
(s/'src/udm/regression.c').write_bytes((r/'regression.c').read_bytes())
with (s/'src/udm/meson.build').open('a') as f:f.write("\nexecutable('udr-selection-regression', sources: 'regression.c', c_args: sbi_cc_flags, include_directories: srcinc, dependencies: libudm_dep, install: false)\n")
PY
cd /work/source
python3 /fix/prepare.py
meson setup build --prefix=/opt/open5gs --libdir=lib/x86_64-linux-gnu --wrap-mode=nodownload -Dbuildtype=debugoptimized 2>&1 | tee /out/configure.log
ninja -C build -j2 -v src/udm/open5gs-udmd src/udm/udr-selection-regression src/udm/selection-driver 2>&1 | tee /out/compile-link.log
timeout -k 2s 20s build/src/udm/udr-selection-regression > /out/regression.log 2>&1
test "$(grep -c ' PASS$' /out/regression.log)" -eq 9
mkdir -p /work/http2
printf 'isolated-non-subscriber-control' > /work/http2/control.txt
nghttpd --no-tls -a 127.0.0.1 -d /work/http2 17777 > /out/http2-server.log 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
ready=0
for attempt in $(seq 1 20); do
 if curl --silent --show-error --max-time 1 --http2-prior-knowledge -o /out/http2-response.txt -w '%{http_version}' http://127.0.0.1:17777/control.txt > /out/http2-version.txt 2>/out/http2-curl.log; then ready=1; break; fi
 sleep 0.1
done
test "$ready" -eq 1
test "$(cat /out/http2-version.txt)" = 2
test "$(cat /out/http2-response.txt)" = isolated-non-subscriber-control
kill "$server"; wait "$server" || true
trap - EXIT
cp build/src/udm/open5gs-udmd /out/root/opt/open5gs/bin/
python3 /ci/collect_elf.py
# Stage SONAME symlinks for every built library; no unbuilt/original internal library overlay.
for f in /out/root/opt/open5gs/lib/x86_64-linux-gnu/*.so*; do
 soname=$(readelf -d "$f" | sed -n 's/.*(SONAME).*\[\(.*\)\].*/\1/p')
 if [ -n "$soname" ] && [ "$soname" != "$(basename "$f")" ]; then ln -sf "$(basename "$f")" "$(dirname "$f")/$soname"; fi
done
find /out/root -type f -exec patchelf --set-rpath /opt/open5gs/lib/x86_64-linux-gnu {} \;
for f in $(find /out/root -type f); do readelf -d -V "$f"; done > /out/elf-dependencies.txt
if grep -E 'libssl.so.3|libcrypto.so.3|OPENSSL_3' /out/elf-dependencies.txt; then exit 1; fi
find /out/root -type f -exec sha256sum {} + > /out/binary-sha256.txt
cp build/compile_commands.json /out/
printf 'PASS native compilation and 9 actual-library checks\n' > /out/native-result.txt

cp build/src/udm/selection-driver /out/selection-driver
sha256sum /out/selection-driver > /out/driver-sha256.txt
python3 /fix/test_lifecycle.py /work/source /out
