#!/usr/bin/env bash
# Instantiate an NS via OSM, with automatic terminate+retry on transient failure.
# Usage: ./instantiate-with-retry.sh <nsd-name> <ns-instance-name> [max-attempts]

set -uo pipefail

OSM_HOST="https://gui.172.30.18.32.nip.io:30843"
VIM_ACCOUNT="ee97e6c7-0412-490a-9bef-a71154760091"
NSD_NAME="$1"
NS_NAME="$2"
MAX_ATTEMPTS="${3:-5}"

get_token() {
  curl -sk -X POST "$OSM_HOST/osm/admin/v1/tokens" \
    -H "Content-Type: application/yaml" \
    --data-binary "$(printf 'username: admin\npassword: admin\nproject-id: admin')" \
    | grep '^id:' | awk '{print $2}'
}

get_nsd_uuid() {
  local token="$1"
  curl -sk -X GET "$OSM_HOST/osm/nsd/v1/ns_descriptors" -H "Authorization: Bearer $token" \
    | grep -B5 "pkg-dir: $NSD_NAME\$" | grep "folder:" | awk -F'[: ]+' '{print $3}' | cut -d: -f1
}

instantiate() {
  local token="$1" nsd_uuid="$2"
  curl -sk -X POST "$OSM_HOST/osm/nslcm/v1/ns_instances_content" \
    -H "Authorization: Bearer $token" -H "Content-Type: application/yaml" \
    --data-binary "$(printf 'nsdId: %s\nnsName: %s\nnsDescription: auto-retry instantiate\nvimAccountId: %s' "$nsd_uuid" "$NS_NAME" "$VIM_ACCOUNT")"
}

wait_for_op() {
  local token="$1" op_id="$2" waited=0
  while [ "$waited" -lt 600 ]; do
    state=$(curl -sk -X GET "$OSM_HOST/osm/nslcm/v1/ns_lcm_op_occs/$op_id" -H "Authorization: Bearer $token" | grep "^operationState:" | awk '{print $2}')
    if [ "$state" = "COMPLETED" ] || [ "$state" = "PARTIALLY_COMPLETED" ]; then echo "COMPLETED"; return; fi
    if [ "$state" = "FAILED" ]; then echo "FAILED"; return; fi
    sleep 10; waited=$((waited+10))
  done
  echo "TIMEOUT"
}

cleanup_instance() {
  local token="$1" ns_id="$2"
  curl -sk -X POST "$OSM_HOST/osm/nslcm/v1/ns_instances/$ns_id/terminate" -H "Authorization: Bearer $token" -H "Content-Length: 0" >/dev/null
  sleep 30
  curl -sk -X DELETE "$OSM_HOST/osm/nslcm/v1/ns_instances/$ns_id?FORCE=true" -H "Authorization: Bearer $token" >/dev/null
  sleep 5
}

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "=== Attempt $attempt/$MAX_ATTEMPTS ==="
  TOKEN=$(get_token)
  if [ -z "$TOKEN" ]; then echo "Could not get token, retrying..."; sleep 10; continue; fi

  NSD_UUID=$(get_nsd_uuid "$TOKEN")
  if [ -z "$NSD_UUID" ]; then echo "Could not find NSD $NSD_NAME"; exit 1; fi

  RESULT=$(instantiate "$TOKEN" "$NSD_UUID")
  NS_ID=$(echo "$RESULT" | grep "^id:" | awk '{print $2}')
  OP_ID=$(echo "$RESULT" | grep "^nslcmop_id:" | awk '{print $2}')

  if [ -z "$NS_ID" ] || [ -z "$OP_ID" ]; then
    echo "Instantiate call itself failed:"
    echo "$RESULT"
    sleep 15
    continue
  fi

  echo "Instance: $NS_ID  Op: $OP_ID  -- waiting for completion..."
  STATE=$(wait_for_op "$TOKEN" "$OP_ID")
  echo "Result: $STATE"

  if [ "$STATE" = "COMPLETED" ]; then
    echo "=== SUCCESS on attempt $attempt: $NS_NAME ($NS_ID) ==="
    exit 0
  fi

  echo "Attempt $attempt failed ($STATE). Cleaning up before retry..."
  TOKEN=$(get_token)
  cleanup_instance "$TOKEN" "$NS_ID"
done

echo "=== FAILED after $MAX_ATTEMPTS attempts ==="
exit 1
