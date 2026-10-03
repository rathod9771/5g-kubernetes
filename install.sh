#!/usr/bin/env bash
# Run only on the target Ubuntu system. Never run this during static validation.
set -euo pipefail
case $- in *x*) set +x ;; esac
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$EUID" == 0 ]]; then
  echo 'ERROR: run install.sh as the intended service user with sudo available.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
# Bootstrap only the parser prerequisite first. Validate watcher/private settings
# before any Kubernetes/service configuration or full host bootstrap.
if ! python3 -c 'import yaml' >/dev/null 2>&1 || ! command -v ip >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-yaml iproute2
fi
source "$REPO_ROOT/scripts/common.sh"
load_config --network --require install --require watcher --require osm
python3 -B "$REPO_ROOT/scripts/installer/install.py" --preflight
bash "$REPO_ROOT/scripts/installer/host.sh"
python3 -B "$REPO_ROOT/scripts/installer/install.py"
