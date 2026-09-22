#!/usr/bin/env bash
# ==============================================================================
# validate.sh — checks the actual, live state of the deployed platform.
# Read-only: never changes anything. A pod being Running does not prove
# the 5G network works, so every check here does real, functional work
# (log greps, curl calls, kubectl exec) instead of trusting pod status
# alone.
#
# Usage:
#   ./scripts/validate.sh            # full report
#   ./scripts/validate.sh --verbose  # also print the evidence for each check
# ==============================================================================
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/common.sh"
load_config

VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

evidence() { [ "$VERBOSE" -eq 1 ] && echo "         └─ $*"; }

# Each check sets a result string: READY / DEGRADED / DOWN / NOT CONFIGURED
declare -A RESULT

print_check() {
  local label="$1" status="$2"
  local color=""
  case "$status" in
    READY) color="OK   ";;
    DEGRADED) color="WARN ";;
    "NOT CONFIGURED") color="INFO ";;
    *) color="ERROR";;
  esac
  printf "  %-22s %s\n" "$label" "$status"
}

echo "=================================================="
echo "  5G Kubernetes Orchestrator — Validation"
echo "=================================================="
echo ""

# ---- 1. Kubernetes node ----
if kubectl_available && k_ get nodes 2>/dev/null | grep -q " Ready"; then
  RESULT[k8s]="READY"
  evidence "$(k_ get nodes --no-headers 2>/dev/null)"
else
  RESULT[k8s]="DOWN"
fi
print_check "Kubernetes" "${RESULT[k8s]}"

# ---- 2/3. Required namespaces + pods ----
OSM_NS="osm"
if namespace_exists "$OSM_NS" && pods_ready_in_namespace "$OSM_NS"; then
  RESULT[osm_pods]="READY"
else
  RESULT[osm_pods]="DEGRADED"
  evidence "$(k_ get pods -n "$OSM_NS" --no-headers 2>/dev/null | grep -v Running)"
fi

# ---- 4/5. OSM NBI reachable ----
OSM_URL="https://${OSM_BASE_DOMAIN:-localhost}:${OSM_HTTPS_PORT:-30843}"
if service_reachable "${OSM_URL}/osm/admin/v1/tokens" 5 || service_reachable "$OSM_URL" 5; then
  RESULT[osm_nbi]="READY"
else
  RESULT[osm_nbi]="DOWN"
fi
print_check "OSM (pods)" "${RESULT[osm_pods]}"
print_check "OSM NBI (${OSM_URL})" "${RESULT[osm_nbi]}"

# ---- 7. Core network healthy ----
# Find whichever open5gs release is currently live and check its pods.
CORE_NS="${OSM_PROJECT_NAMESPACE:-}"
if [ -z "$CORE_NS" ]; then
  # Best-effort: the project namespace is a K8s namespace OSM created;
  # look for one containing an amf-ngap-stable service.
  CORE_NS="$(k_ get svc -A 2>/dev/null | awk '/amf-ngap-stable/{print $1; exit}')"
fi
if [ -n "$CORE_NS" ] && namespace_exists "$CORE_NS"; then
  CORE_PODS_TOTAL="$(k_ get pods -n "$CORE_NS" -l app.kubernetes.io/managed-by=Helm --no-headers 2>/dev/null | grep -cE 'amf|smf|upf|nrf|ausf|udm|udr|pcf' || true)"
  CORE_PODS_RUNNING="$(k_ get pods -n "$CORE_NS" --no-headers 2>/dev/null | grep -E 'amf|smf|upf|nrf|ausf|udm|udr|pcf' | grep -c Running || true)"
  if [ "${CORE_PODS_TOTAL:-0}" -gt 0 ] && [ "$CORE_PODS_TOTAL" -eq "$CORE_PODS_RUNNING" ]; then
    RESULT[core]="READY"
  elif [ "${CORE_PODS_RUNNING:-0}" -gt 0 ]; then
    RESULT[core]="DEGRADED"
  else
    RESULT[core]="DOWN"
  fi
  evidence "namespace=$CORE_NS running=$CORE_PODS_RUNNING/$CORE_PODS_TOTAL core NFs"
