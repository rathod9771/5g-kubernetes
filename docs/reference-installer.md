# Reference reproduction installer (second-machine test candidate)

This installer is for a **new Ubuntu 24.04 amd64 single-node machine** with Internet,
sudo, sufficient disk/RAM for OSM, Rancher, monitoring and the core, and no existing
platform to adopt. It has passed local validation only. Do not run it on the
reference machine. No CI, offline, IMS, O-RAN, H-CRAN or hardware work is included.

## Preparation on the second machine

```bash
git clone https://github.com/rathod9771/5g-kubernetes.git
cd 5g-kubernetes
# Obtain the reviewed implementation through the agreed review/transfer process;
# the implementation is not committed or pushed yet.
umask 077
cp config/global.env.example config/global.env
chmod 600 config/global.env
${EDITOR:-nano} config/global.env
./install.sh
```

Set private `OSM_USER`, `OSM_PASSWORD`, `OSM_PROJECT`, `OSM_VIM_NAME`,
`OSM_BOOTSTRAP_PASSWORD` (the upstream initial account password),
`GRAFANA_ADMIN_PASSWORD`, `RANCHER_BOOTSTRAP_PASSWORD`, and
`LAYER3_FAILOVER_SCENARIO=cran-srsran`. Set `SUBSCRIBER_DATABASE_INPUT` to an
**authorized owner-only gzip mongodump archive**, outside Git, containing the
existing `open5gs.subscribers` collection. Obtain this archive through your
approved private transfer process; this implementation does not export the
reference database. Do not paste subscriber authentication data into commands,
Git, logs or values files.

Leave runtime UUIDs blank. Host/interface discovery uses P0-B1; explicitly set
`HOST_IP`/`HOST_INTERFACE` only when discovery is ambiguous. Keep the reference
5G profile (PLMN/TAC/slice/DNN/CIDRs). The default Flannel manifest requires the
existing pod CIDR. Management/workload kubeconfigs may differ but must address the same cluster for
this submission monitoring topology; a separate
workload kubeconfig must already exist. The installer creates the management
kubeconfig when bootstrapping kubeadm. Configure NodePorts in the private file:
`INGRESS_HTTP_NODEPORT`, `OSM_HTTPS_PORT`, `GRAFANA_NODEPORT`,
`PROMETHEUS_NODEPORT`; collisions are rejected. Defaults use nip.io DNS and need
working DNS resolution to the discovered target host.

## Stage order and authority

1. Bootstrap Python/YAML/iproute2 needed for configuration parsing, validate
   private archive permissions/format before deployment, then install host
   prerequisites, pin kubeadm/kubelet/kubectl and Helm, configure
   containerd systemd cgroups for a fresh kubeadm host, swap/modules/sysctls.
2. Bootstrap or validate single-node Kubernetes; install pinned Flannel, Longhorn,
   cert-manager, ingress-nginx, Rancher and Istio with readiness gates.
3. Download the exact OSM devops commit in `config/reference-versions.json`.
   Build its OSM 19 chart with six exact dependency archive hashes and reference image digests.
   Install it without Helm’s pre-hook `--wait` (Airflow migrations are
   post-install hooks), explicitly wait for deployments/StatefulSets/DaemonSets
   afterwards, wait for certificates, expose the GUI `/osm` NBI route, trust its
   generated CA with hostname verification enabled, authenticate/rotate the
   upstream initial account, select project, register a dummy VIM and flattened
   workload kubeconfig, and verify initialized `helm-chart-v3` support.
4. Deterministically prepare/validate/onboard Open5GS from the **314 reviewed
   staging files** at `osm-packages/open5gs_knf/helm-chart-v3s/open5gs`, not
   `helm/open5gs`. This is the submission authority; canonical reconciliation
   remains deferred. Source bytes, descriptor references, generated staging and
   archive provenance are checked. Legacy trees/archives remain inputs only.
   Instantiate a fresh `core-persistent` NS and wait for OSM, Mongo/PVC, core
   required nonzero workloads, a Mongo ping, Longhorn PVC storage, stable SCTP
   AMF Service/endpoints and four metrics Services.
5. Import private subscribers only, into an empty subscriber collection, using
   `mongorestore --archive --gzip --nsInclude=open5gs.subscribers --stopOnError`
   with archive bytes on stdin. Do not restore accounts, drop collections or
   reintroduce historical Helm seeding. Verify existence of exactly one reference
   IMSI without reading authentication fields. Bind the private import receipt to
   the discovered context, PVC UID and input hash; reruns verify rather than merge.
