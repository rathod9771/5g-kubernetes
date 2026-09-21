#!/usr/bin/env bash
# ==============================================================================
# preflight.sh — checks this machine can actually run the platform before
# install.sh touches anything. Read-only: makes no changes.
# ==============================================================================
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/scripts/common.sh"

FAILED=0
WARNED=0

check_fail() { log_error "$1"; FAILED=1; }
check_warn() { log_warn "$1"; WARNED=1; }

echo "=================================================="
echo "  5G Kubernetes Orchestrator — Preflight Check"
echo "=================================================="
echo ""

# ---- OS ----
log_info "Checking OS..."
if [ -f /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" = "ubuntu" ] && [[ "${VERSION_ID:-}" == 24.* ]]; then
    log_ok "Ubuntu ${VERSION_ID} (this platform was built and verified on 24.04)"
  else
    check_warn "Detected ${PRETTY_NAME:-unknown OS} — this platform was built and verified on Ubuntu 24.04. Other distros/versions are untested, not necessarily incompatible."
  fi
else
  check_warn "Could not read /etc/os-release — unable to confirm OS"
fi

# ---- CPU ----
log_info "Checking CPU..."
CPU_CORES="$(nproc 2>/dev/null || echo 0)"
if [ "$CPU_CORES" -ge 8 ]; then
  log_ok "${CPU_CORES} cores (reference machine: 8)"
elif [ "$CPU_CORES" -ge 4 ]; then
  check_warn "${CPU_CORES} cores detected — the reference machine used 8. Fewer than 8 may still work but expect slower instantiate/terminate operations."
else
  check_fail "${CPU_CORES} cores detected — fewer than 4 is very likely insufficient for Kubernetes + OSM + a 5G core + RAN simultaneously"
fi

# ---- Memory ----
log_info "Checking memory..."
MEM_GB="$(free -g 2>/dev/null | awk '/^Mem:/{print $2}')"
MEM_GB="${MEM_GB:-0}"
if [ "$MEM_GB" -ge 24 ]; then
  log_ok "${MEM_GB} GB RAM (reference machine: 31 GB)"
elif [ "$MEM_GB" -ge 16 ]; then
  check_warn "${MEM_GB} GB RAM detected — workable, but this stack (K8s + OSM + core + RAN + monitoring) has been observed to trigger systemd-oomd under combined memory pressure below ~24 GB. Consider disabling Rancher's own monitoring stack if you add it."
else
  check_fail "${MEM_GB} GB RAM detected — below 16 GB, Kubernetes + OSM + a running 5G core/RAN stack is very likely to be OOM-killed"
fi

# ---- Disk ----
log_info "Checking disk space..."
DISK_FREE_GB="$(df -BG "$HOME" 2>/dev/null | awk 'NR==2{gsub("G","",$4); print $4}')"
DISK_FREE_GB="${DISK_FREE_GB:-0}"
if [ "$DISK_FREE_GB" -ge 60 ]; then
  log_ok "${DISK_FREE_GB} GB free on $HOME"
elif [ "$DISK_FREE_GB" -ge 30 ]; then
  check_warn "${DISK_FREE_GB} GB free — workable for one RAN scenario at a time, but container images (OSM, open5gs, OAI/srsRAN) and Longhorn volumes add up fast with more than one"
else
  check_fail "${DISK_FREE_GB} GB free — likely insufficient; container images alone for this stack typically exceed 15-20 GB"
fi

# ---- Required commands ----
log_info "Checking required commands..."
REQUIRED_CMDS=(kubectl helm git curl python3 jq)
for cmd in "${REQUIRED_CMDS[@]}"; do
  if command_exists "$cmd"; then
    log_ok "$cmd found ($(command -v "$cmd"))"
  else
    check_fail "$cmd not found — install it before continuing"
  fi
done

# containerd or docker (either is acceptable)
if command_exists containerd || command_exists docker; then
  log_ok "Container runtime found ($(command_exists containerd && echo containerd || echo docker))"
else
  check_fail "Neither containerd nor docker found — a container runtime is required"
fi

# ---- Kubernetes cluster (optional at preflight time — install.sh can bootstrap it) ----
log_info "Checking for an existing Kubernetes cluster..."
CLUSTER_REACHABLE=0
if command_exists kubectl && kubectl version --client >/dev/null 2>&1; then
  if kubectl cluster-info >/dev/null 2>&1; then
    CLUSTER_REACHABLE=1
    K8S_VERSION="$(kubectl version -o json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("serverVersion",{}).get("gitVersion","unknown"))' 2>/dev/null || echo unknown)"
    log_ok "Existing cluster reachable, server version: ${K8S_VERSION} — install.sh will reuse it, not reinstall"
  else
    log_info "kubectl installed but no cluster reachable yet — install.sh will bootstrap one with kubeadm"
  fi
else
  log_info "kubectl not yet configured — install.sh will bootstrap Kubernetes with kubeadm"
fi

# ---- Required kernel modules ----
log_info "Checking required kernel modules..."
REQUIRED_MODULES=(overlay br_netfilter sctp)
for mod in "${REQUIRED_MODULES[@]}"; do
  if lsmod 2>/dev/null | grep -q "^${mod} " || modinfo "$mod" >/dev/null 2>&1; then
    log_ok "Kernel module '$mod' available"
  else
    check_fail "Kernel module '$mod' not available — required (sctp specifically for NGAP/PFCP; the RAN cannot reach the core without it)"
  fi
done

# ---- IPv6 (this project disabled it; document, don't force) ----
log_info "Checking IPv6 status..."
IPV6_DISABLED="$(sysctl -n net.ipv6.conf.all.disable_ipv6 2>/dev/null || echo unknown)"
if [ "$IPV6_DISABLED" = "1" ]; then
  log_ok "IPv6 disabled system-wide (matches the reference configuration)"
else
  check_warn "IPv6 is enabled. On the reference machine, enabled IPv6 silently broke raw.githubusercontent.com downloads during OSM install (fix: 'sysctl -w net.ipv6.conf.all.disable_ipv6=1'). Not fatal — worth knowing before an install step fails confusingly."
fi

# ---- Ports ----
# A port already listening isn't automatically a conflict: 6443 is
# expected once Kubernetes is up, and 8090 is expected once
# ran-selector.service is already running (both mean install.sh has
# something to reuse, not something to fix). Only warn when the
# listener genuinely isn't one of this platform's own processes.
log_info "Checking required ports..."

check_port() {
  local port="$1" expected_desc="$2" expected_pattern="$3"
  if ! command_exists ss || ! ss -ltnp 2>/dev/null | grep -q ":${port} "; then
    log_ok "Port ${port} free"
    return
  fi
  local listener
  listener="$(ss -ltnp 2>/dev/null | grep ":${port} " | head -1)"
  if [ -n "$expected_pattern" ] && echo "$listener" | grep -qE "$expected_pattern"; then
    log_ok "Port ${port} in use by ${expected_desc} (expected — install.sh will reuse it)"
  else
    check_warn "Port ${port} is in use by something unexpected (${listener}). On the reference machine, an unrelated k3s install squatting on 6443 caused chronic instability until it was found and stopped — worth confirming this isn't the same kind of conflict."
  fi
}

if [ "$CLUSTER_REACHABLE" -eq 1 ]; then
  log_ok "Port 6443 in use by the Kubernetes API server (already confirmed reachable above — expected)"
else
  check_port 6443 "the Kubernetes API server" "kube-apiserver|kubelet"
fi
check_port 8090 "ran-selector.service (the dashboard)" "python3"
check_port 30843 "OSM's ingress" ""

# ---- Permissions ----
log_info "Checking permissions..."
if [ -w "$HOME" ]; then
  log_ok "$HOME is writable"
else
  check_fail "$HOME is not writable"
fi
if sudo -n true 2>/dev/null; then
  log_ok "Passwordless sudo available"
else
  check_warn "sudo requires a password — install.sh will prompt when it needs elevated commands (kubeadm, systemd units, sysctl)"
fi

# ---- Required repository directories ----
log_info "Checking repository structure..."
for dir in osm-packages helm ran-selector monitoring layer3-autonomous scripts config; do
  if [ -d "${REPO_ROOT}/${dir}" ]; then
    log_ok "${dir}/ present"
  else
    check_fail "${dir}/ missing — repository clone may be incomplete"
  fi
done

echo ""
echo "=================================================="
if [ "$FAILED" -eq 1 ]; then
  echo "  PREFLIGHT: FAILED"
  echo "=================================================="
  echo "Fix the [ERROR] items above before running ./install.sh"
  exit 1
elif [ "$WARNED" -eq 1 ]; then
  echo "  PREFLIGHT: PASSED WITH WARNINGS"
  echo "=================================================="
  echo "Review the [WARN] items above — install.sh will proceed, but they're worth reading first"
  exit 0
else
  echo "  PREFLIGHT: PASSED"
  echo "=================================================="
  exit 0
fi
