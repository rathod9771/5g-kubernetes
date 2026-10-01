#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
echo "============================================================"
echo "  5G KUBERNETES ORCHESTRATOR — REPRODUCIBLE INSTALLER"
echo "============================================================"
source /etc/os-release
[ "$ID" = ubuntu ] || die "Ubuntu is required; detected $PRETTY_NAME"
[[ "$VERSION_ID" == 24.* ]] || warn "Reference platform is Ubuntu 24.04; detected $VERSION_ID"
cores=$(nproc); ram=$(free -g | awk '/^Mem:/{print $2}'); disk=$(df -BG "$HOME" | awk 'NR==2{gsub("G","",$4);print $4}')
[ "$cores" -ge 8 ] || warn "Recommended: 8+ CPU cores; found $cores"
[ "$ram" -ge 24 ] || warn "Recommended: 24+ GB RAM; found $ram GB"
[ "$disk" -ge 60 ] || warn "Recommended: 60+ GB free; found $disk GB"
sudo -v
mkdir -p "$ROOT/installer/state" "$ROOT/installer/logs" "$ROOT/installer/runtime"
if [ ! -f "$ROOT/config/global.env" ]; then cp "$ROOT/config/global.env.example" "$ROOT/config/global.env"; fi
source "$ROOT/config/global.env"
HOST_IP="${HOST_IP:-$(ip route get 1.1.1.1 2>/dev/null|awk '{print $7;exit}')}"
HOST_INTERFACE="${HOST_INTERFACE:-$(ip route get 1.1.1.1 2>/dev/null|awk '{print $5;exit}')}"
OSM_BASE_DOMAIN="${OSM_BASE_DOMAIN:-$HOST_IP.nip.io}"
export HOST_IP HOST_INTERFACE OSM_BASE_DOMAIN
if [ -f "$STATE/00-preflight.ok" ]; then exec bash "$ROOT/installer/stages/01-host.sh"; fi
printf '%s\n' "$HOST_IP" > "$STATE/host-ip"
exec bash "$ROOT/installer/stages/01-host.sh"
