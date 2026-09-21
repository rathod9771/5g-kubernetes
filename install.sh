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

# ---- 6. RAN Selector dashboard ----
log_info "Checking the RAN Selector dashboard..."
if systemd_service_active "ran-selector.service"; then
  log_ok "ran-selector.service already running — reusing it (not touching venv or unit file)"
else
  log_info "Setting up the dashboard's Python environment..."
  python3 -m venv "${REPO_ROOT}/ran-selector/venv" \
    || fail "Failed to create venv" "Install python3-venv: sudo apt install python3-venv"
  "${REPO_ROOT}/ran-selector/venv/bin/pip" install --quiet --upgrade pip
  "${REPO_ROOT}/ran-selector/venv/bin/pip" install --quiet -r "${REPO_ROOT}/ran-selector/requirements.txt" \
    || fail "Failed to install ran-selector's Python dependencies"
  log_ok "Dashboard venv ready"

  log_info "Installing ran-selector.service..."
  sed \
    -e "s#PLACEHOLDER_USER#$(id -un)#g" \
    -e "s#PLACEHOLDER_REPO_ROOT#${REPO_ROOT}#g" \
    -e "s#PLACEHOLDER_KUBECONFIG_PATH#${KUBECONFIG_PATH}#g" \
    "${REPO_ROOT}/deploy/ran-selector.service" | sudo tee /etc/systemd/system/ran-selector.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now ran-selector.service \
    || fail "Failed to start ran-selector.service" "Check: journalctl -u ran-selector -n 50"
  log_ok "ran-selector.service installed and started"
fi

log_info "Checking the dashboard responds..."
sleep 2
if service_reachable "http://localhost:${DASHBOARD_PORT:-8090}/api/scenarios" 10; then
  log_ok "Dashboard responding on port ${DASHBOARD_PORT:-8090}"
else
  log_warn "Dashboard not yet responding — it may still be starting. Check: journalctl -u ran-selector -n 50"
fi

# ---- 7. Layer 3 autonomous watcher ----
log_info "Checking the Layer 3 watcher..."
if systemd_service_active "layer3-watcher.service"; then
  log_ok "layer3-watcher.service already running — reusing it"
else
  log_info "Installing layer3-watcher.service..."
  sed \
    -e "s#PLACEHOLDER_USER#$(id -un)#g" \
    -e "s#PLACEHOLDER_REPO_ROOT#${REPO_ROOT}#g" \
    "${REPO_ROOT}/layer3-autonomous/layer3-watcher.service" | sudo tee /etc/systemd/system/layer3-watcher.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now layer3-watcher.service \
    || fail "Failed to start layer3-watcher.service" "Check: journalctl -u layer3-watcher -n 50"
  log_ok "layer3-watcher.service installed and started"
fi

echo ""
echo "=================================================="
log_ok "Install complete."
echo "=================================================="
echo ""
echo "Next: ./orchestrator.sh to deploy a RAN scenario, or ./status.sh"
echo "to check the current state of everything above."
