# Ready RAN image recovery and simulated UE acceptance

This candidate has local/static validation only. Execute runtime steps on
Precision after review, commit and transfer. Do not execute them on the reference
machine. No USRP, SDR or UHD work is included.

## Image authority

`config/reference-versions.json:ran_images` owns approved image identities.
Registry chart `image_bindings` select components, without duplicating identities.
Packaging applies the approved image values **after** profile values, records
the selected policy components in `source_inputs`, and rejects rendered images
or pull policies that disagree. The complete central file is captured atomically
in the source snapshot; unrelated infrastructure policy entries do not become
RAN content identity. Generator hashes include `runtime_images.py`.

The three ready srsRAN scenarios share one OCI manifest:

```text
localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6
```

The runtime tag is locked to manifest
`sha256:e34a0ef3aa0649b0ba0e4b4d136867084371b780e55ec859c601a2987041fc59`.
The helper verifies manifest/config bytes, unpacked content, and CRI resolution
of this exact tag before success. An existing tag with another target is rejected
without overwriting it. No digest-qualified local name is assumed resolvable.

This is a **local verified import**, not a remotely published image. Kubernetes
uses `imagePullPolicy: Never`; it cannot silently pull another image. Import on
every schedulable amd64 workload node. Dashboard replacement preflight rejects
nodes that do not advertise the verified runtime tag before any RAN teardown.

The existing `/tmp/srsran-reference-working.tar` is the required separately
transferred input, not a Git artifact. Its exact identity is:

```text
size: 3606001152 bytes
SHA256: 110ebdaf346a85b3ceeebe6184cf58b698ca009afa0e2bfb16e7eec10fcc0754
image config: 8a156365eb292ce9d5b03ba69a2901fa997490196b6a89becaf31c55194e514d
srsRAN source: herlesupreeth/srsRAN_Project
commit: 11c9bbabb69873752500d676f55e0034f6caa5c5
version: 25.04.0
```

Conversion authenticates the export, config and each layer, wraps the existing
bytes in a deterministic OCI archive and verifies the resulting manifest digest.
It does not rebuild binaries. Atomic output prevents partial conversion from
being consumed. Import is serialized, checks the containerd `k8s.io` image target
digest, reuses an existing correct image and never pulls `master`. Reuse also
requires containerd's image check to confirm complete, unpacked content;
a partial image record is not reusable. Conversion
uses temporary disk space of approximately 3.4 GiB, in addition to input and
containerd unpacked storage. Runtime/CPU compatibility still needs Precision
validation. A clean clone without this input cannot recreate the retained image;
automatic source rebuilding and public mirroring are deferred.

OAI CU `2026.w13` and DU `2026.w25` remain unchanged. Their digest binding is
explicitly deferred until reference-cache equality is proven. These fixed release
tags are the only ready-runtime digest deferrals; they are not a promise of
registry tag immutability. F-RAN uses the verified nginx index digest recorded in
the policy, preserving its existing edge application. O-RAN/H-CRAN remain blocked.

For direct local Helm inspection, supply the central values explicitly, e.g.:

```bash
python3 -B scripts/runtime_images.py values cran-srsran cu > /tmp/cran-cu-images.json
helm lint helm/cran-srsran/cu -f /tmp/cran-cu-images.json --strict
helm template cran-srsran-cu helm/cran-srsran/cu -f /tmp/cran-cu-images.json
```

OSM deployment uses generated baked charts and needs no additional values file.
Strict validate still rejects source/provenance changes. Explicit `prepare` can
publish a new source receipt when a reviewed canonical input changes without
altering archive bytes; it first checks the stored schema, publication digest and
payload hashes. Metadata-only drift continues to preserve stored provenance.

## Precision sequence after approval

Run from the updated repository, with the approved export transferred outside
Git and private configuration already configured. Set the archive path to its
actual location. The commands below are proposed target operations, not actions
performed during implementation.

```bash
export PYTHONDONTWRITEBYTECODE=1
export P0_RUNTIME_CONFIG="$PWD/.runtime/installer-config.json"
SRSRAN_EXPORT=/private/path/srsran-reference-working.tar
python3 -B scripts/srsran_image.py verify "$SRSRAN_EXPORT"
python3 -B scripts/srsran_image.py import "$SRSRAN_EXPORT"
```

