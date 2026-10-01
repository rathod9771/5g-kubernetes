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

if [ ! -f "$KUBECONFIG" ]; then
  sudo cp /etc/kubernetes/admin.conf "$KUBECONFIG"
  sudo chown "$(id -u):$(id -g)" "$KUBECONFIG"
fi
export KUBECONFIG

kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml
kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>/dev/null || true
kubectl taint nodes --all node-role.kubernetes.io/master- 2>/dev/null || true

sudo mkdir -p /etc/systemd/system/kubelet.service.d
printf '[Service]\nEnvironment="KUBELET_EXTRA_ARGS=--max-pods=%s"\n' "${KUBELET_MAX_PODS:-200}" |
  sudo tee /etc/systemd/system/kubelet.service.d/20-5g-max-pods.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart kubelet

cp "$KUBECONFIG" "${OSM_KUBECONFIG_PATH:-$HOME/osm-kubeconfig.yaml}"

dump_control_plane_diagnostics() {
  warn "Kubernetes control-plane validation failed; collecting diagnostics"
  kubectl get nodes -o wide 2>&1 || true
  kubectl get pods -n kube-system -o wide 2>&1 || true
  kubectl get events -A --sort-by=.lastTimestamp 2>&1 | tail -100 || true
  sudo crictl --runtime-endpoint unix:///var/run/containerd/containerd.sock ps -a 2>&1 || true
  sudo journalctl -u kubelet --since "10 minutes ago" --no-pager 2>&1 | tail -200 || true
}

wait_for_node_ready() {
  info "Waiting for Kubernetes node to become Ready"
  for i in {1..90}; do
    if kubectl get nodes --no-headers 2>/dev/null | awk '$2=="Ready"{x=1} END{exit !x}'; then
      ok "Kubernetes node is Ready"
      return 0
    fi
    sleep 5
  done
  dump_control_plane_diagnostics
  die "Kubernetes node did not become Ready within 450 seconds"
}

control_plane_ready_once() {
  local component pod_name

  kubectl get --raw='/readyz' >/dev/null 2>&1

  for component in etcd kube-apiserver kube-controller-manager kube-scheduler; do
    pod_name="$(kubectl get pods -n kube-system -l "component=$component" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    [ -n "$pod_name" ] || return 1
    kubectl get pod -n kube-system "$pod_name" -o jsonpath='{.status.phase}' 2>/dev/null | grep -qx 'Running' || return 1
  done

  kubectl get pods -n kube-system -l k8s-app=kube-proxy -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null |
    grep -q '^Running$' || return 1

  kubectl get nodes --no-headers 2>/dev/null | awk '$2=="Ready"{x=1} END{exit !x}'
}

wait_for_control_plane_stable() {
  local attempt
  info "Waiting for etcd, API server, controller-manager, scheduler, kube-proxy and /readyz to stabilize"

  for attempt in {1..90}; do
    if control_plane_ready_once; then
      ok "Kubernetes control plane is healthy; beginning stabilization checks"
      break
    fi
    sleep 5
  done

  if ! control_plane_ready_once; then
    dump_control_plane_diagnostics
    die "Kubernetes control plane did not become healthy"
  fi

  for attempt in {1..6}; do
    if ! control_plane_ready_once; then
      dump_control_plane_diagnostics
      die "Kubernetes control plane became unhealthy during stabilization"
    fi
    info "Control-plane stabilization check $attempt/6 passed"
    sleep 5
  done

  kubectl get nodes -o wide
  kubectl get pods -n kube-system -o wide
  kubectl get --raw='/readyz?verbose'
}

wait_for_node_ready
wait_for_control_plane_stable

date -Is > "$STATE/02-kubernetes.ok"
exec bash "$ROOT/installer/stages/03-addons.sh"
