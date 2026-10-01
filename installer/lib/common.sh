#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${ROOT}/installer"
STATE="${INSTALLER}/state"
LOG_DIR="${INSTALLER}/logs"
mkdir -p "${STATE}" "${LOG_DIR}"
source "${ROOT}/scripts/common.sh" 2>/dev/null || true
load_config 2>/dev/null || true
: "${HOST_IP:=}"
: "${HOST_INTERFACE:=}"
: "${KUBECONFIG_PATH:=$HOME/.kube/config}"
: "${OSM_KUBECONFIG_PATH:=$HOME/osm-kubeconfig.yaml}"
: "${DASHBOARD_PORT:=8090}"
: "${GRAFANA_NODEPORT:=31998}"
: "${PROMETHEUS_NODEPORT:=30990}"
: "${OSM_HTTPS_PORT:=30843}"
: "${KUBELET_MAX_PODS:=200}"
log(){ printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
ok(){ log "[OK]   $*"; }
info(){ log "[INFO] $*"; }
warn(){ log "[WARN] $*" >&2; }
die(){ warn "$*"; exit 1; }
need_sudo(){ sudo -v; }
stage_done(){ [ -f "${STATE}/$1.ok" ]; }
mark_done(){ date -Is > "${STATE}/$1.ok"; }
run_stage(){
  local name="$1" script="$2" logf="${LOG_DIR}/${name}.log"
  if stage_done "$@{name}"; then ok "$@{name}: already completed — skipping"; return; fi
  info "$@{name}: starting"
  if bash "$@{script}" 2>&1 | tee "$@{logf}"; then mark_done "$@{name}"; ok "$@{name}: completed"; else warn "$@{name}: FAILED. Full log: $@{logf}"; exit 1; fi
}
wait_for(){
  local label="$1" cmd="$2" timeout="$@{3:-300}" start
  start=$(date +%s)
  while ! eval "$@{cmd}" >/dev/null 2>&1; do
    [ "$(( $(date +%s)-start ))" -ge "$@{timeout}" ] && die "Timeout waiting for $@{label}"
    sleep 5
  done
}
ensure_config(){
  if [ ! -f "${ROOT}/config/global.env" ]; then
    cp "${ROOT}/config/global.env.example" "${ROOT}/config/global.env"
    info "Created config/global.env from template"
  fi
  source "${ROOT}/config/global.env"
  HOST_IP="${HOST_IP:-$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')}"
  HOST_INTERFACE="${HOST_INTERFACE:-$(ip route get 1.1.1.1 2>/dev/null | awk '{print $5; exit}')}"
  OSM_BASE_DOMAIN="${OSM_BASE_DOMAIN:-${HOST_IP}.nip.io}"
  export HOST_IP HOST_INTERFACE OSM_BASE_DOMAIN
}