6. Prepare/validate/onboard and verify C-RAN/srsRAN packages using P0-A. Bind core
   identity to the P0-B1 atomic runtime state. Preserve the dashboard deployment
   workflow: **installation does not instantiate a RAN automatically**.
7. Install monitoring, PodMonitors, exporter/probe script ConfigMaps and portable
   deployments; install the P0-B1 dashboard, watcher and Rancher forwarding units.

After successful installation, in the same checkout/user:

```bash
export P0_RUNTIME_CONFIG="$PWD/.runtime/installer-config.json"
./status.sh
./deploy.sh cran-srsran
```

These are target-machine lifecycle commands; they were not executed during
implementation. The baseline package/catalog is prepared before deployment.
Successful installation is not proof of RF connectivity or a UE PDU session.
The reference audit found no running RAN/UE session; verify the existing baseline
workflow separately on the target.

## Minimal OSM delta

The observed external `15-install-k8s-cluster.sh` and
`20-deploy-aux-svc-cluster.sh` wrappers disabled upstream k3s installation and
auxiliary Gitea/Flux setup. `40-deploy-osm.sh` supplied a host-specific domain;
its monitoring-disable flags are not reproduced because the observed running
OSM release has its monitoring dependencies enabled. We reproduce that by owning kubeadm infrastructure here and
never invoking upstream install wrappers. The wrapper's machine-specific domain
becomes generated values. GitOps remains disabled. We retain OSM's existing
Airflow, Kafka, MongoDB, Prometheus/Grafana dependencies; no external working tree
is copied. `installer/reference.py` is the reviewable build-copy adaptation:
server certificate usages gain `server auth`, and a separate GUI `/osm` ingress
routes to NBI. No insecure TLS bypass is introduced. These repair the observed
client-only certificate problem; target readiness/API testing must prove them.

## Reruns and failures

Run `./install.sh` again after correcting an actionable missing input. Existing
same-version deployed Helm releases are reused, not upgraded; an active dashboard
venv is not reinstalled: this avoids
regenerating upstream OSM secrets. A failed/pending release or different version
requires explicit diagnosis and recovery. Do not uninstall or delete PVCs as a
routine recovery. Runtime records are private under `.runtime/`, ignored by Git.
Interrupted core creation/instantiation leaves a receipt and requires checking
that specific OSM instance before explicit reconciliation; it never blindly
creates a second core. Partial subscriber restore leaves no completion receipt;
nonempty unrecognized state is refused rather than overwritten. Preserve the
private archive and PVC for diagnosis. A changed PVC/context/input is not adopted.

Private values go to owner-only files/stdin. Installer subprocess output is
captured and failures are sanitized to avoid exposing secrets. Diagnose failures
on the target using resource/service status; avoid posting Secret contents,
flattened kubeconfigs, private values, tokens or database documents. Upstream
containers may have their own logging behavior; do not publish their raw logs
without review.

## Remaining target proofs

Internet availability of pinned charts, old Kubernetes apt packages and image
registries; actual OSM API registration/instantiate schema; chart dependency
availability; server TLS issuance and trust; OSM LCM Helm registration; resource
capacity; MongoDB tooling/archive compatibility and subscriber import; Longhorn
PVC provisioning; dashboard/watcher startup; baseline RF/UE operation. Core and
RAN source tags are preserved to avoid changing their observed behavior; recorded
image digests do not make every preserved chart tag immutable. This is online
reproduction, not an offline image cache or a claim of fully proved fresh install.

For a sanitized first diagnosis on the target (not the reference):

```bash
kubectl --kubeconfig "$HOME/.kube/config" get pods,pvc -A
helm --kubeconfig "$HOME/.kube/config" list -A --all
systemctl --failed
```

Remote Helm chart pulls, repository operations and dependency downloads retry only
recognized transient network failures, for at most four attempts with 2/4/8-second
backoff and a 120-second timeout per fetch. Repository charts are downloaded at
the pinned version before any release creation, then installed from that local
archive. Install/upgrade, lint and other lifecycle/validation operations are not
retried. Errors identify the stage/component and chart/version where applicable;
final stderr is summarized using public error categories to avoid leaking secrets.

### Public OSM source reconstruction

