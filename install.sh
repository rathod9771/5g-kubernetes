#!/usr/bin/env bash
# Run only on the target Ubuntu system. Never run this during static validation.
set -euo pipefail
case $- in *x*) set +x ;; esac
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Optional explicit HP/Dell fresh-host gate; always run before apt, sudo, or lifecycle.
if [[ "${1:-}" == --profile ]]; then
  if [[ "$#" -lt 2 || ( "$2" != hp && "$2" != dell ) ]]; then
    echo 'ERROR: --profile requires hp or dell.' >&2
    exit 2
  fi
  HOST_PROFILE="$2"
  shift 2
  if [[ "${1:-}" == --check-only ]]; then
    [[ "$#" -eq 1 ]] || { echo 'ERROR: --check-only accepts no other arguments.' >&2; exit 2; }
    python3 -B "$REPO_ROOT/scripts/installer/host_profile.py" --profile "$HOST_PROFILE"
    echo 'Fresh-host admission only: no installation or deployment performed.'
    exit 0
  fi
  python3 -B "$REPO_ROOT/scripts/installer/host_profile.py" --profile "$HOST_PROFILE"
fi
if [[ "$#" -gt 0 ]]; then
  [[ "$#" -eq 2 && "$1" == --srsran-image-archive && -n "$2" ]] || {
    echo 'Usage: ./install.sh [--srsran-image-archive /path/to/approved-export.tar]' >&2
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
