#!/usr/bin/env bash
set -e
cd ~/5g-kubernetes

declare -A CU_CHART=( [srsran-oran]=srsran-oran-cu [vcran-oai]=oai-cu [vcran-srsran]=vcran-cu )
declare -A DU_CHART=( [srsran-oran]=srsran-oran-du [vcran-oai]=oai-du [vcran-srsran]=vcran-du )

for scenario in srsran-oran vcran-oai vcran-srsran; do
  pkgname="${scenario//-/_}_knf"
  cu_dirname=$(basename "$(grep '^name:' helm/$scenario/cu/Chart.yaml | awk '{print $2}')" 2>/dev/null)
  echo "=== Building $pkgname from $scenario (CU+DU) ==="

  mkdir -p osm-packages/$pkgname/helm-chart-v3s
  cp -r helm/$scenario/cu osm-packages/$pkgname/helm-chart-v3s/cu
  cp -r helm/$scenario/du osm-packages/$pkgname/helm-chart-v3s/du

  # Fix hardcoded AMF in CU (OAI-style inline getent hosts)
  sed -i 's/getent hosts open5gs-amf-ngap/getent hosts {{ .Values.amf.address }}/' \
    osm-packages/$pkgname/helm-chart-v3s/cu/templates/cu.yaml

  cat >> osm-packages/$pkgname/helm-chart-v3s/cu/values.yaml << VALUESEOF

amf:
  address: "amf-ngap-stable"
VALUESEOF

  # Write VNFD referencing both KDUs
  cat > osm-packages/$pkgname/${pkgname}_vnfd.yaml << VNFDEOF
vnfd:
  description: KNF with two KDUs (CU + DU) for $scenario disaggregated gNB
  df:
  - id: default-df
  ext-cpd:
  - id: mgmt-ext
    k8s-cluster-net: net1
  id: $pkgname
  k8s-cluster:
    nets:
    - id: net1
  kdu:
  - name: cu
    helm-chart: cu
  - name: du
    helm-chart: du
  mgmt-cp: mgmt-ext
  product-name: $pkgname
  provider: Amrita5G
  version: '1.0'
VNFDEOF

  echo "--- Verify AMF fix for $pkgname ---"
  grep -n "getent hosts" osm-packages/$pkgname/helm-chart-v3s/cu/templates/cu.yaml
  tail -3 osm-packages/$pkgname/helm-chart-v3s/cu/values.yaml

  cd osm-packages
  tar -czf ${pkgname}.tar.gz $pkgname/
  cd ..
  echo
done

echo "=== All 3 CU/DU packages built ==="
ls -la osm-packages/*_oran_knf.tar.gz osm-packages/vcran_*_knf.tar.gz 2>/dev/null
