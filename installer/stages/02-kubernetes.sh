#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
if ! command -v kubeadm >/dev/null || ! command -v kubectl >/dev/null || ! command -v kubelet >/dev/null; then
  sudo mkdir -p /etc/apt/keyrings
  curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | sudo gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
  echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y kubelet kubeadm kubectl
  sudo apt-mark hold kubelet kubeadm kubectl
fi
mkdir -p "$HOME/.kube"
if ! kubectl cluster-info >/dev/null 2>&1; then
  sudo kubeadm init --pod-network-cidr=10.244.0.0/16
fi
if [ ! -f "$KUBECONFIG" ]; then sudo cp /etc/kubernetes/admin.conf "$KUBECONFIG"; sudo chown "$(id -u):$(id -g)" "$KUBECONFIG"; fi
export KUBECONFIG
kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml
kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>/dev/null || true
kubectl taint nodes --all node-role.kubernetes.io/master- 2>/dev/null || true
sudo mkdir -p /etc/systemd/system/kubelet.service.d
printf '[Service]\nEnvironment="KUBELET_EXTRA_ARGS=--max-pods=%s"\n' "${KUBELET_MAX_PODS:-200}" | sudo tee /etc/systemd/system/kubelet.service.d/20-5g-max-pods.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart kubelet
cp "$KUBECONFIG" "${OSM_KUBECONFIG_PATH:-$HOME/osm-kubeconfig.yaml}"
for i in {1..90}; do kubectl get nodes --no-headers 2>/dev/null | awk '$2=="Ready"{x=1} END{exit !x}' && break; sleep 5; done
kubectl get nodes
date -Is > "$STATE/02-kubernetes.ok"
exec bash "$ROOT/installer/stages/03-addons.sh"