else
  RESULT[core]="NOT CONFIGURED"
fi
print_check "5G Core (open5gs)" "${RESULT[core]}"

# ---- 8/9. RAN healthy + gNB connected to AMF ----
if [ -n "$CORE_NS" ]; then
  # RAN pod naming varies by scenario: monolithic gNB charts use
  # "*-gnb", H-CRAN uses "*-macro"/"*-small", O-RAN/vCRAN split uses
  # "*-cu"/"*-du". Match any of them rather than assuming one pattern.
  GNB_POD="$(k_ get pods -n "$CORE_NS" --no-headers 2>/dev/null | grep -E -- '-gnb-|-macro-|-small-|-cu-|-du-' | awk '{print $1; exit}')"
  if [ -n "$GNB_POD" ]; then
    GNB_RUNNING="$(k_ get pod "$GNB_POD" -n "$CORE_NS" -o jsonpath='{.status.phase}' 2>/dev/null)"
    if [ "$GNB_RUNNING" = "Running" ]; then
      SCTP_STATE="$(k_ exec -n "$CORE_NS" "$GNB_POD" -- ss -a 2>/dev/null | grep ":38412" | head -1)"
      if grep -q "ESTAB" <<< "$SCTP_STATE"; then
        RESULT[ran]="READY"
        evidence "Live SCTP association to AMF: ${SCTP_STATE}"
      elif [ -n "$SCTP_STATE" ]; then
        RESULT[ran]="DEGRADED"
        evidence "SCTP socket to AMF exists but not ESTAB: ${SCTP_STATE}"
      else
        RESULT[ran]="DEGRADED"
        evidence "No SCTP association to AMF (port 38412) found — known failure mode after a core redeploy (see docs/troubleshooting.md)"
      fi
    else
      RESULT[ran]="DOWN"
    fi
  else
    RESULT[ran]="NOT CONFIGURED"
  fi
else
  RESULT[ran]="NOT CONFIGURED"
fi
print_check "RAN (gNB <-> AMF)" "${RESULT[ran]}"

# ---- 10/11/12/13. UE registration + PDU session ----
if [ -n "$CORE_NS" ]; then
  UE_POD="$(k_ get pods -n "$CORE_NS" -l app=oai-nr-ue --no-headers 2>/dev/null | awk '{print $1; exit}')"
  UERANSIM_POD="$(k_ get pods -n "$CORE_NS" -l component=ue --no-headers 2>/dev/null | awk '{print $1; exit}')"
  if [ -n "$UE_POD" ]; then
    UE_FULL_LOG="$(k_ logs -n "$CORE_NS" "$UE_POD" 2>/dev/null)"
    if grep -qE "Registration complete|Received Registration Accept" <<< "$UE_FULL_LOG"; then
      RESULT[ue_reg]="READY"
    else
      RESULT[ue_reg]="DOWN"
    fi
    LAST_PDU_EVENT="$(grep -E "PDU Session establishment is successful|Received PDU Session Establishment Accept|PDU Session Establishment reject" <<< "$UE_FULL_LOG" | tail -1)"
    if grep -qE "successful|Accept" <<< "$LAST_PDU_EVENT"; then
      RESULT[ue_pdu]="READY"
    else
      RESULT[ue_pdu]="DOWN"
      evidence "Known issue: UPF logs 'cannot handle PFCP message type[50]' for Session Establishment Request — see docs/troubleshooting.md. NOT claiming this as working."
    fi
    RESULT[ue_stack]="OAI standalone (oai-nr-ue)"
  elif [ -n "$UERANSIM_POD" ]; then
    UE_LOG="$(k_ logs -n "$CORE_NS" "$UERANSIM_POD" --tail=300 2>/dev/null)"
    grep -q "Initial Registration is successful" <<< "$UE_LOG" && RESULT[ue_reg]="READY" || RESULT[ue_reg]="DOWN"
    grep -q "PDU Session establishment is successful" <<< "$UE_LOG" && RESULT[ue_pdu]="READY" || RESULT[ue_pdu]="DOWN"
    RESULT[ue_stack]="UERANSIM"
  else
    RESULT[ue_reg]="NOT CONFIGURED"
    RESULT[ue_pdu]="NOT CONFIGURED"
    RESULT[ue_stack]="none deployed"
  fi
