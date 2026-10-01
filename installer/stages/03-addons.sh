#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
if ! command -v helm >/dev/null; then curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | sudo bash; fi
helm repo add longhorn https://charts.longhorn.io >/dev/null 2>&1 || true
helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
helm repo add rancher-latest https://releases.rancher.com/server-charts/latest >/dev/null 2>&1 || true
helm repo add istio https://istio-release.storage.googleapis.com/charts >/dev/null 2>&1 || true
helm repo update >/dev/null
kubectl apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset-thick.yml
helm upgrade --install longhorn longhorn/longhorn -n longhorn-system --create-namespace --set persistence.defaultClassReplicaCount=1 --wait --timeout 15m
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true --wait --timeout 10m
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace --set controller.service.type=NodePort --set controller.service.nodePorts.http=31225 --set controller.service.nodePorts.https=30843 --wait --timeout 10m
helm upgrade --install rancher rancher-latest/rancher -n cattle-system --create-namespace --set hostname="rancher.$OSM_BASE_DOMAIN" --set bootstrapPassword="\${RANCHER_BOOTSTRAP_PASSWORD:-admin123456}" --set replicas=1 --wait --timeout 15m
helm upgrade --install istio-base istio/base -n istio-system --create-namespace --wait --timeout 10m
helm upgrade --install istiod istio/istiod -n istio-system --wait --timeout 10m
date -Is > "$STATE/03-addons.ok"
exec bash "$ROOT/installer/stages/04-osm.sh"
