# OSM Release 19 + FluxCD on an existing kubeadm cluster

Working install notes for `rclab-HP-406-G1-MT` (Ubuntu 24.04, 8-core i7-4790, 32 GB,
kubeadm v1.29 + flannel + multus, host `172.30.18.32`).

The upstream installer assumes it builds its own k3s cluster. Installing onto a
pre-existing kubeadm cluster that already runs Rancher, Istio, Longhorn, monitoring
and a 5G core breaks several of those assumptions. Everything below is what had to
change to make it work.

---

## Result

All 14 OSM pods Running in namespace `osm`; Flux bootstrapped against a local Gitea
and reconciling; the `free5gc` 5G stack unaffected throughout.

| Component | Notes |
|---|---|
| nbi, ro, lcm, mon, ngui | OSM core |
| kafka, mongodb-k8s (+arbiter), osm-postgresql | Data plane |
| osm-scheduler, osm-triggerer, osm-webserver, osm-statsd | Airflow |
| webhook-translator | |
| Prometheus, Grafana | **Disabled** — Rancher monitoring already provides both |

---

## Why not Charmed OSM

Ruled out before starting this path. `charmed_install.sh` hard-codes
`JUJU_VERSION=2.9` and requests `--agent-version=2.9.43` at bootstrap; the machine
has Juju 3.6.28. The major-version gap is not bridgeable, and Charmed OSM is a
Juju/charms orchestration model rather than the FluxCD GitOps model that was wanted
in the first place.

Two upstream bugs found while investigating, worth knowing if anyone revisits it:

- `charmed_install.sh:259` gates overlay generation behind `[ -v TAG ]`, but the
  default `TAG='14.0'` on line 257 sits inside an unrelated `if [ -v REGISTRY_URL ]`
  block. With no `--registry`, `TAG` is never set, `generate_images_overlay` never
  runs, and the deploy fails with `images-overlay.yaml not found`.
- The outer `install_osm.sh` does not forward `--tag` to `charmed_install.sh` at all,
  so passing it through the documented entry point has no effect.

LXD and a Juju controller (`osm-controller`, api-port 17070) were set up for this
path before it was abandoned. Both have since been removed:

```bash
juju destroy-controller osm-controller --destroy-all-models --no-prompt
sudo snap remove juju
sudo snap remove lxd
sudo iptables -t nat -D POSTROUTING -s 10.112.108.0/24 -o enp3s0 -j MASQUERADE
sudo netfilter-persistent save
```

That reclaimed ~2.5 GB.

---

## Prerequisite: broken IPv6

This cost the most time and explains a long run of unrelated-looking failures.

Downloads from GitHub failed at a different step on every run — `age`, `flux`,
`argo`, helm chart tarballs, `git clone` from ETSI — always with
`curl: (35) Recv failure: Connection reset by peer` or a gnutls handshake error.

The cause: IPv6 was configured but non-functional. `curl -6` to any host failed in
~1 ms with exit code 000, while `curl -4` succeeded every time. Whenever DNS
returned an AAAA record and curl picked it, the transfer died instantly. Hosts that
resolve IPv6-only (`raw.githubusercontent.com`) failed nearly always.

```bash
# diagnosis
curl -4 -sL -o /dev/null -w "%{http_code}\n" https://github.com/...   # 200
curl -6 -sL -o /dev/null -w "%{http_code}\n" https://github.com/...   # 000
```

```bash
sudo tee /etc/sysctl.d/99-disable-ipv6.conf > /dev/null << 'EOF'
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
sudo sysctl -p /etc/sysctl.d/99-disable-ipv6.conf
```

After this, downloads ran at 16–27 MB/s with no resets. The installer has no retry
logic on any of its dozens of `curl` calls, so a single transient failure aborts the
whole run — which is why this looked like many different problems.

**Note:** this file is required. It was briefly removed while diagnosing an unrelated
networking question and the download failures immediately returned. Nothing in the
cluster (flannel, kube-proxy, the 5G stack) depends on IPv6; disabling and re-enabling
it caused no lasting damage, though kube-proxy and flannel were restarted afterwards
as a precaution.

---

## Run the installer from a local clone