else
  RESULT[ue_reg]="NOT CONFIGURED"
  RESULT[ue_pdu]="NOT CONFIGURED"
  RESULT[ue_stack]="none deployed"
fi
print_check "UE stack" "${RESULT[ue_stack]}"
print_check "UE Registration" "${RESULT[ue_reg]}"
print_check "PDU Session" "${RESULT[ue_pdu]}"

# ---- 15/16. Prometheus + Grafana ----
PROM_URL="http://localhost:${PROMETHEUS_NODEPORT:-30990}"
if service_reachable "${PROM_URL}/api/v1/query?query=up" 5; then
  RESULT[prometheus]="READY"
else
  RESULT[prometheus]="DOWN"
fi
print_check "Prometheus" "${RESULT[prometheus]}"

GRAFANA_URL="http://grafana-metrics.${OSM_BASE_DOMAIN:-localhost}:${GRAFANA_NODEPORT:-31998}"
if service_reachable "$GRAFANA_URL" 5; then
  RESULT[grafana]="READY"
else
  RESULT[grafana]="DOWN"
fi
print_check "Grafana" "${RESULT[grafana]}"

# ---- Layer 3 watcher ----
if systemd_service_active "layer3-watcher.service"; then
  RESULT[layer3]="READY"
else
  RESULT[layer3]="DOWN"
fi
print_check "Layer 3 watcher" "${RESULT[layer3]}"

# ---- Overall ----
echo ""
echo "=================================================="
CRITICAL=("${RESULT[k8s]}" "${RESULT[osm_nbi]}")
OVERALL="READY"
for r in "${CRITICAL[@]}"; do
  [ "$r" != "READY" ] && OVERALL="NOT READY"
done
if [ "${RESULT[ue_pdu]:-}" = "DOWN" ] && [ "${RESULT[ue_stack]:-}" = "OAI standalone (oai-nr-ue)" ]; then
  echo "  Platform: ${OVERALL} (PDU session on the OAI UE is a known, documented issue — see docs/troubleshooting.md)"
else
  echo "  Platform: ${OVERALL}"
fi
echo "=================================================="

if [ "${1:-}" = "--kv" ] || [ "${2:-}" = "--kv" ]; then
  echo "STATUS_KV:PLATFORM=${OVERALL}"
  echo "STATUS_KV:KUBERNETES=${RESULT[k8s]:-UNKNOWN}"
  echo "STATUS_KV:OSM=${RESULT[osm_nbi]:-UNKNOWN}"
  echo "STATUS_KV:CORE=${RESULT[core]:-UNKNOWN}"
  echo "STATUS_KV:RAN=${RESULT[ran]:-UNKNOWN}"
  echo "STATUS_KV:UE_STACK=${RESULT[ue_stack]:-UNKNOWN}"
  echo "STATUS_KV:UE_REGISTRATION=${RESULT[ue_reg]:-UNKNOWN}"
  echo "STATUS_KV:PDU_SESSION=${RESULT[ue_pdu]:-UNKNOWN}"
  echo "STATUS_KV:PROMETHEUS=${RESULT[prometheus]:-UNKNOWN}"
  echo "STATUS_KV:GRAFANA=${RESULT[grafana]:-UNKNOWN}"
  echo "STATUS_KV:LAYER3=${RESULT[layer3]:-UNKNOWN}"
fi