For a genuinely fresh host, the installer-supported path is instead:

```bash
./install.sh --srsran-image-archive /private/path/srsran-reference-working.tar
```

It verifies the input before host bootstrap, imports after Kubernetes bootstrap
and before OSM installation, and prepares/onboards all seven ready scenarios.
It does not instantiate a RAN automatically.

For the retained failed Precision instance, pause target automation and reload
the reviewed dashboard code before reconciliation:

```bash
sudo systemctl stop layer3-watcher
sudo systemctl restart ran-selector
source scripts/common.sh
load_config
python3 -B - <<'PY'
import json, os, yaml
state = yaml.safe_load(open(os.environ['ACTIVE_STATE_PATH']))
pending = state.get('osm', {}).get('pending_instance')
print(json.dumps({k: pending.get(k) for k in ('id', 'scenario', 'operation')} if pending else {}, indent=2))
PY
```

Copy only the printed pending ID into this explicit cleanup request:

```bash
PENDING_ID=the-recorded-pending-id
curl --fail-with-body --max-time 420 -X POST "$DASHBOARD_URL/api/reconcile-pending" \
  -H 'Content-Type: application/json' \
  --data-binary "$(python3 -c 'import json,sys; print(json.dumps({"instance_id":sys.argv[1]}))' "$PENDING_ID")"
```

The endpoint holds the lifecycle lock, verifies the runtime cluster/context,
requires the exact recorded ID, rejects active/core/additive ID overlap, checks
the NS name and failed operation ownership, terminates through OSM, confirms
scenario resources are gone and confirms NS deletion before clearing pending
state atomically. It retains termination operation identity for safe resume.
It does not delete the shared namespace or arbitrary Kubernetes resources.
An unrecorded/requesting-stage ID, processing/successful operation, identity
mismatch, teardown failure or leftover resources stops reconciliation.

After successful reconciliation, update packages and retry deployment once:

```bash
python3 -B scripts/osm_packages.py prepare --ready
python3 -B scripts/osm_packages.py validate --ready
./scripts/osm-onboard.sh --ready
./deploy.sh cran-srsran
./status.sh
```

Verify CU/DU readiness, zero SIGILL/exit 132, F1 SCTP, CU-to-AMF NGAP and a completed
OSM operation. Stop on failure; do not repeat instantiate blindly. Obtain local
namespace/kubeconfig from the validated runtime snapshot, never reference UUIDs.
Use `./deploy.sh` for the subsequent transitions, checking each before continuing:

```bash
./deploy.sh vcran-srsran
./deploy.sh cloudran-srsran
./deploy.sh cran-oai
./deploy.sh vcran-oai
./deploy.sh cloudran-oai
./deploy.sh fran
```

F-RAN is additive; deploy it once. Do not repeat its creation to simulate a smoke
test. Do not use manual Helm installations to bypass OSM. Resume the watcher only
after final validation and a deliberate choice of baseline profile.

## One simulated OAI UE path

Prefer a validated `cran-oai` baseline first. The split UE keeps the existing
RF-simulator parameters: band 78, numerology 1, 106 PRBs, carrier 3450720000 and
SSB 516. Profile rendering selects the actual corresponding DU label. Discovery
uses Python's standard library and mounted service-account TLS credentials,
without installing Python packages at startup.

Create an owner-only JSON input outside Git with exactly `imsi`, `key`, `opc`,
using the authorized subscriber already restored into Open5GS. Do not print its
contents. The configured DNN/SST/SD must remain `internet`/`1`/`010203`.

```bash
./deploy.sh cran-oai
python3 -B scripts/prepare_simulated_ue.py --scenario cran-oai \
  --subscriber-input /private/path/ue-subscriber.json --output "$RUNTIME_DIR/simulated-ue.yaml"
```

The output is mode 0600 and includes private Secret data. Do not commit it, print
it, copy it into packages or use `kubectl diff` on it. It creates the existing UE
RBAC, non-secret discovery/init ConfigMaps, a subscriber Secret and the UE at
replicas zero. Subscriber fields are read from `ue.conf`, not command arguments.
OAI UE stays `2026.w13`, bound to the cached-reference-verified digest; the Python
helper also uses its central digest.

Before enabling this UE, ensure no other simulator UE using the subscriber is
running. If the legacy `oai-nr-ue` deployment exists, scale that target deployment
to zero and confirm its pods are gone first. Then:

