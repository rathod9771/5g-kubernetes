#!/usr/bin/env bash
set -euo pipefail
mode=${1:?address, undefined or thread required}
case "$mode" in address|undefined|thread) ;; *) exit 2 ;; esac
input=$(cd -- "$(dirname -- "$0")" && pwd)
baseline="$GITHUB_WORKSPACE/ci/experimental/srsran-ngap"
source_dir="$RUNNER_TEMP/srsran-stage-c-$mode-source"
build_dir="$RUNNER_TEMP/srsran-stage-c-$mode-build"
results="$GITHUB_WORKSPACE/stage-c-results/$mode"
mkdir -p "$results"
exec > >(tee "$results/native.log") 2>&1
ulimit -c 0
finish() {
  code=$?
  trap - EXIT
  set +e
  printf '%s\n' "$code" > "$results/exit-code.txt"
  for name in CMakeCache.txt compile_commands.json; do
    if [ -f "$build_dir/$name" ]; then cp "$build_dir/$name" "$results/"; fi
  done
  if [ -d "$build_dir" ]; then
    find "$build_dir" -type f \( -name libsrsran_cu_cp.a -o -name cu_cp_test -o -name l3_ngap_snapshot_component -o -name l3_ngap_snapshot_concurrency \) -exec sha256sum {} + > "$results/binary-sha256.txt"
  fi
  if grep -Rq 'FATAL: ThreadSanitizer: unexpected memory mapping' "$results"; then
    printf 'UNSUPPORTED_RUNTIME: TSan address-space mapping; no retry or sysctl change\n' > "$results/runtime-status.txt"
  elif [ "$code" -eq 0 ]; then printf 'PASS\n' > "$results/runtime-status.txt"
  else printf 'FAILURE_OR_INCOMPLETE: inspect complete logs\n' > "$results/runtime-status.txt"; fi
  exit "$code"
}
trap finish EXIT
# Admission check on the remote runner only. No hard virtual-address limit (TSan requires large reservations).
python3 - "$results/resources.json" <<'PY'
import json,os,shutil,sys
from pathlib import Path
memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
free=shutil.disk_usage(os.environ['RUNNER_TEMP']).free
effective=memory
cgmax=Path('/sys/fs/cgroup/memory.max'); cgcurrent=Path('/sys/fs/cgroup/memory.current')
if cgmax.is_file() and cgcurrent.is_file() and cgmax.read_text().strip() != 'max':
 effective=min(memory,max(0,int(cgmax.read_text())-int(cgcurrent.read_text())))
record={'logical_cpus':os.cpu_count(),'available_memory_bytes':memory,'effective_memory_headroom_bytes':effective,'free_disk_bytes':free}
Path(sys.argv[1]).write_text(json.dumps(record,indent=2)+'\n')
if record['logical_cpus'] < 2 or effective < 5*1024**3 or free < 15*1024**3:
 raise SystemExit('INSUFFICIENT_RUNNER_RESOURCES')
PY
python3 - "$input" "$baseline" <<'PY'
import hashlib,json,sys
from pathlib import Path
p,b=map(Path,sys.argv[1:])
for name,h in json.loads((p/'preserved-b2-hashes.json').read_text()).items():
 assert hashlib.sha256((b/name).read_bytes()).hexdigest()==h,name
PY
{ c++ --version; cmake --version; ninja --version; uname -sm; } > "$results/toolchain.txt"
# Existing reviewed helper applies only to the fresh, exact-revision CI clone.
python3 "$baseline/prepare.py" "$source_dir"
(cd "$source_dir" && python3 -B -m unittest discover -s tests/offline_ngap_snapshot -v)
python3 -B -m unittest discover -s "$baseline" -p test_review_checks.py -v
printf '\nadd_subdirectory("%s" l3-stage-b-tests)\nadd_subdirectory("%s" l3-stage-c-tests)\n' "$baseline" "$input" >> "$source_dir/CMakeLists.txt"
asan=OFF; ubsan=OFF; tsan=OFF
case "$mode" in address) asan=ON ;; undefined) ubsan=ON ;; thread) tsan=ON ;; esac
timeout -k 15s 3m cmake -S "$source_dir" -B "$build_dir" -G Ninja \
 -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DENABLE_WERROR=ON \
 -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DCMAKE_GTEST_DISCOVER_TESTS_DISCOVERY_MODE=PRE_TEST \
 -DCMAKE_CXX_FLAGS='-fno-omit-frame-pointer -fno-sanitize-recover=all' \
 "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=$mode" \
 -DENABLE_UHD=OFF -DENABLE_ZEROMQ=OFF -DENABLE_MKL=OFF -DENABLE_ARMPL=OFF \
 -DENABLE_BACKWARD=OFF -DENABLE_PLUGINS=OFF -DENABLE_EXPORT=OFF \
 "-DENABLE_ASAN=$asan" "-DENABLE_UBSAN=$ubsan" "-DENABLE_TSAN=$tsan" \
 -DENABLE_UBSAN_MIN=OFF -DENABLE_RTSAN=OFF \
 -DL3_STAGE_B_INPUT="$baseline" -DL3_STAGE_C_INPUT="$input"
