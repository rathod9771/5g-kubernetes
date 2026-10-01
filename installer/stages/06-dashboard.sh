#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
python3 -m venv "$ROOT/ran-selector/venv"
"$ROOT/ran-selector/venv/bin/pip" install --quiet --upgrade pip
"$ROOT/ran-selector/venv/bin/pip" install --quiet -r "$ROOT/ran-selector/requirements.txt"
sed -e "s#PLACEHOLDER_USER#$(id -un)#g" -e "s#PLACEHOLDER_REPO_ROOT#$ROOT#g" -e "s#PLACEHOLDER_KUBECONFIG_PATH#$KUBECONFIG#g" "$ROOT/deploy/ran-selector.service" | sudo tee /etc/systemd/system/ran-selector.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ran-selector.service
sed -e "s#PLACEHOLDER_USER#$(id -un)#g" -e "s#PLACEHOLDER_REPO_ROOT#$ROOT#g" -e "s#PLACEHOLDER_PROMETHEUS_URL#http://localhost:${PROMETHEUS_NODEPORT:-30990}#g" -e "s#PLACEHOLDER_DASHBOARD_URL#http://localhost:${DASHBOARD_PORT:-8090}#g" -e "s#PLACEHOLDER_CHECK_INTERVAL_SECONDS#${LAYER3_CHECK_INTERVAL_SECONDS:-10}#g" -e "s#PLACEHOLDER_ACTION_COOLDOWN_SECONDS#${LAYER3_COOLDOWN_SECONDS:-120}#g" -e "s#PLACEHOLDER_BLER_THRESHOLD#${LAYER3_BLER_THRESHOLD:-0.05}#g" -e "s#PLACEHOLDER_FAILOVER_SCENARIO#${LAYER3_FAILOVER_SCENARIO:-hcran-oai}#g" "$ROOT/layer3-autonomous/layer3-watcher.service" | sudo tee /etc/systemd/system/layer3-watcher.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now layer3-watcher.service
date -Is > "$STATE/06-dashboard.ok"
exec bash "$ROOT/installer/stages/07-validate.sh"