The acquisition pin is Gerrit's publicly advertised `v19.0` commit
`019473f83210197b833c03e2efdda0fb290b9eed`. Its ancestor is the peeled
`v19.0.0` release tag, `d0376773da8b460264c1ada5f95c8d5f6f03340b`.
Read-only verification on 2026-10-03 found that the reference checkout's origin
was `https://osm.etsi.org/gitlab/osm/devops.git`, rather than Gerrit.
Its HEAD `3804ad93d2626137ca5295a7ecdae29d301a6274` is the upstream
June 2026 Jenkinsfile rename commit, parent
`9cab697f49b5341f75e019cb9d983bb6d474e56d`; it was also its cached
`origin/v19.0`. Public GitLab `ls-remote` independently confirmed that same tip.
GitLab availability does not prove Gerrit availability. Gerrit's
advertised branch ends at the selected base, while its annotated release tag
is `c1fd66fc44b86e85dd61828735f536c913ae35a0`.

`scripts/installer/patches/osm-v19-reference.patch` captures the exact committed
base-to-reference delta: rename `Jenkinsfile` to `Jenkinsfile.old`, change the
release default, add the `-R` installer option, and pass the daily release into
the upstream test pipeline. These five files are preserved for source identity;
our installer never executes upstream installation or CI scripts. This patch
contains no Helm changes. Its SHA256 and reconstructed Git tree
`42ddcbf52f27bcc967e6b72496349fffb73c97b3` are locked alongside the public base.
A fresh public clone plus this patch reproduced that complete committed tree.
All 44 tracked OSM chart files matched the reference working tree exactly.

The reference working tree is not claimed to equal that committed tree. It has
11 uncommitted wrapper changes: ten scripts replaced with `exit 0` (client tools,
Kubernetes, auxiliary services, Flux cloning, four Gitea helpers, and two Minio
helpers), plus `40-deploy-osm.sh` overrides disabling its Prometheus/Grafana and
setting a host-specific domain. They are not chart changes and are not copied:
our installer owns those lifecycle stages and generates domain configuration.
The untracked management-cluster clone helper is another `exit 0` stub and is
not required by our execution path. The two `.bak` files are backups, not inputs;
`40-deploy-osm.sh.bak` equals its HEAD original, while the client-tools backup
differs from its HEAD original. No kubeconfig, certificate, credential or local host value
is extracted into the source patch.

Ignored chart outputs comprise `Chart.lock` and six dependency `.tgz` archives.
Chart.yaml pins all six dependency versions exactly; their reference bytes
match `osm_dependencies` in the version lock. They remain generated/downloaded
outputs, verified by the existing dependency checks.

Reruns reuse only a clean verified public-base cache. Reconstruction happens in
a temporary copy, with base, patch checksum, staged tree and working content
verified before the chart is used. Reapplying to an already reconstructed copy
verifies the same tree without applying twice. Wrong bases, patch failures and
unexpected changes stop installation. The public source cache remains pristine;
existing repository-owned certificate adaptations still operate on the chart
build copy after reconstruction.

### LCM management kubeconfig

Read-only reference inspection confirmed an Opaque `osm/mgmtcluster-secret`,
key `kubeconfig`, mounted through `mgmtcluster-kubeconfig` at
`/etc/osm/mgmtcluster-kubeconfig.yaml`, read-only with matching `subPath`.
The reference uses mode 0644, pod fsGroup 1000 and LCM UID 1000. The upstream
mount is gated by GitOps even though LCM initialization requires the file.
The build-copy adaptation removes only those two mount/volume gates. GitOps
stays disabled; mode 0640 with fsGroup 1000 limits credential readability.

Before releasing OSM, the installer creates target-management-cluster service
account `osm-lcm-management`, its token Secret and a namespace-scoped read-only
Role/RoleBinding. The Role permits get/list/watch of pods, services, ConfigMaps,
Deployments and StatefulSets in the OSM namespace; it grants neither cluster
administration nor Secret access. This is the disabled-GitOps bootstrap scope,
not authorization for enabling management provisioning workflows.

The token controller supplies target-cluster credentials and CA. An in-cluster
API endpoint and that dedicated identity produce the Opaque kubeconfig Secret.
Credentials remain in memory and are sent to kubectl over stdin; server-side
apply avoids last-applied credential annotations. No target kubeconfig is
written to chart values, Git, generated archives or runtime JSON. Reapplying
stable objects reuses the same service account/token identity.

