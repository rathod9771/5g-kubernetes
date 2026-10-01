#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE="$ROOT/installer/state"
LOGS="$ROOT/installer/logs"
mkdir -p "$STATE" "$LOGS"
source "$ROOT/scripts/common.sh"
load_config
export KUBECONFIG="${KUBECONFIG_PATH:-$HOME/.kube/config}"
log(){ printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
ok(){ log "[OK]   $*"; }
info(){ log "[INFO] $*"; }
warn(){ log "[WARN] $*" >&2; }
die(){ warn "$*"; exit 1; }
done_stage(){ test -f "$STATE/$1.ok"; }
mark_stage(){ date -Is > "$STATE/$1.ok"; }
stage(){
  local n="$1"; shift
  if done_stage "$n"; then ok "$n already completed"; return; fi
  info "$n: starting"
  if "$@" 2>&1 | tee "$LOGS/$n.log"; then mark_stage "$n"; ok "$n: complete"; else die "$n failed; see $LOGS/$n.log"; fi
}
