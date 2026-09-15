#!/usr/bin/env bash
set -e
cd ~/5g-kubernetes/osm-packages

declare -A RENAME=(
  [srsran_knf]=cran_srsran_knf
  [oai_cran_knf]=cran_oai_knf
  [cloud_ran_srsran_knf]=cloudran_srsran_knf
  [cloud_ran_oai_knf]=cloudran_oai_knf
  [srsran_oran_knf]=oran_srsran_knf
  [oai_cu_du_knf]=oran_oai_knf
  [fran_edge_knf]=fran_knf
)

for old in "${!RENAME[@]}"; do
  new="${RENAME[$old]}"
  echo "=== Renaming $old -> $new ==="

  git mv "$old" "$new"
  git mv "${old}.tar.gz" "${new}.tar.gz" 2>/dev/null || true

  git mv "$new/${old}_vnfd.yaml" "$new/${new}_vnfd.yaml"

  sed -i "s/id: $old\$/id: $new/" "$new/${new}_vnfd.yaml"
  sed -i "s/product-name: $old\$/product-name: $new/" "$new/${new}_vnfd.yaml"

  echo "--- verify ---"
  grep -n "^  id:\|product-name:" "$new/${new}_vnfd.yaml"

  rm -f "${new}.tar.gz"
  tar -czf "${new}.tar.gz" "$new/"

  echo
done

echo "=== Done renaming ==="
git status
