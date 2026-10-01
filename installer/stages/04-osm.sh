#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
export OSM_HOME_DIR="$HOME/.osm"
export CREDENTIALS_DIR="$OSM_HOME_DIR/.credentials"
mkdir -p "$CREDENTIALS_DIR" "$ROOT/installer/runtime"
if kubectl get ns osm >/dev/null 2>&1 && kubectl -n osm get deployment nbi >/dev/null 2>&1; then
  echo "OSM namespace already exists; reusing it"
  date -Is > "$STATE/04-osm.ok"
  exec bash "$ROOT/installer/stages/05-monitoring.sh"
fi
if ! command -v age >/dev/null; then
  tmp=$(mktemp -d)
  curl -fL --retry 5 -o "$tmp/age.tgz" https://github.com/FiloSottile/age/releases/download/v1.1.0/age-v1.1.0-linux-amd64.tar.gz
  tar -xzf "$tmp/age.tgz" -C "$tmp"
  sudo install -m 0755 "$tmp/age/age" "$tmp/age/age-keygen" /usr/local/bin/
  rm -rf "$tmp"
fi
if [ ! -f "$CREDENTIALS_DIR/age.mgmt.key" ]; then
  age-keygen -o "$CREDENTIALS_DIR/age.mgmt.key"
  age-keygen -y "$CREDENTIALS_DIR/age.mgmt.key" > "$CREDENTIALS_DIR/age.mgmt.pub"
  chmod 600 "$CREDENTIALS_DIR/age.mgmt.key"
fi
OSM="$ROOT/installer/runtime/osm-devops"
if [ ! -d "$OSM/.git" ]; then
  git clone --depth 1 --branch v19.0 https://osm.etsi.org/gitlab/osm/devops.git "$OSM"
fi
[ -x "$OSM/installers/30-deploy-mgmt-cluster.sh" ] || die "OSM v19 installer missing 30-deploy-mgmt-cluster.sh"
[ -x "$OSM/installers/40-deploy-osm.sh" ] || die "OSM v19 installer missing 40-deploy-osm.sh"
# The reference installation requires OSM's own Prometheus/Grafana to be disabled.
if grep -q 'OSM_HELM_OPTS=""' "$OSM/installers/40-deploy-osm.sh"; then
  grep -q 'prometheus.enabled=false' "$OSM/installers/40-deploy-osm.sh" || \
    sed -i '/OSM_HELM_OPTS=""/a OSM_HELM_OPTS="\${OSM_HELM_OPTS} --set prometheus.enabled=false --set grafana.enabled=false"' "$OSM/installers/40-deploy-osm.sh"
fi
if [ -f "$OSM/installers/40-deploy-osm.sh" ]; then
  grep -q 'OSM_BASE_DOMAIN="$OSM_BASE_DOMAIN"' "$OSM/installers/40-deploy-osm.sh" || \
    sed -i '/OSM_HELM_OPTS="\${OSM_HELM_OPTS}/a OSM_BASE_DOMAIN="$OSM_BASE_DOMAIN"' "$OSM/installers/40-deploy-osm.sh"
fi
export OSM_BASE_DOMAIN
export GIT_BASE_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080"
export GIT_BASE_USERNAME="osm-developer"
export FLEET_REPO_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/fleet-osm.git"
export SW_CATALOGS_REPO_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/sw-catalogs-osm.git"
export FLEET_REPO_GIT_USERNAME="osm-developer"
export OSM_HELM_TIMEOUT=20m
cd "$OSM/installers"
./30-deploy-mgmt-cluster.sh
if [ -x gitea/90-provision-gitea-for-osm.sh ]; then
  export GITEA_HTTP_URL="http://$HOST_IP:31225"
  export GITEA_STD_USERNAME="osm-developer"
  ./gitea/90-provision-gitea-for-osm.sh || true
fi
source "$CREDENTIALS_DIR/git_environment.rc" 2>/dev/null || true
source "$CREDENTIALS_DIR/gitea_environment.rc" 2>/dev/null || true
export OSM_BASE_DOMAIN
export GIT_BASE_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080"
export GIT_BASE_USERNAME="osm-developer"
export FLEET_REPO_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/fleet-osm.git"
export SW_CATALOGS_REPO_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/sw-catalogs-osm.git"
export FLEET_REPO_GIT_USERNAME="osm-developer"
export FLEET_REPO_GIT_USER_PASS="\${GIT_TOKEN:-}"
./40-deploy-osm.sh
kubectl -n osm create secret generic grafana --from-literal=admin-user=admin --from-literal=admin-password=admin --dry-run=client -o yaml | kubectl apply -f -
kubectl -n flux-system patch gitrepository flux-system --type=merge -p '{"spec":{"url":"http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/fleet-osm.git"}}' 2>/dev/null || true
kubectl -n osm get pods
date -Is > "$STATE/04-osm.ok"
exec bash "$ROOT/installer/stages/05-monitoring.sh"
