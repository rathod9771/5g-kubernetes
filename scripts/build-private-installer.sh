#!/usr/bin/env bash
# Build one private self-extracting installer for a fresh target machine.
# The generated .run file contains private subscriber data and the approved
# srsRAN image export. Never commit or publish the generated file.
set -euo pipefail
case $- in *x*) set +x ;; esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBSCRIBER_ARCHIVE="${HOME}/private-5g-input/open5gs-subscribers.archive.gz"
SRSRAN_ARCHIVE="${HOME}/private-5g-input/srsran-approved.tar"
OUTPUT="${HOME}/5g-orchestrator-installer.run"
BRANCH="reproducible-installer"
REPO_URL="https://github.com/rathod9771/5g-kubernetes.git"

usage() {
  cat <<'EOF'
Usage:
  scripts/build-private-installer.sh [options]

Options:
  --subscriber-archive PATH   Open5GS mongodump gzip archive
  --srsran-image-archive PATH Approved srsRAN Docker export
  --output PATH               Generated private installer
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscriber-archive) SUBSCRIBER_ARCHIVE="$2"; shift 2 ;;
    --srsran-image-archive) SRSRAN_ARCHIVE="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -f "$SUBSCRIBER_ARCHIVE" && ! -L "$SUBSCRIBER_ARCHIVE" ]] || {
  echo "ERROR: subscriber archive not found: $SUBSCRIBER_ARCHIVE" >&2
  exit 1
}
[[ -f "$SRSRAN_ARCHIVE" && ! -L "$SRSRAN_ARCHIVE" ]] || {
  echo "ERROR: approved srsRAN archive not found: $SRSRAN_ARCHIVE" >&2
  echo "The exact approved export is required; a newly-created export is not assumed equivalent." >&2
  exit 1
}

# Authenticate the approved srsRAN export against the repository integrity lock.
PYTHONPATH="$REPO_ROOT/scripts" python3 -B "$REPO_ROOT/scripts/srsran_image.py" verify "$SRSRAN_ARCHIVE"

# Validate the private subscriber archive without printing its contents.
python3 - "$SUBSCRIBER_ARCHIVE" <<'PY'
import os, stat, sys
p = sys.argv[1]
fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
try:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise SystemExit("ERROR: subscriber input must be a regular file")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise SystemExit("ERROR: subscriber input must be mode 0600 or stricter")
    if os.read(fd, 2) != b"\x1f\x8b":
        raise SystemExit("ERROR: subscriber input is not gzip data")
finally:
    os.close(fd)
PY

EXPECTED_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/payload"
cp -- "$SUBSCRIBER_ARCHIVE" "$TMP/payload/open5gs-subscribers.archive.gz"
cp -- "$SRSRAN_ARCHIVE" "$TMP/payload/srsran-approved.tar"
chmod 600 "$TMP/payload/"*
tar -C "$TMP/payload" -czf "$TMP/payload.tar.gz" .

umask 077
cat > "$OUTPUT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
case \$- in *x*) set +x ;; esac

REPO_URL='$REPO_URL'
BRANCH='$BRANCH'
EXPECTED_COMMIT='$EXPECTED_COMMIT'
REPO="\${HOME}/5g-kubernetes"
PRIVATE_DIR="\${HOME}/private-5g-input"

if [[ "\${EUID}" -eq 0 ]]; then
  echo "ERROR: run this as the intended normal user, not root." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git
fi

if [[ ! -d "\$REPO/.git" ]]; then
  git clone -b "\$BRANCH" "\$REPO_URL" "\$REPO"
else
  if [[ -n "\$(git -C "\$REPO" status --porcelain --untracked-files=no)" ]]; then
    echo "ERROR: existing ~/5g-kubernetes has tracked local changes; refusing to overwrite them." >&2
    exit 1
  fi
  git -C "\$REPO" fetch origin "\$BRANCH"
  git -C "\$REPO" checkout "\$BRANCH"
fi

git -C "\$REPO" cat-file -e "\$EXPECTED_COMMIT^{commit}" 2>/dev/null || git -C "\$REPO" fetch origin "\$EXPECTED_COMMIT"
git -C "\$REPO" reset --hard "\$EXPECTED_COMMIT"

mkdir -p "\$PRIVATE_DIR"
chmod 700 "\$PRIVATE_DIR"

PAYLOAD_LINE=\$(awk '/^__PRIVATE_PAYLOAD_BELOW__\$/ {print NR + 1; exit}' "\$0")
[[ -n "\$PAYLOAD_LINE" ]] || { echo "ERROR: installer payload marker missing" >&2; exit 1; }
tail -n +"\$PAYLOAD_LINE" "\$0" | tar -xzf - -C "\$PRIVATE_DIR"
chmod 600 "\$PRIVATE_DIR/open5gs-subscribers.archive.gz" "\$PRIVATE_DIR/srsran-approved.tar"

cd "\$REPO"
if [[ ! -f config/global.env ]]; then
  cp config/global.env.example config/global.env
fi
chmod 600 config/global.env
python3 - "\$REPO/config/global.env" "\$PRIVATE_DIR/open5gs-subscribers.archive.gz" <<'PY'
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

echo "Private inputs installed securely."
echo "Starting 5G Orchestrator installation..."
exec ./install.sh --srsran-image-archive "\$PRIVATE_DIR/srsran-approved.tar"
exit 0
__PRIVATE_PAYLOAD_BELOW__
EOF
cat "$TMP/payload.tar.gz" >> "$OUTPUT"
chmod 600 "$OUTPUT"

echo "Created private one-command installer:"
echo "  $OUTPUT"
echo
echo "Transfer this file securely to the target machine."
echo "On the target machine, the only command required is:"
echo "  bash ~/$(basename "$OUTPUT")"
echo
echo "WARNING: this generated file contains private subscriber data and the approved srsRAN image."
echo "Never commit, upload, email publicly, or place it in the repository."
