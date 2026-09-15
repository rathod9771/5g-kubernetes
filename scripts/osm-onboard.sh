#!/usr/bin/env bash
set -euo pipefail

OSM_HOST="https://gui.172.30.18.32.nip.io:30843"
OSM_USER="admin"
OSM_PASS="${OSM_PASSWORD:?Set OSM_PASSWORD env var}"

echo "== Getting OSM auth token =="
TOKEN=$(curl -sk -X POST "$OSM_HOST/osm/admin/v1/tokens" \
  -H "Content-Type: application/yaml" \
  --data-binary "$(printf 'username: %s\npassword: %s\nproject-id: admin' "$OSM_USER" "$OSM_PASS")" \
  | grep '^id:' | awk '{print $2}')

if [ -z "$TOKEN" ]; then
  echo "Failed to get token"; exit 1
fi
echo "Token acquired."

FAILED=0

# NOTE: OSM will reject a content update (422 UNPROCESSABLE_ENTITY) if the
# descriptor's 'version' field differs from what's already onboarded.
# Keep 'version' unchanged for in-place fixes; only bump it when you
# deliberately want to onboard a brand-new package (requires delete+recreate,
# not supported by this script's update path).

onboard_vnf() {
  local pkgdir="$1"
  local pkgname
  pkgname=$(basename "$pkgdir")
  local tarfile="/tmp/${pkgname}.tar.gz"

  echo "== Packaging $pkgname =="
  tar -czf "$tarfile" -C "$(dirname "$pkgdir")" "$pkgname"

  echo "== Checking if $pkgname already onboarded =="
  EXISTING_ID=$(curl -sk -X GET "$OSM_HOST/osm/vnfpkgm/v1/vnf_packages" \
    -H "Authorization: Bearer $TOKEN" \
    | grep -B5 "pkg-dir: $pkgname\$" | grep "folder:" | head -1 \
    | sed -E 's/.*folder: ([a-f0-9-]+):.*/\1/' || true)

  local http_status body
  if [ -n "$EXISTING_ID" ]; then
    echo "Updating existing VNF package $pkgname (id=$EXISTING_ID)"
    body=$(curl -sk -w "\nHTTP_STATUS:%{http_code}" -X PUT "$OSM_HOST/osm/vnfpkgm/v1/vnf_packages/$EXISTING_ID/package_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile")
  else
    echo "Creating new VNF package $pkgname"
    body=$(curl -sk -w "\nHTTP_STATUS:%{http_code}" -X POST "$OSM_HOST/osm/vnfpkgm/v1/vnf_packages_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile")
  fi
  http_status=$(echo "$body" | grep -oE "HTTP_STATUS:[0-9]+" | cut -d: -f2)
  echo "$body" | sed '/HTTP_STATUS:/d'
  if [[ "$http_status" -lt 200 || "$http_status" -ge 300 ]]; then
    echo "!! FAILED: $pkgname (HTTP $http_status)"
    FAILED=1
  else
    echo "OK: $pkgname (HTTP $http_status)"
  fi
  echo
}

onboard_ns() {
  local pkgdir="$1"
  local pkgname
  pkgname=$(basename "$pkgdir")
  local tarfile="/tmp/${pkgname}.tar.gz"

  echo "== Packaging $pkgname =="
  tar -czf "$tarfile" -C "$(dirname "$pkgdir")" "$pkgname"

  echo "== Checking if $pkgname already onboarded =="
  EXISTING_ID=$(curl -sk -X GET "$OSM_HOST/osm/nsd/v1/ns_descriptors" \
    -H "Authorization: Bearer $TOKEN" \
    | grep -B5 "pkg-dir: $pkgname\$" | grep "folder:" | head -1 \
    | sed -E 's/.*folder: ([a-f0-9-]+):.*/\1/' || true)

  local http_status body
  if [ -n "$EXISTING_ID" ]; then
    echo "Updating existing NS package $pkgname (id=$EXISTING_ID)"
    body=$(curl -sk -w "\nHTTP_STATUS:%{http_code}" -X PUT "$OSM_HOST/osm/nsd/v1/ns_descriptors/$EXISTING_ID/nsd_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile")
  else
    echo "Creating new NS package $pkgname"
    body=$(curl -sk -w "\nHTTP_STATUS:%{http_code}" -X POST "$OSM_HOST/osm/nsd/v1/ns_descriptors_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile")
  fi
  http_status=$(echo "$body" | grep -oE "HTTP_STATUS:[0-9]+" | cut -d: -f2)
  echo "$body" | sed '/HTTP_STATUS:/d'
  if [[ "$http_status" -lt 200 || "$http_status" -ge 300 ]]; then
    echo "!! FAILED: $pkgname (HTTP $http_status)"
    FAILED=1
  else
    echo "OK: $pkgname (HTTP $http_status)"
  fi
  echo
}

cd "$(dirname "$0")/../osm-packages"

for d in *_knf; do
  [ -d "$d" ] && onboard_vnf "$d"
done

for d in *_ns; do
  [ -d "$d" ] && onboard_ns "$d"
done

if [ "$FAILED" -ne 0 ]; then
  echo "== One or more packages failed to onboard =="
  exit 1
fi

echo "== Done =="
