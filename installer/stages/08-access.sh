#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
echo
echo "============================================================"
echo " INSTALLATION COMPLETE"
echo "============================================================"
echo "Dashboard : http://$HOST_IP:\${DASHBOARD_PORT:-8090}"
echo "Grafana   : http://$HOST_IP:\${GRAFANA_NODEPORT:-31998}"
echo "Prometheus: http://$HOST_IP:\${PROMETHEUS_NODEPORT:-30990}"
echo "OSM GUI   : https://gui.$OSM_BASE_DOMAIN:\${OSM_HTTPS_PORT:-30843}"
echo "OSM NBI   : https://nbi.$OSM_BASE_DOMAIN:\${OSM_HTTPS_PORT:-30843}"
echo
echo "Next:"
echo "  ./status.sh"
echo "  ./deploy.sh --list"
echo "  ./orchestrator.sh"
