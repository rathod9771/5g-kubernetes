#!/usr/bin/env bash
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RAW="$("${REPO_ROOT}/scripts/validate.sh" --kv 2>/dev/null)"

kv() {
  echo "$RAW" | grep "^STATUS_KV:$1=" | head -1 | cut -d= -f2-
}

ACTIVE_RAN="none"
ACTIVE_STACK="—"
if [ -f "${REPO_ROOT}/ran-selector/active-ran.yaml" ]; then
  ACTIVE_SCENARIO="$(python3 -c "
import yaml
try:
    with open('${REPO_ROOT}/ran-selector/active-ran.yaml') as f:
        d = yaml.safe_load(f) or {}
    print(d.get('active') or 'none')
except Exception:
    print('none')
" 2>/dev/null)"
  ACTIVE_RAN="${ACTIVE_SCENARIO:-none}"
  case "$ACTIVE_RAN" in
    *oai*) ACTIVE_STACK="OAI" ;;
    *srsran*) ACTIVE_STACK="srsRAN" ;;
  esac
fi

echo "Platform:     $(kv PLATFORM)"
echo "RAN:          ${ACTIVE_STACK}"
echo "Scenario:     ${ACTIVE_RAN}"
echo "Core:         Open5GS ($(kv CORE))"
echo "Kubernetes:   $(kv KUBERNETES)"
echo "OSM:          $(kv OSM)"
echo "gNB:          $(kv RAN)"
echo "UE stack:     $(kv UE_STACK)"
echo "UE:           $(kv UE_REGISTRATION)"
echo "PDU SESSION:  $(kv PDU_SESSION)"
echo "PROMETHEUS:   $(kv PROMETHEUS)"
echo "GRAFANA:      $(kv GRAFANA)"
echo "LAYER 3:      $(kv LAYER3)"
echo ""
echo "For details and evidence behind each line: ./scripts/validate.sh --verbose"
