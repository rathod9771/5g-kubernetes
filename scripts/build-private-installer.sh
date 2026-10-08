#!/usr/bin/env bash
# Build a private, self-extracting, one-command handover installer.
#
# The generated .run file contains:
#   - the exact tracked repository snapshot at the current commit
#   - the private Open5GS subscriber archive
#   - per-image Docker-format linux/amd64 archives for every image declared by
#     config/reference-versions.json / ran_images
#
# Never commit or publish the generated .run file.
set -euo pipefail
case $- in *x*) set +x ;; esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBSCRIBER_ARCHIVE="${HOME}/private-5g-input/open5gs-subscribers.archive.gz"
UE_SUBSCRIBER_INPUT="${HOME}/private-5g-input/oai-ue-subscriber.json"
OUTPUT="${HOME}/5g-orchestrator-installer.run"
BRANCH="reproducible-installer"

usage() {
  cat <<'EOF'
Usage:
  scripts/build-private-installer.sh [options]

Options:
  --subscriber-archive PATH   Private Open5GS subscriber archive
  --ue-subscriber-input PATH  Private OAI UE JSON (imsi/key/opc)
  --output PATH               Generated private handover installer
  -h, --help                  Show this help

The source host must already be a validated installation with Docker,
containerd, and Skopeo available. Registry images are exported as single-platform
linux/amd64 archives with Skopeo; the validated local srsRAN image is exported
directly from Docker. Every archive is verified through containerd before the
tracked repository snapshot and private inputs are embedded into one .run file.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscriber-archive) SUBSCRIBER_ARCHIVE="$2"; shift 2 ;;
    --ue-subscriber-input) UE_SUBSCRIBER_INPUT="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -ne 0 ]] || {
  echo "ERROR: run the bundle builder as the intended normal user, not root." >&2
  exit 1
}

[[ -d "$REPO_ROOT/.git" ]] || {
  echo "ERROR: repository metadata is required to create a pinned handover snapshot." >&2
  exit 1
}

[[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]] || {
  echo "ERROR: tracked repository changes are present; commit or intentionally revert them before building the handover bundle." >&2
  exit 1
}

[[ -f "$SUBSCRIBER_ARCHIVE" && ! -L "$SUBSCRIBER_ARCHIVE" ]] || {
  echo "ERROR: subscriber archive not found: $SUBSCRIBER_ARCHIVE" >&2
  exit 1
}
[[ -f "$UE_SUBSCRIBER_INPUT" && ! -L "$UE_SUBSCRIBER_INPUT" ]] || {
  echo "ERROR: private OAI UE subscriber input not found: $UE_SUBSCRIBER_INPUT" >&2
  exit 1
}

python3 - "$SUBSCRIBER_ARCHIVE" "$UE_SUBSCRIBER_INPUT" <<'PY'
import json, os, re, stat, sys

def secure_regular(path, label):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SystemExit(f"ERROR: {label} must be a regular file")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise SystemExit(f"ERROR: {label} must be mode 0600 or stricter")
        return fd
    except Exception:
        os.close(fd)
        raise

archive_fd = secure_regular(sys.argv[1], "subscriber archive")
try:
    if os.read(archive_fd, 2) != b"\x1f\x8b":
        raise SystemExit("ERROR: subscriber archive is not gzip data")
finally:
    os.close(archive_fd)

ue_fd = secure_regular(sys.argv[2], "OAI UE subscriber input")
try:
    with os.fdopen(ue_fd, "r") as stream:
        ue_fd = None
        data = json.load(stream)
finally:
    if ue_fd is not None:
        os.close(ue_fd)

if set(data) != {"imsi", "key", "opc"}:
    raise SystemExit("ERROR: OAI UE subscriber JSON must contain exactly imsi, key and opc")
if not isinstance(data["imsi"], str) or not re.fullmatch(r"[0-9]{15}", data["imsi"]):
    raise SystemExit("ERROR: OAI UE IMSI format is invalid")
for key in ("key", "opc"):
    if not isinstance(data[key], str) or not re.fullmatch(r"[0-9a-fA-F]{32}", data[key]):
        raise SystemExit("ERROR: OAI UE authentication field format is invalid")
PY

sudo -v
command -v docker >/dev/null 2>&1 || {
  echo "ERROR: docker is required on the validated source host." >&2
  exit 1
}
command -v ctr >/dev/null 2>&1 || {
  echo "ERROR: ctr is required on the validated source host." >&2
  exit 1
}
command -v skopeo >/dev/null 2>&1 || {
  echo "ERROR: skopeo is required on the validated source host." >&2
  echo "Install it with: sudo apt-get update && sudo apt-get install -y skopeo" >&2
  exit 1
}
sudo systemctl is-active --quiet docker || {
  echo "ERROR: Docker daemon is not active on the validated source host." >&2
  exit 1
}
sudo systemctl is-active --quiet containerd || {
  echo "ERROR: containerd is not active on the validated source host." >&2
  exit 1
}