```bash
kubectl --kubeconfig "$OSM_KUBECONFIG_PATH" apply -f "$RUNTIME_DIR/simulated-ue.yaml"
kubectl --kubeconfig "$OSM_KUBECONFIG_PATH" -n "$OSM_PROJECT_NAMESPACE" scale deployment/oai-nr-ue-cran --replicas=1
kubectl --kubeconfig "$OSM_KUBECONFIG_PATH" -n "$OSM_PROJECT_NAMESPACE" rollout status deployment/oai-nr-ue-cran --timeout=180s
```

Collect restricted runtime evidence of UE pod state, MIB/SIB1, RRC, AMF NGAP,
successful registration, PDU session, assigned UE IP and selected DNN/NSSAI.
Logs can contain subscriber identifiers; do not commit or publish raw logs.
Pod readiness alone is not UE acceptance. If SIB1 still fails, report it and
inspect simulator/profile parameters; do not claim registration or PDU success.
Scale the simulator to zero and wait for its pod removal before any RAN switch.

### Exact local-runtime verification (target machine)

After transferring the approved export, run the helper on each workload node:

```bash
python3 -B scripts/srsran_image.py import /tmp/srsran-reference-working.tar
sudo ctr --namespace k8s.io images list
sudo ctr --namespace k8s.io images check --quiet name==localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6
sudo crictl --runtime-endpoint unix:///run/containerd/containerd.sock --image-endpoint unix:///run/containerd/containerd.sock inspecti localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6 | python3 -B -c 'import json,sys; s=json.load(sys.stdin)["status"]; assert s["id"]=="sha256:8a156365eb292ce9d5b03ba69a2901fa997490196b6a89becaf31c55194e514d"; assert "localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6" in s["repoTags"]; print("Verified CRI runtime tag and config identity")'
```

The list must show the exact tag targeting manifest
`sha256:e34a0ef3aa0649b0ba0e4b4d136867084371b780e55ec859c601a2987041fc59`.
The helper authenticates that manifest's bytes and its config descriptor, checks
unpacked content, then performs the same explicit CRI lookup above. Kubernetes
`Never` uses the tag; it makes no registry fallback. kubeadm's `cri-tools`
dependency supplies `crictl` on fresh hosts.

For an isolated Kubernetes resolution check on the **test machine**, use a
fresh namespace (stop if that namespace already exists):

```bash
kubectl create namespace srsran-image-acceptance
kubectl -n srsran-image-acceptance apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: local-image-check
spec:
  restartPolicy: Never
  containers:
    - name: image-check
      image: localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6
      imagePullPolicy: Never
      command: ["/bin/sh", "-c", "set -e; command -v srscu; command -v srsdu"]
YAML
kubectl -n srsran-image-acceptance wait --for=jsonpath='{.status.phase}'=Succeeded pod/local-image-check --timeout=90s
kubectl -n srsran-image-acceptance get pod local-image-check -o jsonpath='{.spec.containers[0].image}{"\n"}{.spec.containers[0].imagePullPolicy}{"\n"}{.status.containerStatuses[0].imageID}{"\n"}'
```

Success proves the exact rendered name starts a container with `Never`; this
check does not claim RAN radio/CPU acceptance. Delete only this test namespace
when finished: `kubectl delete namespace srsran-image-acceptance`.

### Adopt a completed pending deployment after a client timeout

A client timeout does not prove OSM instantiation failed. If the exact recorded
pending operation completed and its NS is READY/running, use the separate
`POST /api/adopt-pending` endpoint with both recorded identifiers:

```json
{"instance_id":"<recorded pending id>","operation_id":"<recorded pending operation>"}
```

The endpoint takes the shared lifecycle lock, validates current runtime context,
and checks the NS/operation IDs, scenario-owned NS name, instantiate operation
type and COMPLETED/READY/running states. It performs no OSM lifecycle action.
The atomic write promotes the pending scenario/instance to active, stores
`osm.active_operation_id`, and removes pending state. Core identity and context
remain intact. Repeated identical requests revalidate OSM and return
`already_adopted: true` without rewriting state. Stale or conflicting requests
are rejected. `/api/reconcile-pending` continues to handle failed-instance
cleanup only; successful operations are never terminated by adoption.
