#!/usr/bin/env bash
set -e
cd ~/5g-kubernetes/osm-packages

declare -A KDU_NAME=(
  [cran_oai_knf]=cran-oai
  [oran_srsran_knf]=oran-srsran
  [oran_oai_knf]=oran-oai
  [cloudran_srsran_knf]=cloudran-srsran
  [cloudran_oai_knf]=cloudran-oai
  [hcran_srsran_knf]=hcran-srsran
  [hcran_oai_knf]=hcran-oai
  [vcran_srsran_knf]=vcran-srsran
  [vcran_oai_knf]=vcran-oai
  [fran_knf]=fran
)

for vnf in "${!KDU_NAME[@]}"; do
  member="${KDU_NAME[$vnf]}"
  nsname="${vnf%_knf}_ns"
  echo "=== Building $nsname referencing $vnf ==="

  mkdir -p "$nsname"
  cat > "$nsname/${nsname}_nsd.yaml" << EOF
nsd:
  nsd:
  - description: NS consisting of a single KNF $vnf connected to mgmt network
    designer: Amrita5G
    df:
    - id: default-df
      vnf-profile:
      - id: $member
        virtual-link-connectivity:
        - constituent-cpd-id:
          - constituent-base-element-id: $member
            constituent-cpd-id: mgmt-ext
          virtual-link-profile-id: net1
        vnfd-id: $vnf
    id: $nsname
    name: $nsname
    version: 1.0
    virtual-link-desc:
    - id: net1
      mgmt-network: true
    vnfd-id:
    - $vnf
EOF

  tar -czf "${nsname}.tar.gz" "$nsname/"
  echo "Built ${nsname}.tar.gz"
  echo
done

echo "=== All 10 NS packages built ==="
ls -la *_ns.tar.gz
