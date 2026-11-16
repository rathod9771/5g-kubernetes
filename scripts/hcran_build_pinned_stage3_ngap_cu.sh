#!/usr/bin/env bash
# Stage5Q: reproduce the historical Stage3 OAI 2026.w13 NGAP-patched CU from
# source on a disposable GitHub-hosted RFsim runner. NO deploy, NO registry push.
set -euo pipefail
: "${OAI_SOURCE:?need pinned OAI source destination}"
: "${OUTPUT:?need evidence output}"
: "${IMAGE:?need isolated CU image tag}"
: "${RUNNER_TEMP:?}"
: "${GITHUB_WORKSPACE:?}"
UPSTREAM=4941f44043e6dcec81b2041e83dbae4bee6f737d
NGAP_BLOB=da4108df125218f3470940389fdd4cfa496bd81e
PATCH="$GITHUB_WORKSPACE/experiments/oai-ngap-2026w13/ngap-retry-v2.patch"
test -f "$PATCH"
mkdir -p "$OUTPUT"
{ uname -a; free -h; df -h /; } > "$OUTPUT/preflight.txt"
git clone --no-checkout --filter=blob:none \
  https://github.com/OPENAIRINTERFACE/openairinterface5g.git "$OAI_SOURCE"
git -C "$OAI_SOURCE" checkout --detach "$UPSTREAM"
test "$(git -C "$OAI_SOURCE" rev-parse HEAD)" = "$UPSTREAM"
test "$(git -C "$OAI_SOURCE" hash-object openair3/NGAP/ngap_gNB.c)" = "$NGAP_BLOB"
printf '%s\n' "$UPSTREAM" > "$OUTPUT/source-commit.txt"
sha256sum "$PATCH" > "$OUTPUT/ngap-source-patch.sha256"
git -C "$OAI_SOURCE" apply --check "$PATCH"
git -C "$OAI_SOURCE" apply "$PATCH"
git -C "$OAI_SOURCE" diff --check
test "$(git -C "$OAI_SOURCE" diff --name-only)" = "openair3/NGAP/ngap_gNB.c"
git -C "$OAI_SOURCE" diff --binary > "$OUTPUT/applied-ngap-patch.diff"
cd "$OAI_SOURCE/cmake_targets"
./build_oai -I -w SIMU > "$OUTPUT/install-deps.log" 2>&1 || {
  tail -n 90 "$OUTPUT/install-deps.log"
  exit 1
}
./build_oai --gNB -w SIMU --noavx512 --noavx2 --build-tool-opt "-j2" \
  > "$OUTPUT/gnb-build.log" 2>&1 || {
    tail -n 180 "$OUTPUT/gnb-build.log"
    exit 1
  }
BIN="$OAI_SOURCE/cmake_targets/ran_build/build/nr-softmodem"
test -x "$BIN"
sha256sum "$BIN" > "$OUTPUT/cu-binary.sha256"
CONTEXT="$RUNNER_TEMP/stage5q-stage3-cu-build-context"
mkdir -p "$CONTEXT"
cp "$BIN" "$CONTEXT/nr-softmodem"
cat > "$CONTEXT/Dockerfile" <<'DOCKER'
FROM oaisoftwarealliance/oai-gnb:2026.w13
LABEL org.opencontainers.image.description="Stage5Q isolated 2026.w13 Stage3-NGAP source-pinned CU, NO DEPLOY"
COPY --chmod=0755 nr-softmodem /opt/oai-gnb/bin/nr-softmodem
DOCKER
docker build --pull -t "$IMAGE" "$CONTEXT" > "$OUTPUT/docker-build.log" 2>&1 || {
  tail -n 100 "$OUTPUT/docker-build.log"
  exit 1
}
docker run --rm --network none --entrypoint /bin/sh "$IMAGE" -ec \
  'test -x /opt/oai-gnb/bin/nr-softmodem; ldd /opt/oai-gnb/bin/nr-softmodem' \
  > "$OUTPUT/cu-linkage.txt"
if grep -q 'not found' "$OUTPUT/cu-linkage.txt"; then
  echo "FAIL CLOSED: CU missing dynamic linker dependency" >&2
  exit 1
fi
docker image inspect "$IMAGE" --format '{{.Id}}' > "$OUTPUT/cu-image-id.txt"
echo "NO_GO: source-pinned Stage3-like CU rebuild for Stage5Q only; not bit-identical historical archive" \
  > "$OUTPUT/provenance-decision.txt"
