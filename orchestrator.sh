#!/usr/bin/env bash
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${REPO_ROOT}/scripts/common.sh"

clear 2>/dev/null || true
echo "========================================"
echo "       5G ORCHESTRATOR"
echo "========================================"
echo ""

echo "Select deployment architecture:"
echo ""
echo "  1) Centralized RAN (C-RAN)"
echo "  2) O-RAN (CU/DU split over F1)"
echo "  3) Cloud-RAN"
echo "  4) v-C-RAN (autoscaling CU)"
echo "  5) H-CRAN (macro + small cell)"
echo "  6) F-RAN (edge breakout — no RAN stack choice, adds alongside any RAN)"
echo ""
read -rp "Choice [1-6]: " ARCH_CHOICE

case "$ARCH_CHOICE" in
  1) ARCH_KEY="cran"; ARCH_NAME="Centralized RAN" ;;
  2) ARCH_KEY="oran"; ARCH_NAME="O-RAN" ;;
  3) ARCH_KEY="cloudran"; ARCH_NAME="Cloud-RAN" ;;
  4) ARCH_KEY="vcran"; ARCH_NAME="v-C-RAN" ;;
  5) ARCH_KEY="hcran"; ARCH_NAME="H-CRAN" ;;
  6) ARCH_KEY="fran"; ARCH_NAME="F-RAN" ;;
  *) fail "Invalid choice: '${ARCH_CHOICE}'" "Enter a number from 1 to 6" ;;
esac

if [ "$ARCH_KEY" = "fran" ]; then
  SCENARIO_KEY="fran"
  STACK_NAME="—"
else
  echo ""
  echo "Select RAN implementation:"
  echo ""
  echo "  1) OpenAirInterface (OAI)"
  echo "  2) srsRAN"
  echo ""
  read -rp "Choice [1-2]: " STACK_CHOICE

  case "$STACK_CHOICE" in
    1) STACK_KEY="oai"; STACK_NAME="OpenAirInterface" ;;
    2) STACK_KEY="srsran"; STACK_NAME="srsRAN" ;;
    *) fail "Invalid choice: '${STACK_CHOICE}'" "Enter 1 or 2" ;;
  esac
  SCENARIO_KEY="${ARCH_KEY}-${STACK_KEY}"
fi

echo ""
echo "========================================"
echo "  Architecture: ${ARCH_NAME}"
echo "  RAN stack:    ${STACK_NAME}"
echo "  Scenario key: ${SCENARIO_KEY}"
echo "========================================"
echo ""
if [ "$ARCH_KEY" != "fran" ]; then
  echo "This will terminate whatever non-additive RAN is currently running"
  echo "and instantiate ${SCENARIO_KEY} in its place (a real OSM operation,"
  echo "typically 1-3 minutes)."
else
  echo "F-RAN is additive -- it deploys alongside whatever RAN is already"
  echo "running rather than replacing it."
fi
echo ""
read -rp "Deploy? [y/N]: " CONFIRM

case "$CONFIRM" in
  [yY]|[yY][eE][sS]) ;;
  *) log_info "Cancelled — nothing was deployed."; exit 0 ;;
esac

echo ""
exec "${REPO_ROOT}/deploy.sh" "$SCENARIO_KEY"
