#!/usr/bin/env bash
# Run only on the target Ubuntu system. Never run this during static validation.
set -euo pipefail
case $- in *x*) set +x ;; esac
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --platform-only-plan ]]; then
  # Platform-only profile: print the non-mutating research-only setup plan and
  # exit. Never provisions, builds or removes the reference RAN/Core workloads
  # below, imports no images/subscribers and starts no service.
  exec env PYTHONPATH="$REPO_ROOT/scripts" python3 -m telecom.research_setup --mode research-only --reuse-existing --plan
fi
platform_only=0
if [[ "${1:-}" == --platform-only ]]; then
  # Platform-only profile: install the shared orchestration infrastructure
  # (Kubernetes/OSM/Rancher/monitoring/researcher dashboard) only. Never
  # builds srsRAN, never requires a subscriber database input, never
  # provisions or removes the reference RAN/Core workloads below.
  [[ "$#" -eq 1 ]] || { echo 'Usage: ./install.sh --platform-only' >&2; exit 2; }
  platform_only=1
elif [[ "$#" -gt 0 ]]; then
  [[ "$#" -eq 2 && "$1" == --srsran-image-archive && -n "$2" ]] || {
    echo 'Usage: ./install.sh [--srsran-image-archive /path/to/approved-export.tar] [--platform-only] [--platform-only-plan]' >&2
    exit 2
  }
fi
if [[ "$EUID" == 0 ]]; then
  echo 'ERROR: run install.sh as the intended service user with sudo available.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1

# Bootstrap parser/network prerequisites.
if ! python3 -c 'import yaml' >/dev/null 2>&1 || ! command -v ip >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-yaml iproute2
fi

source "$REPO_ROOT/scripts/common.sh"
load_config --network --require install --require osm

if [[ "$platform_only" == 1 ]]; then
  python3 -B "$REPO_ROOT/scripts/installer/install.py" --platform-only --validate-inputs
  bash "$REPO_ROOT/scripts/installer/host.sh"
  python3 -B "$REPO_ROOT/scripts/installer/install.py" --platform-only --preflight
  python3 -B "$REPO_ROOT/scripts/installer/install.py" --platform-only
  exit 0
fi

# Authenticate private inputs and reject unsafe active services before host changes.
python3 -B "$REPO_ROOT/scripts/installer/install.py" --validate-inputs

if [[ "$#" -eq 2 ]]; then
  # Legacy handover path: authenticate the supplied export before modifying the host.
  python3 -B "$REPO_ROOT/scripts/installer/install.py" --preflight "$@"
  bash "$REPO_ROOT/scripts/installer/host.sh"
else
  # Fresh-machine path: prepare the host, build the supported srsRAN image from the
  # immutable upstream revision, then run the same policy preflight.
  bash "$REPO_ROOT/scripts/installer/host.sh"
  bash "$REPO_ROOT/scripts/build_srsran_from_source.sh"
  python3 -B "$REPO_ROOT/scripts/installer/install.py" --preflight
fi

python3 -B "$REPO_ROOT/scripts/installer/install.py" "$@"
