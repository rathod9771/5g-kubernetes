# P1–P7 security review

## Enforced boundaries

- Server-owned bearer identity file must be regular, non-symlink, owned by the
  process user and inaccessible to group/others. Only SHA256 token hashes are
  stored. Token values never appear in journal events, reports or diagnostics.
- Every new API request authenticates before opening the sandbox journal. Writes
  require narrow roles, scoped tenant objects and strict bounded bodies. Workload
  creation cannot supply ownership, verified evidence, tenant or live-execution flags.
- Legacy dashboard APIs require an explicit tenant-bound operator. Lifecycle writes
  are retired even for operators. Existing assurance controls remain local
  incident/proposal operations; LIVE_AUTO is rejected and ProductionExecutor disabled.
  A central operator token cannot submit upstream telemetry through the legacy L3
  webhook: it is not an observation attestation credential.
- Independent reviewer approval binds release, exact package digests, action,
  target/context, tenant and expiry. Approvals are one-use; replayed identical events
  return the prior result, while changed requests under the same ID are rejected.
- Catalog and update authorizations are separate. Both protected NS UUIDs are
  denied; there is no arbitrary HTTP primitive, shell command, client injection or
  caller-supplied backend callback available through the API. P5 permits only explicit
  approved disposable-project catalog publication using private server configuration.
- Updates are sandbox state transitions only. No code calls instantiate/terminate/
  delete/upgrade or Helm install. Unknown results persist a conflict; reconciliation
  changes only the simulated record and does not replay any request.
- Local chart inputs are captured with no-follow descriptors, bounded depth/count/
  bytes; existing chart safety/OSM reference/archive code is reused. Rendered inputs
  must use the declared image digest, nonprivileged singleton workloads, no host
  network/ports/mounts, no service-account token, explicit resource limits and no
  chart hooks or cluster-scoped kinds. This deliberately excludes advanced NFs.
- Helm/Git reads use the existing bounded command runner (8 seconds, 2 MiB combined
  output, group cleanup and a 256 MiB sampled RSS watchdog); validators receive a minimal
  credential-free environment and Helm is limited to one Go execution thread. Helm
  cannot use an inherited kubeconfig. P5 adds separately bounded source/registry
  readers and a remote TLS BuildKit client; it never uses the host Docker daemon or
  runs containers locally. The workspace must contain
  operator-approved local inputs; Helm templating is not a hostile-code sandbox.
- SQLite is owner-only, transactions short and connections explicit. Reads do not
  reinitialize schema. Audit integrity is verified before new events; incomplete
  schema/corruption/contention fails closed. Audit processing stops for review at
  10,000 records rather than silently truncating history. Admin file replacement is
  outside this hash-chain threat model; it is not WORM or HA storage.
- All protocol/monitoring assertions accepted by the MVP are SIMULATED or UNAVAILABLE.
  Source metadata and provider claims are separate from platform offline validation.
  Production qualification is always false. Age is evaluated independently of receipt.
- Correlation uses existing L3 snapshot identities and exact service/cluster/provenance.
  Synthetic data lives in separate tenant journals; production assurance.sqlite is
  never opened. Investigation and blocked rollback proposals have no executor.
- Frontend results use textContent; HTML reports escape serialized data and use a
  restrictive CSP/no-store. Tokens stay in page memory; there is no localStorage,
  automatic polling, cross-origin bearer forwarding or automatic request retry.

## Residual risks / not production certification

This is a single-host research coordinator with server-owned token administration, not
SSO/OIDC, a distributed lease service, live ownership attestation or multi-cluster
RBAC certification. A person who controls the service's user account, auth file or
source code can change policy. Tenant boundaries protect API operations, not two
hostile workloads sharing the same kernel. No researcher build should execute on
the production four-core DU host.

The sample OCI references are deliberately fictitious; registry bytes, signatures,
licenses, SBOMs and runtime behavior are NOT VERIFIED. Manifest validation is bounded
syntax/policy checking, not a 3GPP conformance certificate. Chart lint/render and
heuristic secret screening cannot prove absence of all secret encodings or arbitrary
application behavior. Hardware/privileged/CRD/operator charts need isolated workers,
additional policy and separate qualification.

