> Historical P0 design baseline at c63aa1c. See ../README.md for implemented MVP scope.

# Proposed OSM v19 lifecycle adapter

## Scope and reuse

Extend the existing verified-HTTPS `Catalog` and `osm_client` modules; reuse runtime
context validation, exact identity/provenance checks, atomic state, local lifecycle
lock, pending reconciliation and assurance verification. Keep `ReadOnlyOSM` as a
strict separate observation interface with its request budgets; never add mutation
methods to that class. Keep Layer 3 ProductionExecutor disabled.

The future deployment coordinator has its own narrowly approved deployment
identity; deploying a researcher release does not enable automatic remediation.
Do not route CI jobs through the legacy dashboard replacement API or shell retry
helper. Existing clients need bounded response size/polling, typed sanitized errors,
and exact request/operation propagation before general use; `Catalog.request`
currently reads response bodies without an application-level size cap.

## Capability profile, not guessed endpoints

Bind certification to OSM release, NBI/LCM image/source digest, patches, connector
backend, Helm version, descriptor model and chart/package shape. Record each
operation as UNSUPPORTED, CANDIDATE, LAB_VALIDATED or ENVIRONMENT_APPROVED with
proof and expiry. Start with all running-update capabilities unavailable.

| Operation | Plan/contract | Current baseline |
|---|---|---|
| Discover | Project/VIM/OSM cluster, exact NS/VNF/KDU/release, package and resource UID mapping, pending operations and ownership | Partial existing discovery and validation; expand beyond one RAN/core graph. |
| Catalog compare/publish | Resolve content hash/version to exact UUID; create new release identity; readback match; no modification of referenced bytes | Existing verify/upload reusable, but general immutable-catalog policy missing. |
| Instantiate isolated NS | Approved target/peers/capacity, immutable package IDs, durable intent then single submission | Existing create client and pending tracking reusable; tenant/telecom acceptance gates missing. |
| Update KDU | Exact NS/member-VNF/KDU/release and old/new hashes; capability-certified OSM primitive/payload | Not implemented/certified. Official v19 examples include KDU upgrade and rollback, but this install was not exercised in P0. |
| Change VNF package | Exact model-specific payload and software-version semantics; tested effect | No general validated running-update support. Not interchangeable with a chart upgrade. |
| OSM declarative application | Separate OKA/KSU/profile/Flux compatibility lane, with one authoritative owner | Future optional lane; current bootstrap GitOps URLs do not establish an operative provider pipeline. |
| Rollback | Recorded prior release/values/data compatibility; approved compensating operation; independently verify result | No generic validated service rollback. Never fall back to direct Helm rollback under OSM. |
| Terminate/remove | Separately approved destructive plan, exact owned resource set, protected-target/dependency checks | Existing methods are primitives, not safe general workflow authorization. Never automatic retry cleanup. |

The official [OSM v19 KNF guide](https://osm.etsi.org/docs/user-guide/v19/how-to/nfv/deploying-network-services.html)
demonstrates KDU upgrade/rollback via NS actions and separately discusses
CHANGE_VNFPKG. This supports a lab investigation, not an activation command.
The local `docs/osm-ns-update-findings.md` records same-package/no-diff behavior;
it does not prove those documented mechanisms work or fail for a changed image in
this installation. The reported object-versus-list payload discrepancy requires
version-specific schema/source verification; never silently try alternative bodies
against a live service.

## Proposed typed interface

Design operations: `discover`, `compare_catalog`, `plan`, `validate_plan`,
`submit_approved_operation`, `observe_operation`, `verify_effect`,
`plan_rollback`, `reconcile_unknown`. Each returns typed identity/evidence objects,
not shell text or boolean-only success. The manifest chooses a certified operation
kind; researchers cannot supply arbitrary URL, REST method, primitive or shell command.

A plan binds:

- Tenant/owner, OSM host/project/VIM/cluster UUIDs, namespace UID and deployment owner.
- NS/VNF/KDU UUID/name mapping, Helm release and Kubernetes resource UID inventory.
- Old/new immutable artifacts/config/schema hashes, dependency/peer graph and impact.
- Proposed exact operation/body hash; current operation and drift baseline.
- Capability proof, test/rollback evidence, capacity/radio prerequisites.
- Approver identity/role, expiry, allowed disruption and compensations, plan digest.

An operation record binds intent ID, approval and plan digest, submission attempt,
request stage, OSM operation ID/state, evidence locations, resource observations,
watchdog status, terminal status and uncertainty reason. No token, subscriber data
or full raw response is recorded.

## Submission, concurrency and failure semantics

1. Reject either protected NS and its transitive owned/required resources before
   planning/submission, including shared Core/catalog/PVC dependencies.
2. Revalidate approval and context under the existing lifecycle lock. Check all
   affected NS operations and ownership; cache observations do not authorize writes.
3. Persist intent plus exact request hash before submission; commit/close the DB
   transaction before external I/O. One coordinator worker per host initially.
4. Submit once. Persist returned operation ID immediately. HTTP acceptance means
   SUBMITTED, not DEPLOYED. Authentication refresh must not replay an uncertain write.
5. A connection loss after submission, malformed response or process death creates
   UNKNOWN_OUTCOME. Reconcile against bound NS/type/time/evidence; ambiguous matches
   require an operator, not a new POST or forced cleanup.
6. Poll with bounded reads/deadline/backoff and cancellation checkpoints. A timeout
   stops local polling, not proof that OSM stopped. PARTIALLY_COMPLETED, FAILED_TEMP,
   missing state and unknown IDs are not successful release transitions.
7. Check OSM operation identity/type/owner and final state; then verify chart/release,
   runtime image IDs, workload generation/health, config and telecom effects. A
   successful expected no-op is distinguishable from an expected update with no effect.
8. Release locks only after durable outcome or explicit unknown/pending state is
   recorded. Unknown state blocks conflicting work even after the OS lock is freed.

A local flock cannot fence external OSM clients or workers on another host. Before
multi-worker scale, add durable target leases and fencing tokens at the coordinator,
restrict mutation credentials to it, and certify backend conflict behavior. Journal
CAS does not mean OSM supports conditional writes or idempotency keys; do not claim
exactly-once side effects without backend evidence. External drift always triggers
replan/reapproval. An emergency stop prevents further submissions; it cannot promise
to cancel an already accepted OSM operation. No unvalidated OSM cancellation is sent.

## Verification and rollback

Require agreement between OSM intent/outcome, workload state and telecom tests.
Verify the actual running image/config and reference artifact separately from Pod
name/age: a valid in-place update may not create a different Pod, while a restarted
Pod can still run old bytes. Inspect last-applied configuration and owning release,
using read-only bounded clients and privacy-safe evidence.

Kubernetes Deployment rollback concerns workload revisions; it is not a database or
UE-session restoration guarantee. [Kubernetes Deployment lifecycle](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/).
Each provider must classify stateless config changes, CU/DU process restart, AMF/SMF
state change, UPF/PFCP/session impact and persistent-data migration independently.
A saved chart with incompatible data is not a viable rollback. Pin prior package,
image, values, secret versions and verified backups, including recoverable schema
version and consistency point. Irreversible migration requires an approved recovery
plan or rejection of the update. No data backup containing subscriber material goes
to public Git or ordinary CI artifacts.

If continuity cannot be demonstrated, plan a maintenance interruption or reject the
update. Parallel NFs need spare CPU/radio/cell identities, network/traffic steering,
compatible peers and tested state transfer; simply adding a second Pod is not a
canary. Failed rollback escalates and freezes conflicting work. Never terminate an
old working service to force a new update to look successful.
