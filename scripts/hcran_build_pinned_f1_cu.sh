#!/usr/bin/env bash
# Build the pinned OAI 2026.w13 CU + telnet/CI modules for disposable handover experiments.
# NOT a production build or authorization. No HP cluster, OSM or subscriber access.
set -euo pipefail

: "${OAI_SOURCE:?set OAI_SOURCE}"
: "${OUTPUT:?set OUTPUT}"
: "${IMAGE:?set IMAGE}"
: "${GITHUB_WORKSPACE:?set GITHUB_WORKSPACE}"
: "${RUNNER_TEMP:?set RUNNER_TEMP}"

UPSTREAM=4941f44043e6dcec81b2041e83dbae4bee6f737d
mkdir -p "$OUTPUT"
{ uname -a; free -h; df -h /; } > "$OUTPUT/runner.txt"

git clone --no-checkout --filter=blob:none \
  https://github.com/OPENAIRINTERFACE/openairinterface5g.git "$OAI_SOURCE"
cd "$OAI_SOURCE"
git checkout --detach "$UPSTREAM"
test "$(git rev-parse HEAD)" = "$UPSTREAM"
git rev-parse HEAD > "$OUTPUT/upstream-source-commit.txt"
test "$(git hash-object openair2/RRC/NR/rrc_gNB_mobility.c)" = \
  3c59cdfc1660d3381506768e728897b35bfabe1d
git apply --check "$GITHUB_WORKSPACE/experiments/oai-ngap-2026w13/ngap-retry-v2.patch"
git apply "$GITHUB_WORKSPACE/experiments/oai-ngap-2026w13/ngap-retry-v2.patch"
# Stage5I opt-in: retain Stage4Z normal builder behavior on all other runs.
if [[ "${STAGE5I_NO_AMF_RELEASE:-0}" == "1" ]]; then
  python3 "$GITHUB_WORKSPACE/experiments/oai-ngap-2026w13/stage5i/patch_no_amf_release.py" "$OAI_SOURCE/openair3/NGAP/ngap_gNB.c"
fi
git diff --check
test "$(git diff --name-only)" = "openair3/NGAP/ngap_gNB.c"
git diff --binary > "$OUTPUT/applied-ngap-patch.diff"
sha256sum "$OUTPUT/applied-ngap-patch.diff" > "$OUTPUT/ngap-patch-sha256.txt"

cd "$OAI_SOURCE/cmake_targets"
./build_oai -I -w SIMU > "$OUTPUT/build-dependencies.log" 2>&1 || {
  tail -n 120 "$OUTPUT/build-dependencies.log"
  exit 1
}
./build_oai --gNB -w SIMU --noavx512 --noavx2 --build-tool-opt "-j2" \
  > "$OUTPUT/gnb-build.log" 2>&1 || {
  tail -n 160 "$OUTPUT/gnb-build.log"
  exit 1
}
./build_oai --build-lib telnetsrv --build-tool-opt "-j2" \
  > "$OUTPUT/telnetsrv-build.log" 2>&1 || {
  tail -n 160 "$OUTPUT/telnetsrv-build.log"
  exit 1
}

BUILD="$OAI_SOURCE/cmake_targets/ran_build/build"
test -x "$BUILD/nr-softmodem"
test -f "$BUILD/libtelnetsrv.so"
test -f "$BUILD/libtelnetsrv_ci.so"
sha256sum "$BUILD/nr-softmodem" "$BUILD"/libtelnetsrv*.so > "$OUTPUT/binary-sha256.txt"

CONTEXT="$RUNNER_TEMP/hcran-f1-docker-context"
mkdir -p "$CONTEXT/lib"
cp "$BUILD/nr-softmodem" "$CONTEXT/nr-softmodem"
cp "$BUILD"/libtelnetsrv*.so "$CONTEXT/lib/"
cat > "$CONTEXT/Dockerfile" <<'DOCKER'
FROM oaisoftwarealliance/oai-gnb:2026.w13
LABEL org.opencontainers.image.description="Isolated H-CRAN F1 mobility test only"
COPY --chmod=0755 nr-softmodem /opt/oai-gnb/bin/nr-softmodem
COPY lib/ /opt/oai-gnb/lib/
ENV LD_LIBRARY_PATH="/opt/oai-gnb/lib:/opt/oai-gnb/lib64:/usr/local/lib"
DOCKER

docker build --pull -t "$IMAGE" "$CONTEXT" > "$OUTPUT/docker-build.log" 2>&1 || {
  tail -n 100 "$OUTPUT/docker-build.log"
  exit 1
}
docker run --rm --network none --entrypoint /bin/sh "$IMAGE" -ec '
  set -e
  test -x /opt/oai-gnb/bin/nr-softmodem
  test -f /opt/oai-gnb/lib/libtelnetsrv.so
  test -f /opt/oai-gnb/lib/libtelnetsrv_ci.so
  ldd /opt/oai-gnb/bin/nr-softmodem
  ldd /opt/oai-gnb/lib/libtelnetsrv.so
  ldd /opt/oai-gnb/lib/libtelnetsrv_ci.so
' > "$OUTPUT/image-linkage.txt"
if grep -q 'not found' "$OUTPUT/image-linkage.txt"; then
  echo "Missing dynamic library dependency in compiled candidate" >&2
  exit 1
fi
docker image inspect "$IMAGE" --format '{{.Id}}' > "$OUTPUT/candidate-image-id.txt"
echo "NO_GO: patched CU image has not passed real F1 mobility tests" \
  > "$OUTPUT/qualification.txt"
mkdir -p "$RUNNER_TEMP/hcran-f1-candidate-archive"
docker save "$IMAGE" | gzip -1 \
  > "$RUNNER_TEMP/hcran-f1-candidate-archive/hcran-f1-patched-telnet.tar.gz"
gzip -t "$RUNNER_TEMP/hcran-f1-candidate-archive/hcran-f1-patched-telnet.tar.gz"
sha256sum "$RUNNER_TEMP/hcran-f1-candidate-archive/hcran-f1-patched-telnet.tar.gz" \
  > "$OUTPUT/candidate-image-archive.sha256"
