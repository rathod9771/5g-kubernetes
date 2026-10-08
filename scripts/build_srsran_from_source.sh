#!/usr/bin/env bash
# Build the supported srsRAN runtime directly from the pinned upstream source revision.
# This is the fresh-machine fallback when no approved legacy export is supplied.
set -euo pipefail
case $- in *x*) set +x ;; esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY="$REPO_ROOT/config/reference-versions.json"

readarray -t META < <(python3 - "$POLICY" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))['ran_images']['components']['srsran']
s = p['source']
print(p['repository'] + ':' + p['tag'])
print(s['repository'])
print(s['commit'])
print(s['version'])
PY
)

IMAGE_REF="${META[0]}"
SOURCE_REPO="${META[1]}"
SOURCE_COMMIT="${META[2]}"
SOURCE_VERSION="${META[3]}"

if sudo ctr -n k8s.io images list 2>/dev/null | awk '{print $1}' | grep -Fxq "$IMAGE_REF"; then
  echo "srsRAN runtime reference already exists in containerd: $IMAGE_REF"
  exit 0
fi

if ! dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q '^install ok installed$'; then
  echo "Installing Docker build tooling for the pinned srsRAN source build..."
  sudo apt-get update
  sudo apt-get install -y docker.io
fi

sudo systemctl enable --now docker
if ! sudo docker info >/dev/null 2>&1; then
  echo "Docker daemon is not ready; restarting it once..."
  sudo systemctl restart docker
  sleep 2
fi
sudo docker info >/dev/null 2>&1 || {
  echo "ERROR: Docker daemon is unavailable after installing docker.io." >&2
  sudo systemctl status docker --no-pager >&2 || true
  exit 1
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Fetching pinned srsRAN source revision..."
git -C "$WORK" init -q source
git -C "$WORK/source" remote add origin "$SOURCE_REPO"
git -C "$WORK/source" fetch -q --depth 1 origin "$SOURCE_COMMIT"
git -C "$WORK/source" checkout -q --detach FETCH_HEAD

actual="$(git -C "$WORK/source" rev-parse HEAD)"
[[ "$actual" == "$SOURCE_COMMIT" ]] || {
  echo "ERROR: fetched srsRAN revision differs from policy." >&2
  exit 1
}

# The upstream 25.04 Dockerfile's build/run dependency modes omit ZeroMQ,
# although this platform's CU/DU charts require the ZMQ radio driver.
# Add only the missing build/runtime packages; keep the source revision itself pinned.
python3 - "$WORK/source/docker/Dockerfile" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
build_old = "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git clang"
build_new = "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git clang ccache libzmq3-dev"
run_old = "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ntpdate"
run_new = "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ntpdate libzmq5"
if text.count(build_old) != 1 or text.count(run_old) != 1:
    raise SystemExit("ERROR: pinned upstream Dockerfile dependency anchors changed")
path.write_text(text.replace(build_old, build_new).replace(run_old, run_new))
PY

echo "Building srsRAN from pinned source. This can take a significant amount of time..."
sudo docker build \
  --target runtime \
  --build-arg OS_VERSION=24.04 \
  --build-arg MARCH=native \
  --build-arg NUM_JOBS="$(nproc)" \
  --build-arg EXTRA_CMAKE_ARGS="-DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON" \
  --label "org.opencontainers.image.source=$SOURCE_REPO" \
  --label "org.opencontainers.image.revision=$SOURCE_COMMIT" \
  --label "org.opencontainers.image.version=$SOURCE_VERSION" \
  --label "io.5g-kubernetes.acquisition=pinned-source-build" \
  -f "$WORK/source/docker/Dockerfile" \
  -t "$IMAGE_REF" \
  "$WORK/source"

echo "Importing pinned srsRAN image into Kubernetes containerd..."
sudo docker save "$IMAGE_REF" -o "$WORK/srsran-source-build.tar"
sudo ctr -n k8s.io images import --platform linux/amd64 "$WORK/srsran-source-build.tar" >/dev/null

sudo ctr -n k8s.io images list \
  | awk -v ref="$IMAGE_REF" '$1==ref {print; found=1} END{exit !found}'

echo "Pinned srsRAN source build is available to Kubernetes: $IMAGE_REF"
