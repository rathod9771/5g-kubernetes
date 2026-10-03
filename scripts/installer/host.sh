#!/usr/bin/env bash
# Executed only by install.sh on the target Ubuntu host, never by static tests.
set -euo pipefail
case $- in *x*) set +x ;; esac
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. /etc/os-release
[[ "${ID}" == ubuntu && "${VERSION_ID}" == 24.04 ]] || {
  echo 'ERROR: submission bootstrap requires Ubuntu 24.04.' >&2; exit 1;
}
if [[ "${EUID}" == 0 ]]; then
  echo 'ERROR: run install.sh as the intended service user, with sudo available.' >&2; exit 1
fi
[[ "$(uname -m)" == x86_64 ]] || { echo 'ERROR: submission bootstrap requires amd64.' >&2; exit 1; }
sudo -v
sudo apt-get update
sudo apt-get install -y --no-upgrade ca-certificates curl gnupg git python3 python3-yaml python3-venv \
  python3-pip open-iscsi nfs-common conntrack socat iptables iproute2 containerd
K8S_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kubernetes"])' "$REPO_ROOT/config/reference-versions.json")"
HELM_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["helm"])' "$REPO_ROOT/config/reference-versions.json")"
sudo install -d -m 0755 /etc/apt/keyrings
key_file="$(mktemp)"
trap 'rm -f "$key_file"' EXIT
curl --fail --location --retry 3 https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key > "$key_file"
gpg --dearmor --batch --yes --output "${key_file}.gpg" "$key_file"
sudo install -m 0644 "${key_file}.gpg" /etc/apt/keyrings/kubernetes-apt-keyring.gpg
rm -f "${key_file}.gpg"
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
sudo apt-get update
for package in kubeadm kubelet kubectl; do
  installed="$(dpkg-query -W -f='${Version}' "$package" 2>/dev/null || :)"
  [[ "$installed" == "$K8S_VERSION-1.1" ]] || \
    sudo apt-get install -y "$package=$K8S_VERSION-1.1"
done
sudo apt-mark hold kubeadm kubelet kubectl
current="$(helm version --short 2>/dev/null || :)"
if [[ "$current" != "v${HELM_VERSION}"* ]]; then
  archive="$(mktemp -d)"
  trap 'rm -f "$key_file"; rm -rf "$archive"' EXIT
  curl --fail --location --retry 3 "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" -o "$archive/helm.tar.gz"
  curl --fail --location --retry 3 "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" -o "$archive/checksum"
  expected="$(awk '{print $1}' "$archive/checksum")"
  echo "$expected  $archive/helm.tar.gz" | sha256sum --check --status
  tar -xzf "$archive/helm.tar.gz" -C "$archive" linux-amd64/helm
  sudo install -m 0755 "$archive/linux-amd64/helm" /usr/local/bin/helm
fi
# Preserve an existing runtime configuration. A new host receives systemd cgroups.
if [[ ! -e /etc/kubernetes/admin.conf ]]; then
  containerd config default | sed 's/SystemdCgroup = false/SystemdCgroup = true/' \
    | sudo tee /etc/containerd/config.toml >/dev/null
fi
sudo systemctl enable --now containerd iscsid kubelet
if [[ ! -e /etc/kubernetes/admin.conf ]]; then
  sudo systemctl restart containerd
fi
# Match existing single-node kubeadm prerequisites without globally disabling IPv6.
sudo swapoff -a
sudo sed -i.bak '/^[^#].*[[:space:]]swap[[:space:]]/s/^/# installer: /' /etc/fstab
printf '%s\n' overlay br_netfilter | sudo tee /etc/modules-load.d/5g-kubernetes.conf >/dev/null
sudo modprobe overlay
sudo modprobe br_netfilter
printf '%s\n' 'net.ipv4.ip_forward=1' 'net.bridge.bridge-nf-call-iptables=1' \
  'net.bridge.bridge-nf-call-ip6tables=1' 'fs.inotify.max_user_watches=699050' \
  'fs.inotify.max_user_instances=10922' 'fs.inotify.max_queued_events=1398101' \
  | sudo tee /etc/sysctl.d/90-5g-kubernetes.conf >/dev/null
sudo sysctl --system >/dev/null