EXPECTED_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
EXPECTED_BRANCH="$(git -C "$REPO_ROOT" branch --show-current)"
[[ "$EXPECTED_BRANCH" == "$BRANCH" ]] || {
  echo "ERROR: build the handover bundle from branch $BRANCH; current branch is $EXPECTED_BRANCH." >&2
  exit 1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PAYLOAD="$TMP/payload"
mkdir -p "$PAYLOAD"
rm -f -- "$OUTPUT"

echo "Preparing private inputs..."
cp -- "$SUBSCRIBER_ARCHIVE" "$PAYLOAD/open5gs-subscribers.archive.gz"
cp -- "$UE_SUBSCRIBER_INPUT" "$PAYLOAD/oai-ue-subscriber.json"
chmod 600 "$PAYLOAD/open5gs-subscribers.archive.gz" "$PAYLOAD/oai-ue-subscriber.json"

echo "Creating pinned repository snapshot: $EXPECTED_COMMIT"
git -C "$REPO_ROOT" archive --format=tar.gz --prefix=5g-kubernetes/ \
  -o "$PAYLOAD/repository.tar.gz" "$EXPECTED_COMMIT"

echo "Staging every required linux/amd64 image as single-platform archives..."
mapfile -t POLICY_IMAGES < <(python3 - "$REPO_ROOT/config/reference-versions.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
records={}
for ref, digest in d.get("images", {}).items():
    records[ref]=(digest, "locked")
for image in d["ran_images"]["components"].values():
    if image.get("runtime_reference") == "local-tag":
        ref=image["repository"] + ":" + image["tag"]
        records[ref]=("", "local")
    elif image.get("digest"):
        ref=image["repository"] + "@" + image["digest"]
        records[ref]=(image["digest"], "locked")
    else:
        ref=image["repository"] + ":" + image["tag"]
        records[ref]=("", "registry")
for ref in sorted(records):
    digest, mode=records[ref]
    print(ref + "|" + digest + "|" + mode)
PY
)

mkdir -p "$PAYLOAD/container-images"
: > "$PAYLOAD/image-refs.tsv"
VERIFY_NS="handover-verify-$$"
sudo ctr namespaces create "$VERIFY_NS" >/dev/null 2>&1 || true
cleanup_verify_ns() {
  sudo ctr namespaces remove "$VERIFY_NS" >/dev/null 2>&1 || true
}
trap 'cleanup_verify_ns; rm -rf "$TMP"' EXIT

index=0
for record in "${POLICY_IMAGES[@]}"; do
  IFS='|' read -r runtime_ref expected_digest mode <<<"$record"
  index=$((index + 1))
  staging_ref="$(printf 'localhost/5g-handover/image-%03d:bundle' "$index")"
  archive_name="$(printf 'image-%03d.tar' "$index")"

  archive_path="$PAYLOAD/container-images/$archive_name"

  if [[ "$mode" == local ]]; then
    sudo docker image inspect "$runtime_ref" >/dev/null 2>&1 || {
      echo "ERROR: required local image is not present in Docker: $runtime_ref" >&2
      echo "The pinned srsRAN build must remain available in Docker on the validated source host." >&2
      exit 1
    }
    local_platform="$(sudo docker image inspect --format '{{.Os}}/{{.Architecture}}' "$runtime_ref")"
    [[ "$local_platform" == "linux/amd64" ]] || {
      echo "ERROR: required local image is not linux/amd64: $runtime_ref ($local_platform)" >&2
      exit 1
    }
    echo "Saving/verifying local image $index: $runtime_ref"
    sudo docker save -o "$archive_path" "$runtime_ref"
  elif [[ "$mode" == locked ]]; then
    immutable="$runtime_ref"
    if [[ "$runtime_ref" != *@sha256:* ]]; then
      repo_without_tag="${runtime_ref%:*}"
      immutable="$repo_without_tag@$expected_digest"
    fi
    echo "Saving/verifying locked image $index: $runtime_ref"
    sudo skopeo copy \
      --override-os linux \
      --override-arch amd64 \
      "docker://$immutable" \
      "docker-archive:$archive_path:$staging_ref"
  else
    echo "Saving/verifying registry image $index: $runtime_ref"
    sudo skopeo copy \
      --override-os linux \
      --override-arch amd64 \
      "docker://$runtime_ref" \
      "docker-archive:$archive_path:$staging_ref"
  fi

  sudo chown "$(id -u):$(id -g)" "$archive_path"

  if ! sudo ctr -n "$VERIFY_NS" images import --platform linux/amd64       "$PAYLOAD/container-images/$archive_name" >/dev/null; then
    echo "ERROR: source-side handover import verification failed for: $runtime_ref" >&2
    echo "Archive: $archive_name" >&2
    exit 1
  fi

  printf '%s\t%s\t%s\n' "$runtime_ref" "$staging_ref" "$archive_name" >> "$PAYLOAD/image-refs.tsv"
done

[[ "$index" -gt 0 ]] || {
  echo "ERROR: image policy produced no bundle references." >&2
  exit 1
}

echo "Verified $index per-image linux/amd64 archives through containerd import."

(
  cd "$PAYLOAD"
  sha256sum repository.tar.gz open5gs-subscribers.archive.gz oai-ue-subscriber.json image-refs.tsv container-images/*.tar > SHA256SUMS
)

echo "Creating self-extracting handover installer..."
PAYLOAD_TAR="$TMP/payload.tar"
tar -C "$PAYLOAD" -cf "$PAYLOAD_TAR" .

umask 077

cat > "$OUTPUT" <<'HEADER1'
#!/usr/bin/env bash
set -euo pipefail
case $- in *x*) set +x ;; esac
HEADER1

printf "EXPECTED_COMMIT=%q\n" "$EXPECTED_COMMIT" >> "$OUTPUT"

cat >> "$OUTPUT" <<'HEADER2'
TARGET_REPO="${HOME}/5g-kubernetes"
PRIVATE_DIR="${HOME}/private-5g-input"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ "${EUID}" -eq 0 ]]; then
  echo "ERROR: run this installer as the intended normal user, not root." >&2
  exit 1
fi

sudo -v

PAYLOAD_LINE=$(awk '/^__PRIVATE_PAYLOAD_BELOW__$/ {print NR + 1; exit}' "$0")
[[ -n "$PAYLOAD_LINE" ]] || {
  echo "ERROR: installer payload marker missing." >&2
  exit 1
}

tail -n +"$PAYLOAD_LINE" "$0" | tar -xf - -C "$WORK"
(
  cd "$WORK"
  sha256sum -c SHA256SUMS
)

echo "Installing minimum host prerequisites..."
sudo apt-get update
sudo apt-get install -y ca-certificates containerd python3 python3-yaml iproute2
sudo systemctl enable --now containerd

if [[ -e /etc/kubernetes/admin.conf ]]; then
  echo "ERROR: target already contains a Kubernetes control plane." >&2
  echo "Use this handover installer only on the intended fresh target host." >&2
  exit 1
fi

echo "Importing bundled container images into Kubernetes containerd..."
while IFS=$'\t' read -r runtime_ref staging_ref archive_name; do
  [[ -n "$runtime_ref" && -n "$staging_ref" && -n "$archive_name" ]] || continue
  echo "  importing: $runtime_ref"
  sudo ctr -n k8s.io images import --platform linux/amd64 \
    "$WORK/container-images/$archive_name" >/dev/null
  sudo ctr -n k8s.io images tag --force "$staging_ref" "$runtime_ref" >/dev/null
done < "$WORK/image-refs.tsv"

echo "Installing pinned repository snapshot..."
if [[ -e "$TARGET_REPO" ]]; then
  echo "ERROR: $TARGET_REPO already exists; refusing to overwrite it." >&2
  exit 1
fi

mkdir -p "$HOME"
tar -xzf "$WORK/repository.tar.gz" -C "$HOME"

cd "$TARGET_REPO"

mkdir -p "$PRIVATE_DIR"
chmod 700 "$PRIVATE_DIR"
install -m 600 "$WORK/open5gs-subscribers.archive.gz" \
  "$PRIVATE_DIR/open5gs-subscribers.archive.gz"
install -m 600 "$WORK/oai-ue-subscriber.json" \
  "$PRIVATE_DIR/oai-ue-subscriber.json"

cp config/global.env.example config/global.env
chmod 600 config/global.env
python3 - "$TARGET_REPO/config/global.env" "$PRIVATE_DIR/open5gs-subscribers.archive.gz" <<'PY'
from pathlib import Path
import sys
cfg = Path(sys.argv[1])
subscriber = sys.argv[2]
lines = cfg.read_text().splitlines()
out = []
seen = False
for line in lines:
    if line.startswith("SUBSCRIBER_DATABASE_INPUT="):
        out.append("SUBSCRIBER_DATABASE_INPUT=" + subscriber)
        seen = True
    else:
        out.append(line)
if not seen:
    out.append("SUBSCRIBER_DATABASE_INPUT=" + subscriber)
cfg.write_text("\n".join(out) + "\n")
PY

echo "Bundled inputs ready:"
echo "  repository commit: $EXPECTED_COMMIT"
echo "  Open5GS private subscriber input: installed"
echo "  OAI UE private subscriber input: installed"
echo "  container image bundle: imported"
echo
echo "Starting complete 5G Orchestrator installation..."
exec ./install.sh
exit 0
__PRIVATE_PAYLOAD_BELOW__
HEADER2

cat "$PAYLOAD_TAR" >> "$OUTPUT"
chmod 600 "$OUTPUT"

echo
echo "Created final private handover bundle:"
echo "  $OUTPUT"
echo
echo "Bundle size:"
du -h "$OUTPUT"
echo
echo "This single file contains all required private subscriber inputs and a source-verified per-image linux/amd64 archive set."
echo "Never commit or publish it."
echo
echo "Transfer it securely to the target machine."
echo "The target operator runs only:"
echo "  bash ~/$(basename "$OUTPUT")"
