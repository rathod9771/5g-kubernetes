#!/usr/bin/env bash
set -euo pipefail
mode=${1:?address, undefined or thread required}
case "$mode" in address|undefined|thread) ;; *) exit 2 ;; esac
input=$(cd -- "$(dirname -- "$0")" && pwd)
source_dir="$RUNNER_TEMP/srsran-stage-c-$mode-source"
build_dir="$RUNNER_TEMP/srsran-stage-c-$mode-build"
overlay="$RUNNER_TEMP/srsran-stage-c3-$mode-overlay"
results="$GITHUB_WORKSPACE/stage-c3-results/$mode"
mkdir -p "$results"
exec > >(tee "$results/native.log") 2>&1
ulimit -c 0
finish() {
  code=$?; trap - EXIT; set +e
  printf '%s\n' "$code" > "$results/exit-code.txt"
  for name in CMakeCache.txt compile_commands.json; do
    if [ -f "$build_dir/$name" ]; then cp "$build_dir/$name" "$results/"; fi
  done
  find "$build_dir" -type f \( -name libsrsran_cu_cp.a -o -name cu_cp_test -o -name l3_ngap_snapshot_component -o -name l3_ngap_snapshot_concurrency -o -name l3_ngap_snapshot_freshness \) -exec sha256sum {} + > "$results/binary-sha256.txt"
  if grep -Rq 'FATAL: ThreadSanitizer: unexpected memory mapping' "$results"; then
    printf 'INCONCLUSIVE: TSan runtime mapping failure; no retry\n' > "$results/runtime-status.txt"
  elif [ "$code" -eq 0 ]; then printf 'PASS\n' > "$results/runtime-status.txt"
  else printf 'FAILURE_OR_INCOMPLETE: inspect diagnostics\n' > "$results/runtime-status.txt"; fi
  exit "$code"
}
trap finish EXIT
# Require the unmodified Stage C2 runner to have actually passed all 53 cases first.
test "$(cat "$GITHUB_WORKSPACE/stage-c-results/$mode/runtime-status.txt")" = PASS
python3 - "$input" "$source_dir" <<'PRESERVE'
import hashlib,json,sys
from pathlib import Path
p,source=map(Path,sys.argv[1:]);repo=p.parents[2]
for name,h in json.loads((p/'preserved-c2-hashes.json').read_text()).items():
    assert hashlib.sha256((repo/name).read_bytes()).hexdigest()==h,name
m=json.loads((p.parent/'srsran-ngap/source-manifest.json').read_text())
for name,h in m['files'].items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==h,name
print('All ten Stage A files and all Stage C2 inputs unchanged')
PRESERVE
python3 "$input/prepare_overlay.py" "$source_dir" "$overlay" > "$results/source-overlay.json"
printf '\nadd_subdirectory("%s" l3-stage-c3-tests)\n' "$input" >> "$source_dir/CMakeLists.txt"
timeout -k 15s 3m cmake -S "$source_dir" -B "$build_dir" \
 -DL3_STAGE_C3_INPUT="$input" -DL3_STAGE_C3_OVERLAY="$overlay"
ninja -C "$build_dir" -t commands l3_ngap_snapshot_freshness > "$results/build-commands.txt"
python3 "$input/verify_isolation.py" "$mode" "$build_dir" "$overlay" "$results/build-commands.txt" "$source_dir" > "$results/compile-isolation.json"
timeout -k 15s 5m nice -n 10 cmake --build "$build_dir" --parallel 2 --target l3_ngap_snapshot_freshness
# Prove the standalone TU used only the overlay header, and linked support objects used neither variant.
python3 - "$input" "$build_dir" "$results" "$overlay" "$mode" <<'PY'
import json,subprocess,sys
from pathlib import Path
p,build,out,overlay=map(Path,sys.argv[1:5]);mode=sys.argv[5];sys.path.insert(0,str(p))
from verify_isolation import verify_deps,verify_symbols
meta=json.loads((out/'compile-isolation.json').read_text())
proc=subprocess.run(['ninja','-C',str(build),'-t','deps',meta['freshness_object']]+meta['support_objects'],capture_output=True,text=True,timeout=30,check=True)
assert len(proc.stdout.encode())<=64*1024**2,'Dependency evidence exceeded 64 MiB'
(out/'linked-header-dependencies.txt').write_text(proc.stdout)
report=verify_deps(proc.stdout,meta['freshness_object'],overlay/'lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h',meta['support_objects'])
normal={}
for target in ['lib/cu_cp/libsrsran_cu_cp.a','lib/support/libsrsran_support.a','lib/srslog/libsrslog.a','external/fmt/libfmt.a','tests/unittests/cu_cp/cu_cp_test','l3-stage-b-tests/l3_ngap_snapshot_component','l3-stage-c-tests/l3_ngap_snapshot_concurrency']:
    text=subprocess.check_output(['nm','-C',str(build/target)],text=True,timeout=30)
    normal[target]=text
    (out/(Path(target).name+'.symbols.txt')).write_text(text)
fresh=subprocess.check_output(['nm','-C',str(build/'l3-stage-c3-tests/l3_ngap_snapshot_freshness')],text=True,timeout=30)
(out/'freshness.symbols.txt').write_text(fresh)
report.update(verify_symbols(fresh,normal))
assert {'address':'__asan_init','undefined':'__ubsan_handle_','thread':'__tsan_init'}[mode] in fresh
(out/'link-isolation.json').write_text(json.dumps(report,indent=2)+'\n')
PY
case "$mode" in
 address) san_env="ASAN_OPTIONS=halt_on_error=1:abort_on_error=1:detect_leaks=1:log_path=$results/asan" ;;
 undefined) san_env="UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1:log_path=$results/ubsan" ;;
 thread) san_env="TSAN_OPTIONS=halt_on_error=1:exitcode=66:second_deadlock_stack=1:log_path=$results/tsan" ;;
esac
run_uid=$(id -u); run_gid=$(id -g)
sudo -n unshare --net --setgid "$run_gid" --setuid "$run_uid" env "$san_env" \
 timeout -k 5s 60s ctest --test-dir "$build_dir" -R '^l3_ngap_snapshot_freshness$' \
 --verbose --output-on-failure --no-tests=error --output-junit "$results/freshness.xml"
python3 "$input/verify_results.py" "$results"
python3 - "$input" "$source_dir" <<'PRESERVE'
import hashlib,json,sys
from pathlib import Path
p,source=map(Path,sys.argv[1:]);repo=p.parents[2]
for name,h in json.loads((p/'preserved-c2-hashes.json').read_text()).items():
    assert hashlib.sha256((repo/name).read_bytes()).hexdigest()==h,name
m=json.loads((p.parent/'srsran-ngap/source-manifest.json').read_text())
for name,h in m['files'].items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==h,name
print('All ten Stage A files and all Stage C2 inputs unchanged')
PRESERVE
