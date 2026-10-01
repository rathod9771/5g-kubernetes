#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${ROOT}/installer"
source "${INSTALLER}/lib/common.sh"
need_sudo
mkdir -p "${INSTALLER}/state" "${INSTALLER}/logs"
echo "============================================================"
echo "  5G KUBERNETES ORCHESTRATOR — ONE COMMAND INSTALLER"
echo "============================================================"
run_stage "00-host" "${INSTALLER}/stages/00-host.sh"
run_stage "01-kubernetes" "${INSTALLER}/stages/01-kubernetes.sh"
run_stage "02-addons" "${INSTALLER}/stages/02-addons.sh"
run_stage "03-osm" "${INSTALLER}/stages/03-osm.sh"
run_stage "04-platform" "${INSTALLER}/stages/04-platform.sh"
run_stage "05-dashboard" "${INSTALLER}/stages/05-dashboard.sh"
run_stage "06-validate" "${INSTALLER}/stages/06-validate.sh"
echo
echo "============================================================"
echo "  INSTALLATION COMPLETE"
echo "============================================================"
"${INSTALLER}/stages/07-access.sh"
