#!/usr/bin/env bash
set -euo pipefail
input=$(cd -- "$(dirname -- "$0")" && pwd)
source_dir="$RUNNER_TEMP/srsran-source"
build_dir="$RUNNER_TEMP/srsran-build"
mkdir -p "$GITHUB_WORKSPACE/stage-b-results"
exec > >(tee "$GITHUB_WORKSPACE/stage-b-results/native.log") 2>&1
trap 'rc=$?; printf "%s\n" "$rc" > "$GITHUB_WORKSPACE/stage-b-results/exit-code.txt"' EXIT
{ c++ --version; cmake --version; ninja --version; uname -sm; dpkg-query -W build-essential cmake ninja-build libmbedtls-dev libsctp-dev libyaml-cpp-dev libfftw3-dev libgtest-dev; } > "$GITHUB_WORKSPACE/stage-b-results/toolchain.txt"
python3 "$input/prepare.py" "$source_dir"
(cd "$source_dir" && python3 -B -m unittest discover -s tests/offline_ngap_snapshot -v)
# CI-only test wiring, after all reviewed source hashes were verified.
printf '\nadd_subdirectory("%s" l3-stage-b-tests)\n' "$input" >> "$source_dir/CMakeLists.txt"
cmake -S "$source_dir" -B "$build_dir" -G Ninja \
 -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DENABLE_WERROR=ON \
 -DENABLE_UHD=OFF -DENABLE_ZEROMQ=OFF -DENABLE_MKL=OFF -DENABLE_ARMPL=OFF \
 -DENABLE_BACKWARD=OFF -DENABLE_PLUGINS=OFF -DENABLE_EXPORT=OFF \
 -DENABLE_ASAN=OFF -DENABLE_TSAN=OFF -DENABLE_UBSAN=OFF \
 -DL3_STAGE_B_INPUT="$input"
timeout -k 15s 75m nice -n 10 cmake --build "$build_dir" --parallel 2 \
 --target srsran_cu_cp cu_cp_test l3_ngap_snapshot_component
# Native CU-CP tests use in-memory AMF/DU doubles; run only selected suites.
sudo unshare --net timeout -k 5s 120s "$build_dir/tests/unittests/cu_cp/cu_cp_test" --gtest_filter='cu_cp_connectivity_test.*:cu_cp_setup_test.*' --gtest_output="xml:$GITHUB_WORKSPACE/stage-b-results/cu-cp.xml"
sudo unshare --net timeout -k 5s 60s ctest --test-dir "$build_dir" -R '^l3_ngap_snapshot_component$' --output-on-failure --no-tests=error --output-junit "$GITHUB_WORKSPACE/stage-b-results/snapshot.xml"
python3 - "$GITHUB_WORKSPACE/stage-b-results/cu-cp.xml" <<'CHECK'
import sys,xml.etree.ElementTree as E
root=E.parse(sys.argv[1]).getroot()
assert int(root.get('tests','0'))>0, 'No CU-CP tests executed'
assert int(root.get('failures','0'))==0 and int(root.get('errors','0'))==0
CHECK
cp "$build_dir/CMakeCache.txt" "$GITHUB_WORKSPACE/stage-b-results/"
find "$build_dir" -type f \( -name libsrsran_cu_cp.a -o -name l3_ngap_snapshot_component -o -name cu_cp_test \) -exec sha256sum {} + > "$GITHUB_WORKSPACE/stage-b-results/build-sha256.txt"
