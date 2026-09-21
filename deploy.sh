#!/usr/bin/env bash
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${REPO_ROOT}/scripts/common.sh"
load_config

DASHBOARD_URL="http://localhost:${DASHBOARD_PORT:-8090}"

usage() {
  cat << USAGE
Usage: $0 <scenario-key>
       $0 --list

Deploys the given scenario via the RAN selector dashboard's own
/api/deploy (same path the UI and Layer 3's watcher use).

Examples:
  $0 cloudran-oai
  $0 hcran-srsran
  $0 --list          # show every valid scenario key
USAGE
}

if [ "${1:-}" = "--list" ] || [ "${1:-}" = "-l" ]; then
  RESP="$(curl -sk --max-time 10 "${DASHBOARD_URL}/api/scenarios" 2>/dev/null)"
  if [ -z "$RESP" ]; then
    fail "Could not reach the dashboard at ${DASHBOARD_URL}/api/scenarios" \
      "ran-selector.service may not be running -- check: systemctl status ran-selector" \
      "DASHBOARD_PORT in config/global.env may not match the actual running port"
  fi
  echo "$RESP" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for scen in d.get("scenarios", []):
    key = scen.get("key", "")
    name = scen.get("name", "")
    print("  " + key.ljust(20) + " " + name)
' 2>/dev/null || echo "$RESP"
  exit 0
fi

if [ "$#" -lt 1 ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 1
fi

SCENARIO="$1"

log_info "Checking the dashboard is reachable at ${DASHBOARD_URL}..."
if ! service_reachable "${DASHBOARD_URL}/api/scenarios" 5; then
  fail "Dashboard not reachable at ${DASHBOARD_URL}" \
    "ran-selector.service is not running -- start it: sudo systemctl start ran-selector" \
    "DASHBOARD_PORT in config/global.env doesn't match the service's actual port" \
    "The dashboard may still be starting up -- wait a few seconds and retry"
fi
log_ok "Dashboard reachable"

log_info "Deploying scenario '${SCENARIO}' -- this is a real OSM terminate+instantiate, typically 1-3 minutes..."
RESPONSE="$(curl -sk --max-time 200 -X POST "${DASHBOARD_URL}/api/deploy" \
  -H "Content-Type: application/json" \
  -d "{\"ran\": \"${SCENARIO}\"}")"

if [ -z "$RESPONSE" ]; then
  fail "No response from the dashboard's /api/deploy" \
    "The request may have timed out (200s) -- a real OSM operation can occasionally take longer" \
    "Check the dashboard's own logs: journalctl -u ran-selector -n 50"
fi

STATUS="$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status","error"))' 2>/dev/null)"

if [ "$STATUS" = "success" ]; then
  NS_ID="$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ns_instance_id",""))' 2>/dev/null)"
  NAME="$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("name",""))' 2>/dev/null)"
  log_ok "Deployed: ${NAME} (ns_instance_id=${NS_ID})"
  echo ""
  echo "Run ./status.sh or ./scripts/validate.sh --verbose to check the real state."
  exit 0
else
  ERROR_MSG="$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("error","unknown error"))' 2>/dev/null)"
  fail "Deployment failed: ${ERROR_MSG}" \
    "Run '$0 --list' to confirm '${SCENARIO}' is a valid scenario key" \
    "Check OSM's own state: the old RAN may not have torn down cleanly" \
    "Full response was: ${RESPONSE}"
fi
