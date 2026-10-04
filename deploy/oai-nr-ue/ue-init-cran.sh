#!/bin/bash
# C-RAN TEST VARIANT of ue-init.sh. A separate script so the shared,
# working ConfigMap oai-nr-ue-config (and the file inside it) is never
# modified. Subscriber values (imsi/key/opc/dnn/nssai) still come ONLY
# from the private oai-nr-ue-config Secret, mounted read-only.
#
# Frequency fix: the CU/DU-split C-RAN DU is configured for a different
# carrier than the monolithic gNB the shared script was written for.
# The DU itself prints the parameters it expects at every startup:
#   [NR_MAC] Command line parameters for OAI UE: -C 3450720000 -r 106
#     --numerology 1 --band 78 --ssb 516
# (confirmed live, cran-oai-du log, 2026-09-28). The shared script's
# -C 3319680000 matches only the monolithic gNB's carrier, a ~130.72 MHz
# mismatch against this DU -- explains sync/PBCH succeeding while every
# SIB1 decode attempt NACKs. Two changes from the shared script below:
#   -C 3319680000  ->  -C 3450720000
#   (missing)      ->  --ssb 516   (also DU-printed; not set anywhere before)
set -e
GNB_IP="$(cat /shared/gnb-ip.txt)"
export POD_IP=$(awk 'END{print $1}' /etc/hosts)
echo "OAI NR-UE: POD_IP=${POD_IP} connecting to gNB rfsim server at ${GNB_IP} (discovered fresh this start)"
mkdir -p /tmp/conf
cp /opt/oai-nr-ue/etc/ue.conf /tmp/conf/ue.conf
# Subscriber settings already live in ue.conf; keep key material out of argv.
chmod 600 /tmp/conf/ue.conf
exec /opt/oai-nr-ue/bin/nr-uesoftmodem -O /tmp/conf/ue.conf --rfsim -r 106 --numerology 1 --band 78 \
  -C 3450720000 --ssb 516 --rfsimulator.serveraddr "${GNB_IP}"
