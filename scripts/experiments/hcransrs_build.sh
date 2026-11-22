#!/usr/bin/env bash
# Only a disposable GitHub-hosted runner. Never use the host installer.
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "${RUNNER_ENVIRONMENT:-}" = github-hosted
: "${RUNNER_TEMP:?}" "${GITHUB_WORKSPACE:?}" "${SRS_IMAGE:?}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="$RUNNER_TEMP/hcransrs-source"
OUT="$RUNNER_TEMP/hcransrs-build"
mkdir -p "$OUT"
test ! -e "$SOURCE"
readarray -t META < <(python3 - "$ROOT/config/reference-versions.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))["ran_images"]["components"]["srsran"]["source"]
assert m["repository"] == "https://github.com/herlesupreeth/srsRAN_Project.git"
assert m["commit"] == "11c9bbabb69873752500d676f55e0034f6caa5c5"
assert m["version"] == "25.04.0"
print(m["repository"])
print(m["commit"])
PY
)
test "${#META[@]}" -eq 2
git init -q "$SOURCE"
git -C "$SOURCE" remote add origin "${META[0]}"
git -C "$SOURCE" fetch --depth=1 origin "${META[1]}"
git -C "$SOURCE" checkout --detach FETCH_HEAD
test "$(git -C "$SOURCE" rev-parse HEAD)" = "${META[1]}"
test -z "$(git -C "$SOURCE" status --porcelain)"
printf '%s\n' "${META[0]}" "${META[1]}" > "$OUT/source-pin.txt"
docker pull ubuntu:24.04
BASE="$(docker image inspect ubuntu:24.04 --format '{{index .RepoDigests 0}}')"
[[ "$BASE" =~ ^(ubuntu|docker.io/library/ubuntu)@sha256:[0-9a-f]{64}$ ]]
printf '%s\n' "$BASE" > "$OUT/base-digest.txt"
# No host package install, service restart, containerd import, push, or deployment.
docker build --progress=plain --file "$ROOT/scripts/experiments/hcransrs_Dockerfile" \
  --build-arg "BASE=$BASE" --build-arg "SOURCE_COMMIT=${META[1]}" \
  --tag "$SRS_IMAGE" "$SOURCE" 2>&1 | tee "$OUT/build.log"
docker image inspect "$SRS_IMAGE" --format '{{.Id}}' > "$OUT/image-id.txt"
docker run --rm --network none --entrypoint /bin/sh "$SRS_IMAGE" -ec \
  'test -x /usr/local/bin/srscu && test -x /usr/local/bin/srsdu && cat /proof/binary-sha256.txt /proof/build-options.txt' \
  | tee "$OUT/binary-proof.txt"
docker run --rm --network none --entrypoint /bin/sh "$SRS_IMAGE" -ec \
  'sha256sum /usr/local/bin/srscu /usr/local/bin/srsdu && cat /proof/build-packages.txt /proof/runtime-packages.txt' \
  > "$OUT/package-and-runtime-proof.txt"
