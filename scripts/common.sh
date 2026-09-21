#!/usr/bin/env bash
set -uo pipefail

_ts() { date '+%H:%M:%S'; }
log_info()  { echo "[$(_ts)] [INFO]  $*"; }
log_ok()    { echo "[$(_ts)] [OK]    $*"; }
log_warn()  { echo "[$(_ts)] [WARN]  $*" >&2; }
log_error() { echo "[$(_ts)] [ERROR] $*" >&2; }

fail() {
  local msg="$1"; shift || true
  log_error "$msg"
  if [ "$#" -gt 0 ]; then
    echo "" >&2
    echo "Possible causes:" >&2
    for cause in "$@"; do
      echo "  - $cause" >&2
    done
  fi
  exit 1
}

load_config() {
  local cfg="${REPO_ROOT}/config/global.env"
  local cfg_example="${REPO_ROOT}/config/global.env.example"
  if [ -f "$cfg" ]; then
    source "$cfg"
    log_info "Loaded config/global.env"
  elif [ -f "$cfg_example" ]; then
    source "$cfg_example"
    log_warn "config/global.env not found — using config/global.env.example defaults"
    log_warn "Run: cp config/global.env.example config/global.env"
  else
    fail "Neither config/global.env nor config/global.env.example found" \
      "Run this script from the repository root" \
      "The repository clone may be incomplete"
  fi

  if [ -z "${HOST_IP:-}" ]; then
    HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$HOST_IP" ] && log_info "Auto-detected HOST_IP=$HOST_IP"
  fi
  if [ -z "${HOST_INTERFACE:-}" ]; then
    HOST_INTERFACE="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $5; exit}')"
    [ -n "$HOST_INTERFACE" ] && log_info "Auto-detected HOST_INTERFACE=$HOST_INTERFACE"
  fi
  if [ -z "${OSM_BASE_DOMAIN:-}" ] && [ -n "${HOST_IP:-}" ]; then
    OSM_BASE_DOMAIN="${HOST_IP}.nip.io"
  fi
  export HOST_IP HOST_INTERFACE OSM_BASE_DOMAIN
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

k_() { kubectl --kubeconfig "${OSM_KUBECONFIG_PATH:-$HOME/osm-kubeconfig.yaml}" "$@"; }

kubectl_available() {
  command_exists kubectl && k_ version --client >/dev/null 2>&1
}

namespace_exists() {
  k_ get namespace "$1" >/dev/null 2>&1
}

helm_release_exists() {
  local release="$1" ns="${2:-default}"
  helm status "$release" -n "$ns" >/dev/null 2>&1
}

crd_exists() {
  k_ get crd "$1" >/dev/null 2>&1
}

pods_ready_in_namespace() {
  local ns="$1"
  local total ready
  total="$(k_ get pods -n "$ns" --no-headers 2>/dev/null | wc -l)"
  [ "$total" -eq 0 ] && return 1
  ready="$(k_ get pods -n "$ns" --no-headers 2>/dev/null | awk '{split($2,a,"/"); if (a[1]==a[2] && $3=="Running") c++} END{print c+0}')"
  [ "$ready" -eq "$total" ]
}

service_reachable() {
  # Any HTTP response at all -- even 404/401 -- proves the server is
  # alive and answering; only a connection failure (curl exit != 0,
  # or code 000) means genuinely down. A stricter 2xx/3xx check would
  # wrongly report OSM's bare root (a legitimate 404) as down.
  local url="$1" timeout="${2:-3}"
  local code
  code="$(curl -sk --max-time "$timeout" -o /dev/null -w '%{http_code}' "$url" 2>/dev/null)"
  [ -n "$code" ] && [ "$code" != "000" ]
}

systemd_service_active() {
  systemctl is-active --quiet "$1" 2>/dev/null
}

skip_or_run() {
  local label="$1" check="$2" action="$3"
  if eval "$check"; then
    log_ok "$label already present — skipping"
  else
    log_info "$label not found — installing"
    eval "$action"
  fi
}
