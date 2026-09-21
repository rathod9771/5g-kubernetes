#!/usr/bin/env bash
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${REPO_ROOT}/scripts/common.sh"
load_config

echo "=================================================="
echo "  5G Kubernetes Orchestrator — Install"
echo "=================================================="
echo ""

log_info "Checking Kubernetes..."
if kubectl_available && kubectl cluster-info >/dev/null 2>&1; then
  log_ok "Kubernetes already running — reusing it"
else
  log_info "No cluster reachable — bootstrapping with kubeadm"
  command_exists kubeadm || fail "kubeadm not found" \
    "Install it first: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/"

  log_info "Raising kubelet's max pod count to ${KUBELET_MAX_PODS:-200}..."
  sudo mkdir -p /etc/systemd/system/kubelet.service.d
  echo "[Service]
Environment=\"KUBELET_EXTRA_ARGS=--max-pods=${KUBELET_MAX_PODS:-200}\"" | sudo tee /etc/systemd/system/kubelet.service.d/20-max-pods.conf >/dev/null
  sudo systemctl daemon-reload

  log_info "Running kubeadm init (this can take a few minutes)..."
  sudo kubeadm init --pod-network-cidr=10.244.0.0/16 || fail "kubeadm init failed" \
    "Check 'sudo kubeadm init --pod-network-cidr=10.244.0.0/16' output above for the specific error" \
    "Common causes: swap not disabled, port 6443 already in use, insufficient resources"

  mkdir -p "$HOME/.kube"
  sudo cp -f /etc/kubernetes/admin.conf "$HOME/.kube/config"
  sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"

  log_info "Installing Flannel CNI..."
  kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml \
    || fail "Flannel install failed" "Check network connectivity to raw.githubusercontent.com" "If IPv6 is enabled, it has broken this download before on this project's reference machine -- try: sudo sysctl -w net.ipv6.conf.all.disable_ipv6=1"

  log_info "Untainting control-plane node (single-node cluster)..."
  kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>/dev/null || true

  log_ok "Kubernetes bootstrapped"
fi

log_info "Checking Longhorn..."
if namespace_exists "longhorn-system" && pods_ready_in_namespace "longhorn-system"; then
  log_ok "Longhorn already running — reusing it"
else
  log_info "Installing Longhorn..."
  command_exists helm || fail "helm not found" "Install Helm 3 first: https://helm.sh/docs/intro/install/"
  helm repo add longhorn https://charts.longhorn.io >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1
  helm upgrade --install longhorn longhorn/longhorn \
    --namespace longhorn-system --create-namespace \
    --set persistence.defaultClassReplicaCount=1 \
    || fail "Longhorn install failed" "Check: kubectl get pods -n longhorn-system" "Requires open-iscsi on the host: sudo apt install open-iscsi"
  log_ok "Longhorn installed"
fi

log_info "Checking cert-manager..."
if crd_exists "certificates.cert-manager.io"; then
  log_ok "cert-manager already installed — reusing it"
else
  log_info "Installing cert-manager..."
  kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.5/cert-manager.yaml \
    || fail "cert-manager install failed" "Check network connectivity" "OSM's chart requires cert-manager.io/v1 CRDs, so this must succeed before OSM can be installed"
  log_ok "cert-manager installed"
fi

log_info "Checking OSM..."
if namespace_exists "osm" && pods_ready_in_namespace "osm"; then
  log_ok "OSM already running — reusing it"
else
  echo ""
  echo "  ================================================"
  echo "  USER ACTION REQUIRED: OSM is not installed"
  echo "  ================================================"
  echo ""
  echo "  This project's OSM install is a multi-step process with"
  echo "  environment-specific patches (see docs/OSM19_INSTALL.md for"
  echo "  the exact, verified steps). It hasn't been re-proven as a"
  echo "  one-shot script on a fresh machine, so rather than run"
  echo "  something unverified against your cluster, this stops here."
  echo ""
  echo "  Follow docs/OSM19_INSTALL.md, then re-run ./install.sh to"
  echo "  continue with monitoring and the rest of the stack."
  echo ""
  exit 1
fi

log_info "Checking the monitoring stack..."
if helm_release_exists "kube-prometheus-stack" "monitoring"; then
  log_ok "kube-prometheus-stack already installed — reusing it"
else
  log_info "Installing kube-prometheus-stack..."
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1
  helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
    -n monitoring --create-namespace --version 91.4.1 \
    -f "${REPO_ROOT}/monitoring/kube-prometheus-stack-values.yaml" \
    || fail "kube-prometheus-stack install failed" "Check: kubectl get pods -n monitoring"
  log_ok "kube-prometheus-stack installed"
fi

log_info "Checking the Prometheus NodePort..."
if kubectl get svc kube-prometheus-stack-prometheus-nodeport -n monitoring >/dev/null 2>&1; then
  log_ok "Prometheus NodePort already exposed"
else
  kubectl apply -f "${REPO_ROOT}/monitoring/prometheus-nodeport.yaml" \
    || fail "Failed to apply monitoring/prometheus-nodeport.yaml"
  log_ok "Prometheus NodePort exposed"
fi

echo ""
echo "=================================================="
log_ok "Install complete."
echo "=================================================="
echo ""
echo "Next: ./orchestrator.sh to deploy a RAN scenario, or ./status.sh"
echo "to check the current state of everything above."