`install_osm.sh` re-clones the devops repo to `/tmp/osm-devops-XXXXXX` on every run,
so edits to any script are discarded — and the clone itself fails when ETSI's GitLab
drops a TLS connection. Clone once and run `full_install_osm.sh` directly:

```bash
git clone -b v19.0 https://osm.etsi.org/gitlab/osm/devops.git ~/osm-devops-local
cd ~/osm-devops-local/installers
```

`full_install_osm.sh:45` sets `OSM_DEVOPS="${OSM_DEVOPS:-"${HERE}/.."}"`, so running
it from the clone makes it use that clone. Steps can then be run individually
(`30-deploy-mgmt-cluster.sh`, `40-deploy-osm.sh`, …) instead of restarting from
scratch after every failure.

---

## Client tools installed manually

Each of these failed mid-download before the IPv6 fix. Kept here because a fresh run
may still want them present:

```bash
# age
curl -L --retry 5 -O https://github.com/FiloSottile/age/releases/download/v1.1.0/age-v1.1.0-linux-amd64.tar.gz
tar xzf age-v1.1.0-linux-amd64.tar.gz
sudo mv age/age age/age-keygen /usr/local/bin/ && sudo chmod +x /usr/local/bin/age*

# argo
curl -L --retry 5 -O https://github.com/argoproj/argo-workflows/releases/download/v3.5.7/argo-linux-amd64.gz
gunzip argo-linux-amd64.gz && chmod +x argo-linux-amd64
sudo mv ./argo-linux-amd64 /usr/local/bin/argo

# kustomize
curl --retry 5 -s "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh" | bash -s -- 5.4.3
sudo mv ./kustomize /usr/local/bin/
```

`flux` and `sops` installed cleanly from the installer once IPv6 was disabled.

---

## Gitea and the fleet repositories

Gitea deploys fine, but the repos Flux needs were never created — the `osm-cloner`
job died silently at "Creating Repositories". `90-provision-gitea-for-osm.sh` does
this, but needs `CREDENTIALS_DIR` set (otherwise it writes to `/gitea_tokens.rc` and
fails on permissions) and `GITEA_HTTP_URL` pointed somewhere the host can reach.

```bash
cd ~/osm-devops-local/installers/gitea
export OSM_HOME_DIR="$HOME/.osm"
export CREDENTIALS_DIR="$HOME/.osm/.credentials"
export GITEA_HTTP_URL="http://git.myexample.com:31225"   # ingress, see below
export GITEA_STD_USERNAME="osm-developer"
source ~/.osm/.credentials/git_environment.rc
export GITEA_STD_TOKEN="${GIT_TOKEN}"
./90-provision-gitea-for-osm.sh
```

This regenerates the standard-user token and rewrites the credentials files, so
re-source them afterwards.

Verify:

```bash
curl -s -H "Authorization: token ${GIT_TOKEN}" \
  http://git.myexample.com:31225/api/v1/user/repos | python3 -c \
  "import sys,json; print([r['name'] for r in json.load(sys.stdin)])"
# ['fleet-osm', 'sw-catalogs-osm']
```

### Reaching Gitea

Three services point at port 3000; the container only listens on **8080** and 2222.
The 3000 services (`osm-gitea-svc`, `gitea-direct-access`, `gitea-ext`) never answer
and should be ignored. Use:

- from the host: the nginx ingress, host `git.myexample.com` on NodePort 31225
- from inside the cluster: `gitea-http.gitea.svc.cluster.local:8080`

```bash
echo "172.30.18.32 git.myexample.com" | sudo tee -a /etc/hosts
```

---

## Flux bootstrap

`flux bootstrap` runs on the host but the resulting GitRepository is reconciled by a
controller inside the cluster, so the two need different URLs. Bootstrap against the
ingress, then patch the object to the in-cluster service name.

```bash
cd ~/osm-devops-local/installers
source ~/.osm/.credentials/git_environment.rc
source ~/.osm/.credentials/gitea_environment.rc
export FLEET_REPO_HTTP_URL="http://git.myexample.com:31225/osm-developer/fleet-osm.git"
export FLEET_REPO_GIT_USERNAME="osm-developer"
export FLEET_REPO_GIT_USER_PASS="${GIT_TOKEN}"
./30-deploy-mgmt-cluster.sh
```