Broad multi-tenant legacy monitoring is not offered: the old dashboard is operator
only and explicitly bound to one tenant. Existing standalone assurance deployments
retain their prior authentication interface and require their own exposure review.
No changes to token files or service environments were made by this implementation.

Before any live adapter: certify OSM version/backend/payload/rollback behavior,
resolve supported platform versions, issue least-privilege target credentials,
centralize real ownership verification, protect every affected dependency, review
P5 crash-safe submission intents and manual unknown-outcome reconciliation, and independently
approve a disposable target. Do not convert SIMULATED_ONLY to live with a flag.

## P5 additions reviewed

- Every secret reference is bound to tenant, purpose and exact destination/workload.
  A catalog credential cannot be selected as registry authentication. Malformed
  policy allowlists fail closed rather than becoming substring allowlists.
- Native GitHub ingress authenticates bounded push JSON with SHA256 HMAC before
  journal access. Server policy fixes identity/repository/ref/chart; it only queues.
  Other endpoints retain bearer authentication. Duplicate events never auto-run jobs.
- A host-wide execution lock plus per-workload lock serializes jobs. Source captures
  are pinned, hook-free, confined and bounded. Child errors/logs are suppressed to
  stage/class/exit code; no request can supply shell commands or worker endpoints.
- Dockerfile execution requires an externally provisioned dedicated quota-enforced
  TLS worker with a finite lease. That assertion is server policy, not independently
  proven by this implementation. Isolation/cancellation remain live-verification gates.
- Verified OCI bytes are mandatory before build success. Retention is atomic, fsynced,
  digest-addressed and refuses corrupt preexisting artifacts; no in-use image is
  replaced and no image is pushed or imported into host containerd.
- Catalog publication requires a verified package and independent exact approval,
  disposable research-project policy, fresh scope/target checks and durable intent.
  It uses existing Catalog transport, only POSTs new immutable names, verifies
  readback, and never retries a failed/uncertain publication or deletes partial work.
  External name races need exclusive project credentials; local flock is not fencing.
- Instance execution remains NOT_VERIFIED and hard-blocked. No request/body field
  can bypass the blocked OSM upgrade/rollback and Kubernetes rollout-certification gate.

No live service environment or auth file was changed. P5.md records supported
limits and gaps. Secret screening is heuristic, not proof that arbitrary provider
source or layers contain no secrets. Test registry/catalog transports and exported
fixture images are explicitly offline evidence, never telecom qualification.

## P7 registry and worker additions

- Digest-only Skopeo publication/readback replaces the previous image-push gap.
  Exact tenant/repository/purpose credentials, verified TLS, isolated client home,
  pinned CLI contract, resource/request watchdogs and durable uncertain outcomes
  prevent anonymous fallback or automatic replay. Raw debug traces are never persisted.
- Before sending build context, mTLS certificate preflight checks a reviewed peer
  fingerprint; Buildctl uses the pinned IP/server name/dedicated CA. Remote quota,
  egress and lease evidence must still be certified independently.
- OCI TAR/media-type limits with bounded PAX timestamps reject metadata bombs and URL-addressed layers before
  transfer. Digest verification does not certify image signatures or application safety.
- Built releases without fresh exact registry evidence or with changed/disabled
  registry policy cannot request/publish catalog packages. Actual NBI research
  publication rejects admin/default credentials and checks the configured project
  UUID/name before content writes. CLI/osm_im is not used by this path.
- Network counts are version-pinned debug-trace observations, not proof of exact
  wire request totals. Certify redirect/internal-request bounds and enforce quotas
  on the test registry/proxy before enabling live operation. Watchdogs are sampled.
- No live operation was run; no instance execution/remediation path was enabled.
  See P7.md for full constraints and P7-LIVE-TEST.md for required approval boundaries.
