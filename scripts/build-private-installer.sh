#!/usr/bin/env bash
# Build a private, self-extracting, one-command handover installer.
#
# The generated .run file contains:
#   - the exact tracked repository snapshot at the current commit
#   - the private Open5GS subscriber archive
#   - a containerd export containing every image cached on this validated source host
#     plus every image declared by config/reference-versions.json / ran_images
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

The source host must already be a validated installation with containerd running.
The script pulls any missing policy-declared registry images, verifies the local
srsRAN image exists, exports the complete k8s.io image cache, embeds the tracked
repository snapshot and private subscriber input, and emits one .run file.
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
command -v ctr >/dev/null 2>&1 || {
  echo "ERROR: ctr is required on the validated source host." >&2
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

echo "Preparing private inputs..."
cp -- "$SUBSCRIBER_ARCHIVE" "$PAYLOAD/open5gs-subscribers.archive.gz"
cp -- "$UE_SUBSCRIBER_INPUT" "$PAYLOAD/oai-ue-subscriber.json"
chmod 600 "$PAYLOAD/open5gs-subscribers.archive.gz" "$PAYLOAD/oai-ue-subscriber.json"

echo "Creating pinned repository snapshot: $EXPECTED_COMMIT"
git -C "$REPO_ROOT" archive --format=tar.gz --prefix=5g-kubernetes/   -o "$PAYLOAD/repository.tar.gz" "$EXPECTED_COMMIT"

echo "Ensuring every policy-declared runtime image is available..."
mapfile -t POLICY_IMAGES < <(python3 - "$REPO_ROOT/config/reference-versions.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))

# ref|digest|mode
# mode=locked means the tag/reference must resolve to the exact policy digest.
# mode=registry means the current policy intentionally defers the digest.
# mode=local means bytes must already exist and must never be pulled.
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

image_digest() {
  local ref="$1"
  sudo ctr -n k8s.io images list | awk -v ref="$ref" '$1==ref {print $3; exit}'
}

for record in "${POLICY_IMAGES[@]}"; do
  IFS='|' read -r ref expected_digest mode <<<"$record"
  current_digest="$(image_digest "$ref")"

  if [[ "$mode" == local ]]; then
    [[ -n "$current_digest" ]] || {
      echo "ERROR: required local runtime image is missing: $ref" >&2
      echo "Run ./install.sh on the validated source host first, then rebuild the handover bundle." >&2
      exit 1
    }
    continue
  fi

  if [[ "$mode" == locked ]]; then
    if [[ -n "$current_digest" && "$current_digest" == "$expected_digest" ]]; then
      continue
    fi

    immutable="$ref"
    if [[ "$ref" != *@sha256:* ]]; then
      immutable="$ref@$expected_digest"
    fi

    if [[ -n "$current_digest" ]]; then
      echo "Reconciling cached policy image to locked digest: $ref"
      echo "  cached:   $current_digest"
      echo "  required: $expected_digest"
    else
      echo "Pulling missing digest-locked image: $ref"
    fi

    sudo ctr -n k8s.io images pull --platform linux/amd64 "$immutable"

    if [[ "$immutable" != "$ref" ]]; then
      sudo ctr -n k8s.io images tag --force "$immutable" "$ref" >/dev/null
    fi

    current_digest="$(image_digest "$ref")"
    [[ "$current_digest" == "$expected_digest" ]] || {
      echo "ERROR: digest reconciliation failed for policy image: $ref" >&2
      echo "Expected: $expected_digest" >&2
      echo "Found:    ${current_digest:-<missing>}" >&2
      exit 1
    }
    continue
  fi

  if [[ -z "$current_digest" ]]; then
    echo "Pulling missing registry image: $ref"
    sudo ctr -n k8s.io images pull --platform linux/amd64 "$ref"
  fi
done

echo "Collecting complete validated k8s.io image cache..."
mapfile -t IMAGE_REFS < <(sudo ctr -n k8s.io images list -q | sort -u)
[[ "${#IMAGE_REFS[@]}" -gt 0 ]] || {
  echo "ERROR: no containerd images were found." >&2
  exit 1
}

printf '%s\n' "${IMAGE_REFS[@]}" > "$PAYLOAD/image-refs.txt"

echo "Exporting ${#IMAGE_REFS[@]} container image references."
echo "This file can be large and may take several minutes..."
sudo ctr -n k8s.io images export --platform linux/amd64   "$PAYLOAD/container-images.tar" "${IMAGE_REFS[@]}"
sudo chown "$(id -u):$(id -g)" "$PAYLOAD/container-images.tar"

(
  cd "$PAYLOAD"
  sha256sum repository.tar.gz open5gs-subscribers.archive.gz oai-ue-subscriber.json container-images.tar image-refs.txt > SHA256SUMS
)

echo "Creating self-extracting handover installer..."
PAYLOAD_TAR="$TMP/payload.tar"
tar -C "$PAYLOAD" -cf "$PAYLOAD_TAR" .

umask 077
cat > "$OUTPUT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
case \$- in *x*) set +x ;; esac

EXPECTED_COMMIT='$EXPECTED_COMMIT'
TARGET_REPO="\${HOME}/5g-kubernetes"
PRIVATE_DIR="\${HOME}/private-5g-input"
WORK="\$(mktemp -d)"
trap 'rm -rf "\$WORK"' EXIT

if [[ "\${EUID}" -eq 0 ]]; then
  echo "ERROR: run this installer as the intended normal user, not root." >&2
  exit 1
fi

sudo -v

PAYLOAD_LINE=\$(awk '/^__PRIVATE_PAYLOAD_BELOW__\$/ {print NR + 1; exit}' "\$0")
[[ -n "\$PAYLOAD_LINE" ]] || {
  echo "ERROR: installer payload marker missing." >&2
  exit 1
}

tail -n +"\$PAYLOAD_LINE" "\$0" | tar -xf - -C "\$WORK"
(
  cd "\$WORK"
  sha256sum -c SHA256SUMS
)

echo "Installing minimum host prerequisites..."
sudo apt-get update
sudo apt-get install -y ca-certificates containerd python3 python3-yaml iproute2
sudo systemctl enable --now containerd

if [[ -e /etc/kubernetes/admin.conf ]]; then
  echo "ERROR: target already contains a Kubernetes control plane."
  echo "Use this handover installer only on the intended fresh target host." >&2
  exit 1
fi

echo "Importing bundled container images into Kubernetes containerd..."
sudo ctr -n k8s.io images import --platform linux/amd64 "\$WORK/container-images.tar" >/dev/null

echo "Installing pinned repository snapshot..."
if [[ -e "\$TARGET_REPO" ]]; then
  echo "ERROR: \$TARGET_REPO already exists; refusing to overwrite it." >&2
  exit 1
fi

mkdir -p "\$HOME"
tar -xzf "\$WORK/repository.tar.gz" -C "\$HOME"

cd "\$TARGET_REPO"
actual_tree=\$(git init -q /tmp/5g-handover-git-\$$ 2>/dev/null || true)
rm -rf /tmp/5g-handover-git-\$$ 2>/dev/null || true

mkdir -p "\$PRIVATE_DIR"
chmod 700 "\$PRIVATE_DIR"
install -m 600 "\$WORK/open5gs-subscribers.archive.gz"   "\$PRIVATE_DIR/open5gs-subscribers.archive.gz"

cp config/global.env.example config/global.env
chmod 600 config/global.env
python3 - "\$TARGET_REPO/config/global.env" "\$PRIVATE_DIR/open5gs-subscribers.archive.gz" <<'PY'
from pathlib import Path
import sys
cfg=Path(sys.argv[1])
subscriber=sys.argv[2]
out=[]
seen=False
for line in cfg.read_text().splitlines():
    if line.startswith("SUBSCRIBER_DATABASE_INPUT="):
        out.append("SUBSCRIBER_DATABASE_INPUT=" + subscriber)
        seen=True
    else:
        out.append(line)
if not seen:
    out.append("SUBSCRIBER_DATABASE_INPUT=" + subscriber)
cfg.write_text("\n".join(out) + "\n")
PY

echo "Bundled inputs ready:"
echo "  repository commit: \$EXPECTED_COMMIT"
echo "  Open5GS private subscriber input: installed"
echo "  OAI UE private subscriber input: installed"
echo "  container image bundle: imported"
echo
echo "Starting complete 5G Orchestrator installation..."
exec ./install.sh
exit 0
__PRIVATE_PAYLOAD_BELOW__
EOF

cat "$PAYLOAD_TAR" >> "$OUTPUT"
chmod 600 "$OUTPUT"

echo
echo "Created final private handover bundle:"
echo "  $OUTPUT"
echo
echo "Bundle size:"
du -h "$OUTPUT"
echo
echo "This single file contains all required private subscriber inputs and the complete validated container image cache."
echo "Never commit or publish it."
echo
echo "Transfer it securely to the target machine."
echo "The target operator runs only:"
echo "  bash ~/$(basename "$OUTPUT")"