The generated `git_environment.rc` exports `FLEET_REPO_URL`, but
`flux/scripts/mgmt-cluster-bootstrap.sh` reads `FLEET_REPO_HTTP_URL`. Export it
explicitly or bootstrap fails with `scheme "" is not supported`.

Then:

```bash
kubectl -n flux-system patch gitrepository flux-system --type=merge \
  -p '{"spec":{"url":"http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/fleet-osm.git"}}'
kubectl get gitrepository,kustomization -n flux-system   # both READY True
```

Adding `git.myexample.com` to CoreDNS also works and survives reconciliation better,
since kustomize-controller reverts manual patches to objects it manages:

```
hosts {
    172.30.18.32 git.myexample.com
    fallthrough
}
```

placed after `ready` in the `.:53` block of the `coredns` ConfigMap, then
`kubectl -n kube-system rollout restart deployment coredns`.

### age keys

`40-deploy-osm.sh` mounts a SOPS keypair that step 30 normally creates:

```bash
export CREDENTIALS_DIR="$HOME/.osm/.credentials"
age-keygen -o "${CREDENTIALS_DIR}/age.mgmt.key"
age-keygen -y "${CREDENTIALS_DIR}/age.mgmt.key" > "${CREDENTIALS_DIR}/age.mgmt.pub"
chmod 600 "${CREDENTIALS_DIR}/age.mgmt.key"
```

---

## cert-manager

OSM's chart creates `Certificate`, `Issuer` and `ClusterIssuer` resources. cert-manager
was not installed, but a partial install from ~2 months earlier had left cluster-scoped
RBAC and webhooks behind, which blocked a clean Helm install with
`invalid ownership metadata`.

One of those leftovers was a `validatingwebhookconfiguration` with
`failurePolicy: Fail` pointing at a service that no longer existed — worth removing on
its own account, since that can silently block API operations.

```bash
kubectl get clusterrole -o name | grep cert-manager | xargs -r kubectl delete
kubectl get clusterrolebinding -o name | grep cert-manager | xargs -r kubectl delete
kubectl delete validatingwebhookconfiguration cert-manager-webhook --ignore-not-found
kubectl delete mutatingwebhookconfiguration cert-manager-webhook --ignore-not-found
kubectl get role -n kube-system -o name | grep cert-manager | xargs -r kubectl delete -n kube-system
kubectl get rolebinding -n kube-system -o name | grep cert-manager | xargs -r kubectl delete -n kube-system

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true --wait --timeout 10m
```

Expect 3 pods and 6 CRDs.

---

## Edits to `40-deploy-osm.sh`

The script resets `OSM_HELM_OPTS=""` at line 74 and derives `OSM_BASE_DOMAIN` from a
LoadBalancer IP that does not exist here, so neither can be set from the environment.
Two lines inserted after line 74:

```bash
OSM_HELM_OPTS="${OSM_HELM_OPTS} --set prometheus.enabled=false --set grafana.enabled=false"
OSM_BASE_DOMAIN="172.30.18.32.nip.io"
```

Without the domain, ingress hosts render as `gui..nip.io` and fail RFC 1123
validation. `~/.osm/user-install-options.rc` also has an empty
`export OSM_BASE_DOMAIN=` that overrides the environment — fix it there too.

Prometheus and Grafana are disabled because an existing standalone `prometheus`
ClusterRole (unrelated to `rancher-monitoring`, and actively bound) blocks Helm with
an ownership error, and because a third monitoring stack on this node is not wanted.

### Grafana secret

With Grafana disabled, `mon` fails with `CreateContainerConfigError: secret "grafana"
not found`. It only needs the secret to exist:

```bash
kubectl create secret generic grafana -n osm \
  --from-literal=admin-user=admin --from-literal=admin-password=admin
```

---

## Node pod limit

Scheduling stalled with `0/1 nodes are available: 1 Too many pods` — the kubelet
default cap of 110 pods, not a CPU or memory limit (requests were 67% CPU / 14%
memory at the time). This node reached 115 pods with OSM added.

```bash
echo "maxPods: 200" | sudo tee -a /var/lib/kubelet/config.yaml
sudo systemctl restart kubelet
```

