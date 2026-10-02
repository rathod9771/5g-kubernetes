#!/usr/bin/env bash
# Compatibility entry point: one generator now builds both KNF and NSD.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$#" -eq 0 ]; then
  echo "Generating registry-ready RAN scenarios only; blocked/legacy components remain untouched." >&2
  set -- --ready
fi
exec python3 "${REPO_ROOT}/scripts/osm_packages.py" build "$@"
