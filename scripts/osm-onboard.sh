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

  if [ -n "$EXISTING_ID" ]; then
    echo "Updating existing VNF package $pkgname (id=$EXISTING_ID)"
    curl -sk -X PUT "$OSM_HOST/osm/vnfpkgm/v1/vnf_packages/$EXISTING_ID/package_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile"
  else
    echo "Creating new VNF package $pkgname"
    curl -sk -X POST "$OSM_HOST/osm/vnfpkgm/v1/vnf_packages_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile"
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

  if [ -n "$EXISTING_ID" ]; then
    echo "Updating existing NS package $pkgname (id=$EXISTING_ID)"
    curl -sk -X PUT "$OSM_HOST/osm/nsd/v1/ns_descriptors/$EXISTING_ID/nsd_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile"
  else
    echo "Creating new NS package $pkgname"
    curl -sk -X POST "$OSM_HOST/osm/nsd/v1/ns_descriptors_content" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary "@$tarfile"
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

echo "== Done =="
