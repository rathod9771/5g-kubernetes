#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring --create-namespace --version 91.4.1 -f "$ROOT/monitoring/kube-prometheus-stack-values.yaml" --wait --timeout 20m
kubectl apply -f "$ROOT/monitoring/prometheus-nodeport.yaml"
date -Is > "$STATE/05-monitoring.ok"
exec bash "$ROOT/installer/stages/06-dashboard.sh"
