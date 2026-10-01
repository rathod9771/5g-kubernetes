#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg git jq python3 python3-pip python3-venv build-essential conntrack socat ebtables ethtool iproute2 iptables net-tools open-iscsi nfs-common chrony rsync unzip tar wget
sudo swapoff -a
sudo sed -ri 's/^([^#].*[[:space:]]swap[[:space:]].*)$/#\1/' /etc/fstab || true
for m in overlay br_netfilter sctp; do sudo modprobe "$m"; done
sudo tee /etc/modules-load.d/5g-kubernetes.conf >/dev/null <<'EOF'
overlay
br_netfilter
sctp
EOF
sudo tee /etc/sysctl.d/99-5g-kubernetes.conf >/dev/null <<'EOF'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
sudo sysctl --system >/dev/null
if ! command -v containerd >/dev/null; then sudo apt-get install -y containerd; fi
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml >/dev/null
sudo sed -ri 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl enable --now containerd
sudo systemctl enable --now open-iscsi 2>/dev/null || true
sudo systemctl enable --now chrony 2>/dev/null || sudo systemctl enable --now chronyd 2>/dev/null || true
date -Is > "$STATE/01-host.ok"
exec bash "$ROOT/installer/stages/02-kubernetes.sh"
