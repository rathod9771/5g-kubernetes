#!/usr/bin/env bash
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${REPO_ROOT}/scripts/common.sh"
load_config

usage() {
  cat << USAGE
Usage: $0 [--ran] [--monitoring] [--core] [--platform] [--all]

Every flag requires typing "yes" at a confirmation prompt before doing
anything. Nothing here runs by default -- you must name what to tear
down.

  --ran          Terminate the currently active RAN NS instance via OSM
  --monitoring   Uninstall kube-prometheus-stack, ran-exporter, latency-probe
  --core         Terminate the open5gs core NS instance via OSM
  --platform     Uninstall OSM, Longhorn, cert-manager (does NOT touch Kubernetes itself)
  --all          All of the above, in dependency-safe order (ran -> core -> monitoring -> platform)
USAGE
}

confirm() {
  local what="$1"
  echo ""
  echo "  ⚠  This will PERMANENTLY remove: ${what}"
  read -rp "  Type 'yes' to confirm, anything else to skip: " ans
  [ "$ans" = "yes" ]
}

do_ran() {
  if confirm "the currently active RAN NS instance (via OSM terminate)"; then
    ACTIVE_SCENARIO="$(python3 -c "
import yaml
try:
    with open('${REPO_ROOT}/ran-selector/active-ran.yaml') as f:
        d = yaml.safe_load(f) or {}
    print(d.get('active') or '')
except Exception:
    print('')
" 2>/dev/null)"
    if [ -z "$ACTIVE_SCENARIO" ] || [ "$ACTIVE_SCENARIO" = "none" ]; then
      log_info "No active RAN scenario recorded — nothing to terminate"
    else
      log_info "Terminating '${ACTIVE_SCENARIO}' via the dashboard's /api/deploy(ran=none) equivalent..."
      log_warn "Not yet wired to a direct terminate-only API call -- terminate manually via the dashboard or OSM GUI for now"
    fi
  else
    log_info "Skipped --ran"
  fi
}

do_monitoring() {
  if confirm "kube-prometheus-stack, ran-exporter, latency-probe, and their PodMonitors"; then
    log_info "Uninstalling monitoring components..."
    helm uninstall kube-prometheus-stack -n monitoring 2>/dev/null || true
    kubectl delete namespace monitoring --ignore-not-found 2>/dev/null || true
    if [ -n "${OSM_PROJECT_NAMESPACE:-}" ]; then
      kubectl delete deployment ran-exporter latency-probe -n "${OSM_PROJECT_NAMESPACE}" --ignore-not-found 2>/dev/null || true
    fi
    log_ok "Monitoring removed"
  else
    log_info "Skipped --monitoring"
  fi
}

do_core() {
  if confirm "the open5gs core NS instance (via OSM terminate)"; then
    log_warn "Not yet wired to a direct terminate-only API call -- terminate the core NS via the OSM GUI for now: ${OSM_BASE_DOMAIN:+https://$OSM_BASE_DOMAIN:${OSM_HTTPS_PORT:-30843}}"
  else
    log_info "Skipped --core"
  fi
}

do_platform() {
  if confirm "OSM, Longhorn, and cert-manager (Kubernetes itself is left alone)"; then
    log_info "Uninstalling OSM..."
    kubectl delete namespace osm --ignore-not-found 2>/dev/null || true
    log_info "Uninstalling Longhorn..."
    helm uninstall longhorn -n longhorn-system 2>/dev/null || true
    kubectl delete namespace longhorn-system --ignore-not-found 2>/dev/null || true
    log_info "Uninstalling cert-manager..."
    kubectl delete -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.5/cert-manager.yaml --ignore-not-found 2>/dev/null || true
    log_ok "Platform components removed. Kubernetes itself is untouched -- reset it separately if needed (kubeadm reset)."
  else
    log_info "Skipped --platform"
  fi
}

if [ "$#" -eq 0 ]; then
  usage
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    --ran) do_ran ;;
    --monitoring) do_monitoring ;;
    --core) do_core ;;
    --platform) do_platform ;;
    --all)
      do_ran
      do_core
      do_monitoring
      do_platform
      ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown flag: '${arg}'" "Run '$0 --help' for usage" ;;
  esac
done
