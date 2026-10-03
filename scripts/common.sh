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
  # Disable tracing before reading any credential. Restore the caller's mode last.
  local tracing=0
  case $- in *x*) tracing=1; set +x ;; esac
  local records name value buffer
  buffer="$(mktemp -d)" || fail "Cannot allocate private configuration buffer"
  records="$buffer/records"
  chmod 700 "$buffer"
  if ! python3 "${REPO_ROOT}/scripts/runtime_config.py" --auto-network --snapshot "$buffer/config.json" --shell-records "$@" > "$records"; then
    rm -f "$records" "$buffer/config.json"
    rmdir "$buffer"
    fail "Runtime configuration validation failed"
  fi
  while IFS= read -r -d '' name && IFS= read -r -d '' value; do
    case "$name" in
      OSM_PASSWORD|GRAFANA_ADMIN_PASSWORD|RANCHER_BOOTSTRAP_PASSWORD|OSM_BOOTSTRAP_PASSWORD)
        # Secrets live only in the owner-only snapshot; do not retain shell variables.
        unset "$name"
        ;;
      *) printf -v "$name" '%s' "$value"; export "$name" ;;
    esac
  done < "$records"
  rm -f "$records"
  export P0_RUNTIME_CONFIG="$buffer/config.json"
  # Children inherit only the snapshot path, never individual credentials.
  P0_CONFIG_BUFFER="$buffer"
  trap 'rm -f "$P0_CONFIG_BUFFER/config.json"; rmdir "$P0_CONFIG_BUFFER"' EXIT
  [ "$tracing" -eq 0 ] || set -x
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

# Management owns OSM/monitoring/Rancher; workload owns core/RAN/exporters.
p_kubectl() { command kubectl --kubeconfig "${KUBECONFIG_PATH:?load_config is required}" "$@"; }
r_kubectl() { command kubectl --kubeconfig "${OSM_KUBECONFIG_PATH:?load_config is required}" "$@"; }
p_helm() { command helm --kubeconfig "${KUBECONFIG_PATH:?load_config is required}" "$@"; }
r_helm() { command helm --kubeconfig "${OSM_KUBECONFIG_PATH:?load_config is required}" "$@"; }
k_() { r_kubectl "$@"; } # Existing workload callers, retained for compatibility.

kubectl_available() {
  command_exists kubectl && p_kubectl version --client >/dev/null 2>&1
}

namespace_exists() {
  p_kubectl get namespace "$1" >/dev/null 2>&1
}

ran_namespace_exists() { r_kubectl get namespace "$1" >/dev/null 2>&1; }

helm_release_exists() {
  local release="$1" ns="${2:-default}"
  p_helm status "$release" -n "$ns" >/dev/null 2>&1
}

crd_exists() {
  p_kubectl get crd "$1" >/dev/null 2>&1
}

pods_ready_in_namespace() {
  local ns="$1"
  local total ready
  total="$(p_kubectl get pods -n "$ns" --no-headers 2>/dev/null | wc -l)"
  [ "$total" -eq 0 ] && return 1
  ready="$(p_kubectl get pods -n "$ns" --no-headers 2>/dev/null | awk '{split($2,a,"/"); if (a[1]==a[2] && $3=="Running") c++} END{print c+0}')"
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