printf 'set(CTEST_CUSTOM_MAXIMUM_PASSED_TEST_OUTPUT_SIZE 1048576)\nset(CTEST_CUSTOM_MAXIMUM_FAILED_TEST_OUTPUT_SIZE 10485760)\n' > "$build_dir/CTestCustom.cmake"
ninja -C "$build_dir" -t commands srsran_cu_cp cu_cp_test l3_ngap_snapshot_component l3_ngap_snapshot_concurrency > "$results/build-commands.txt"
python3 "$input/verify_instrumentation.py" "$mode" "$build_dir/compile_commands.json" "$results/build-commands.txt" > "$results/instrumentation.json"
timeout -k 15s 75m nice -n 10 cmake --build "$build_dir" --parallel 2 \
 --target srsran_cu_cp cu_cp_test l3_ngap_snapshot_component l3_ngap_snapshot_concurrency
# Verify actual instrumented executable symbol references, not configuration alone.
for binary in tests/unittests/cu_cp/cu_cp_test l3-stage-b-tests/l3_ngap_snapshot_component l3-stage-c-tests/l3_ngap_snapshot_concurrency; do
  name=$(basename "$binary")
  nm -u "$build_dir/$binary" > "$results/$name.symbols.txt"
  case "$mode" in
    address) grep -q '__asan_init' "$results/$name.symbols.txt" ;;
    undefined) grep -q '__ubsan_handle_' "$results/$name.symbols.txt" ;;
    thread) grep -q '__tsan_init' "$results/$name.symbols.txt" ;;
  esac
done
case "$mode" in
 address) san_env="ASAN_OPTIONS=halt_on_error=1:abort_on_error=1:detect_leaks=1:log_path=$results/asan" ;;
 undefined) san_env="UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1:log_path=$results/ubsan" ;;
 thread) san_env="TSAN_OPTIONS=halt_on_error=1:exitcode=66:second_deadlock_stack=1:log_path=$results/tsan" ;;
esac
# Run only bounded offline tests. Drop privilege after namespace creation so diagnostics remain readable.
run_uid=$(id -u); run_gid=$(id -g)
run_isolated() {
  limit=$1; shift
  sudo -n unshare --net --setgid "$run_gid" --setuid "$run_uid" env "$san_env" timeout -k 5s "$limit" "$@"
}
# No fallback if namespace creation or sanitizers fail.
run_isolated 30s "$build_dir/tests/unittests/cu_cp/cu_cp_test" --gtest_filter='cu_cp_connectivity_test.*:cu_cp_setup_test.*' --gtest_list_tests > "$results/cu-cp-list.txt"
python3 "$baseline/verify_cu_cp_tests.py" listing "$results/cu-cp-list.txt"
run_isolated 240s "$build_dir/tests/unittests/cu_cp/cu_cp_test" --gtest_filter='cu_cp_connectivity_test.*:cu_cp_setup_test.*' --gtest_output="xml:$results/cu-cp.xml"
python3 "$baseline/verify_cu_cp_tests.py" results "$results/cu-cp.xml"
python3 - "$baseline" "$results" <<'PYCOUNTS'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from verify_cu_cp_tests import verify_results
assert verify_results((Path(sys.argv[2])/'cu-cp.xml').read_text()) == {'cu_cp_connectivity_test':18,'cu_cp_setup_test':5}
PYCOUNTS
run_isolated 180s ctest --test-dir "$build_dir" -R '^l3_ngap_snapshot_(component|concurrency)$' --verbose --output-on-failure --no-tests=error --output-junit "$results/snapshot.xml"
python3 "$input/verify_results.py" "$results"
