#!/usr/bin/env bash
# Disposable OAI 2026.w45 UE 3GPP TS24.501 cause-#9 recovery experiment.
# Does not touch HP, production, OSM, or real SIM/subscriber keys.
set -euo pipefail
: "${OAI_SOURCE:?}"
: "${OUTPUT:?}"
: "${IMAGE:?}"
: "${RUNNER_TEMP:?}"
: "${GITHUB_WORKSPACE:?}"

UPSTREAM=dfb296ab867050a7bde81b393fa31b757ada2c83
UPSTREAM_NAS_BLOB=60f009118ef1db9ba29f6f85075ffee0f73d459d
mkdir -p "$OUTPUT"
{ uname -a; free -h; df -h /; } > "$OUTPUT/runner-preflight.txt"
git clone --no-checkout --filter=blob:none \
    https://github.com/OPENAIRINTERFACE/openairinterface5g.git "$OAI_SOURCE"
git -C "$OAI_SOURCE" checkout --detach "$UPSTREAM"
test "$(git -C "$OAI_SOURCE" rev-parse HEAD)" = "$UPSTREAM"
FILE="$OAI_SOURCE/openair3/NAS/NR_UE/nr_nas_msg.c"
test "$(git -C "$OAI_SOURCE" hash-object openair3/NAS/NR_UE/nr_nas_msg.c)" = "$UPSTREAM_NAS_BLOB"
printf '%s\n' "$UPSTREAM" > "$OUTPUT/upstream-source-commit.txt"

python3 "$GITHUB_WORKSPACE/experiments/oai-ngap-2026w13/stage5g/patch_oai_ue_cause9.py" "$FILE"
# Stage5I experimental idle retry: no production certification or default change.
if [[ "${STAGE5I_REARM:-0}" == "1" ]]; then
  python3 "$GITHUB_WORKSPACE/experiments/oai-ngap-2026w13/stage5i/patch_ue_idle_rearm.py" "$FILE"
fi
git -C "$OAI_SOURCE" diff --check
test "$(git -C "$OAI_SOURCE" diff --name-only)" = "openair3/NAS/NR_UE/nr_nas_msg.c"
git -C "$OAI_SOURCE" diff --binary > "$OUTPUT/test-only-UE-NAS-cause9.patch"
sha256sum "$OUTPUT/test-only-UE-NAS-cause9.patch" > "$OUTPUT/source-patch-sha256.txt"

cd "$OAI_SOURCE/cmake_targets"
./build_oai -I -w SIMU > "$OUTPUT/install-deps.log" 2>&1 || {
    tail -n 90 "$OUTPUT/install-deps.log"
    exit 1
}
./build_oai --nrUE -w SIMU --noavx512 --noavx2 --build-tool-opt "-j2" \
   > "$OUTPUT/ue-build.log" 2>&1 || {
    tail -n 160 "$OUTPUT/ue-build.log"
    exit 1
}
BINARY="$OAI_SOURCE/cmake_targets/ran_build/build/nr-uesoftmodem"
test -x "$BINARY"
sha256sum "$BINARY" > "$OUTPUT/ue-executable.sha256"
CONTEXT="$RUNNER_TEMP/stage5g-ue-docker-build"
mkdir -p "$CONTEXT"
cp "$BINARY" "$CONTEXT/nr-uesoftmodem"
cat > "$CONTEXT/Dockerfile" <<'DOCKER'
FROM oaisoftwarealliance/oai-nr-ue:2026.w45
LABEL org.opencontainers.image.description="Stage5G disposable OAI UE 24.501 cause-9 re-registration experiment. NOT production."
COPY --chmod=0755 nr-uesoftmodem /opt/oai-nr-ue/bin/nr-uesoftmodem
DOCKER
docker build --pull -t "$IMAGE" "$CONTEXT" > "$OUTPUT/docker-build.log" 2>&1 || {
  tail -n 110 "$OUTPUT/docker-build.log"
  exit 1
}
docker run --rm --network none --entrypoint /bin/sh "$IMAGE" -ec '
  test -x /opt/oai-nr-ue/bin/nr-uesoftmodem
  ldd /opt/oai-nr-ue/bin/nr-uesoftmodem
' > "$OUTPUT/candidate-linkage.txt"
if grep -q 'not found' "$OUTPUT/candidate-linkage.txt"; then
  echo "Missing compiled candidate dependency" >&2
  exit 1
fi
docker image inspect "$IMAGE" --format '{{.Id}}' > "$OUTPUT/ue-test-candidate-id.txt"
printf '%s\n' "NO_GO: source candidate not authorized for production. Runtime recovery unverified." \
  > "$OUTPUT/production-decision.txt"
