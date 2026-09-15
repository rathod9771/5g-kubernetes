#!/usr/bin/env bash
set -e
cd ~/5g-kubernetes

# srsRAN-flavored charts: hardcoded AMF_SERVICE env var
SRSRAN_STYLE="cloud-ran-srsran"
# OAI-flavored charts: inline getent hosts open5gs-amf-ngap
OAI_STYLE="cloud-ran-oai hcran-oai oai-cran"

build_package() {
  local chart="$1"
  local style="$2"
  local pkgname="${chart//-/_}_knf"
  echo "=== Building $pkgname from $chart ($style style) ==="

  mkdir -p osm-packages/$pkgname/helm-chart-v3s
  cp -r helm/$chart osm-packages/$pkgname/helm-chart-v3s/

  if [ "$style" = "srsran" ]; then
    sed -i 's/value: "open5gs-amf-ngap"/value: {{ .Values.amf.address | quote }}/' \
      osm-packages/$pkgname/helm-chart-v3s/$chart/templates/gnb.yaml
  else
    sed -i 's/getent hosts open5gs-amf-ngap/getent hosts {{ .Values.amf.address }}/' \
      osm-packages/$pkgname/helm-chart-v3s/$chart/templates/gnb.yaml
  fi

  cat >> osm-packages/$pkgname/helm-chart-v3s/$chart/values.yaml << VALUESEOF

amf:
  address: "amf-ngap-stable"
VALUESEOF

  cat > osm-packages/$pkgname/${pkgname}_vnfd.yaml << VNFDEOF
vnfd:
  description: KNF with single KDU using a helm-chart for $chart
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
  - name: $chart
    helm-chart: $chart
  mgmt-cp: mgmt-ext
  product-name: $pkgname
  provider: Amrita5G
  version: '1.0'
VNFDEOF

  echo "--- Verify AMF fix for $pkgname ---"
  grep -n "amf\|AMF" osm-packages/$pkgname/helm-chart-v3s/$chart/templates/gnb.yaml | head -3
  tail -3 osm-packages/$pkgname/helm-chart-v3s/$chart/values.yaml

  cd osm-packages
  tar -czf ${pkgname}.tar.gz $pkgname/
  cd ..
  echo
}

for chart in $SRSRAN_STYLE; do
  build_package "$chart" "srsran"
done

for chart in $OAI_STYLE; do
  build_package "$chart" "oai"
done

echo "=== All packages built ==="
ls -la osm-packages/*_knf.tar.gz