Every Pending pod scheduled within two minutes, and the airflow-migrations job that
several components were waiting on completed on its own.

---

## Deploying OSM

```bash
cd ~/osm-devops-local/installers
source ~/.osm/.credentials/git_environment.rc
source ~/.osm/.credentials/gitea_environment.rc
export OSM_BASE_DOMAIN="172.30.18.32.nip.io"
export GIT_BASE_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080"
export GIT_BASE_USERNAME="osm-developer"
export FLEET_REPO_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/fleet-osm.git"
export SW_CATALOGS_REPO_HTTP_URL="http://gitea-http.gitea.svc.cluster.local:8080/osm-developer/sw-catalogs-osm.git"
export FLEET_REPO_GIT_USERNAME="osm-developer"
export FLEET_REPO_GIT_USER_PASS="${GIT_TOKEN}"
export OSM_HELM_TIMEOUT=20m
./40-deploy-osm.sh
```

A failed run leaves a broken release; `helm uninstall osm -n osm` before retrying.

### Load during deployment

Load average hit ~30 on 8 cores while images pulled and pods started, and the API
server began timing out on `kubectl` calls. It settled to ~7.5 within 40 minutes with
no intervention. Two knock-on effects, both self-correcting:

- `mon` failed pod sandbox creation once with a multus timeout talking to the API
  server. Deleting the pod was enough.
- `osm-webserver` crash-looped ~17 times. Gunicorn started fine but was SIGTERMed by
  its readiness probe (`/health`, 15s delay, 20 failures allowed) which it could not
  answer in time under load. It came up 1/1 on first try once load dropped.

The 5G stack in `free5gc` stayed healthy throughout — including across the kubelet
restart and the IPv6 changes.

---

## Access

```
https://gui.172.30.18.32.nip.io:30843      OSM UI      (admin / admin)
https://nbi.172.30.18.32.nip.io:30843      NBI API     (401 without auth, as expected)
https://airflow.172.30.18.32.nip.io:30843  Airflow
```

Served by ingress-nginx: HTTP on NodePort 31225, HTTPS on 30843. If those NodePorts
change, find them with:

```bash
kubectl get svc -n ingress-nginx ingress-nginx-controller \
  -o jsonpath='{range .spec.ports[*]}{.name}{" "}{.nodePort}{"\n"}{end}'
```

`nip.io` resolves `<anything>.172.30.18.32.nip.io` to `172.30.18.32`, so no hosts
entries are needed for these (unlike `git.myexample.com`).

---

## Dashboard integration

The RAN selector dashboard (`ran-selector/`) has an OSM panel. Its URL was still the
dead v14 NodePort (`:30080`) and now points at the OSM 19 ingress. OSM 19 sends no
`X-Frame-Options`, so unlike Rancher it embeds in an iframe cleanly.

`backend.py` gained `/api/osm/status`, returning OSM pod readiness and the Flux
GitRepository/Kustomization ready state:

```json
{"pods":{"ready":14,"total":14},
 "flux":[{"kind":"GitRepository","ready":true},{"kind":"Kustomization","ready":true}]}
```

`index.html` renders that as a status strip above the iframe, so the panel shows the
GitOps loop is live rather than just embedding a UI.

One caveat worth keeping in the UI text: the dashboard is HTTP on :8090 and OSM is
HTTPS with a self-signed cert, so the iframe stays blank until the browser has
visited the OSM URL directly once and accepted the certificate.

---

## Housekeeping done after the install

- Gitea `osm-developer` token rotated. The provisioning script had accumulated eight
  tokens across re-runs; a new one was created, written into the `flux-system` secret
  in `flux-system`, verified (`GitRepository` stayed `READY True`), and the other
  seven revoked.

```bash
kubectl -n flux-system create secret generic flux-system \
  --from-literal=username=osm-developer --from-literal=password="${NEW_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n flux-system annotate gitrepository flux-system \
  reconcile.fluxcd.io/requestedAt="$(date +%s)" --overwrite
```

- LXD, Juju and the `lxdbr0` masquerade rule removed (see the Charmed OSM section).

---

## Still open

- Decide whether Istio and the standalone Prometheus are still needed — this node
  runs 115 pods and sits at load ~7.5 idle.