Before API/registration readiness, the installer checks Secret key names and
the exact deployment mount/source/permissions, without printing credential
values. Missing objects or wrong paths fail specifically. For an existing
pinned release lacking the mount, a single Helm upgrade of the adapted chart
with `--reuse-values` reconciles it; subsequent reruns do not repeat that repair.
Existing timeouts remain unchanged. No reference-machine credentials are copied.

### Reference GitOps startup settings (supersedes the disabled-GitOps assumption)

Read-only inspection confirmed that the reference release uses the upstream
`global.gitops.enabled: true` default, imports `osm-gitops-secret` into LCM,
and provides all three URL keys. Its runtime base is the upstream default
`http://git.127.0.0.1.nip.io`; both Helm repository URL overrides are
`https://github.com/example/example.git`. These are observed reference bootstrap
settings, not invented replacements or proof of functional GitOps repositories.
Secret data and runtime authentication values were not printed. The LCM ConfigMap contains
no GitOps overrides; URLs reach LCM through Secret-backed environment variables.

The running LCM GitOps constructor builds an authenticated catalog URL without
handling None. Disabling the Helm gate removes required environment settings;
it does not implement a clean application-level GitOps disable switch.
The installer now reproduces those exact non-secret settings from the version
lock. No LCM code, private Git credentials or repository provisioning is added.
Missing or credential-bearing URL configuration is rejected before rendering.
Existing releases are reconciled with a values-preserving Helm upgrade only
when settings, environment reference or required Secret keys are missing.
Subsequent matching reruns leave the release alone. Readiness refuses a repair
that does not converge. No timeout is increased.

The earlier management credential Role remains scoped to the OSM namespace.
This change fixes reference startup configuration; it does not grant permissions
or create real repositories for management GitOps provisioning workflows.

### Open5GS NS instantiate contract and reruns

Read-only reference operation metadata showed `nsName: core-persistent`,
`nsDescription: persistent open5gs core with NRF fix`, an NSD catalog UUID and
its dummy-VIM UUID, with no additional per-VNF overrides. The installer uses
that name/description with the freshly verified target NSD and discovered VIM;
it retains its target-project Kubernetes namespace parameters. Reference UUIDs
are not copied. Both creation and the separate instantiate POST include nsName.

The private context-bound receipt records creation before instantiation. A
schema-rejected request retains the owned instance. Reruns may resubmit only
when that exact instance is NOT_INSTANTIATED and has no lifecycle operations.
READY/BUILDING instances resume without another instantiate request. Unknown
ownership, mismatched contexts, existing operations and broken states require
explicit recovery; no automatic terminate/delete or duplicate creation occurs.

HTTP failures retain their status and operation. A 422 required-field response
is summarized using a fixed field whitelist; arbitrary response bodies, tokens
and request data are suppressed. Catalog byte-provenance checks still run after
onboarding and are not bypassed. The ingress API remains verified HTTPS, with
an explicit HTTP upstream protocol to the NBI service at port 9999. The installer
does not make direct HTTPS requests to that plain-HTTP service port.

### Monitoring resume and diagnostics

The healthy pinned monitoring Helm release is reused, never reinstalled or
upgraded automatically. Kubernetes workload status must show the current
observed generation, desired/updated/ready replicas, and availability before a
redundant rollout check is skipped. StatefulSets are already checked individually
inside release verification; the extra collection rollout has been removed.
NodePort reconciliation first checks its type, selector and exact ports. An
already matching service is left alone; missing/mismatched service state is
applied and verified. PodMonitors and the two monitoring addons retain their
existing idempotent apply paths.

All monitoring namespace, workload discovery/rollout, NodePort, PodMonitor,
AMF-discovery, addon ConfigMap/RBAC/Deployment and addon-rollout operations carry
specific error context. Explicit safe reads/applies/rollout checks retry only
recognized transient API failures or command timeouts, at most four attempts
with 2/4/8-second backoff. Deterministic permission/configuration/resource failures
remain fatal; failures are not suppressed merely because other pods are healthy.
No Helm lifecycle command gains retries.

Read-only reference inspection on 2026-10-03 found no
`v1beta1.metrics.k8s.io` APIService and no metrics-server deployment.
Kube-prometheus-stack does not establish that API. Missing HPA resource metrics
are a separate platform gap; metrics-server is not installed by this fix.
The original generic error cannot identify which command failed on the second
machine. In particular, the unnamed StatefulSet rollout succeeds on the
reference; new diagnostics avoid attributing the failure to it without evidence.
