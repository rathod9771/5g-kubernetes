> Historical P0 design baseline at c63aa1c. See ../README.md for implemented MVP scope.

# Proposed researcher onboarding contract

Design only. No schema, API, runner or deployment is implemented in P0.
Extend the existing scenario registry/package validators; keep provider identity
separate from the selection of an environment and from deployment authorization.

## Supported participation modes

| Mode | Platform may manage | Peer and continuity responsibility |
|---|---|---|
| Bring RAN | Researcher's approved packaged gNB or CU/DU set in an isolated target | Bind a declared external/reference Core through N2/N3; never mutate that Core without its owner's separate approval. |
| Bring Core | Approved Core NF graph, persistence and network resources | Bind a separately owned RAN; private subscriber data is provisioned through an approved secret/data workflow. |
| Bring both | Approved independently identified RAN and Core graphs | Validate both compatibility directions, graph ordering, state migration and affected services. |
| Attach existing external NF | Read-only endpoint/capability inventory and approved probes | External owner retains lifecycle authority. No namespace discovery, adoption, restart or deletion by implication. |
| Bring image/chart only | Verified immutable artifact and contract validation | No source build claims; existing image origin and license/security provenance required. |

A later managed adoption is its own reviewed ownership-transfer procedure. It must
not happen through matching a name, label, chart path or external endpoint.

## Required release manifest fields

| Contract group | Required content and rejection rule |
|---|---|
| Identity | Schema version; provider/tenant/project; component and release IDs; owner/support contact; license; NF roles and dependency graph. Names are not global resource identities. |
| Source | `github`, approved Git server, or local Git/snapshot; canonical repository identity, exact commit and tree/submodule/LFS identities; build-context paths and Dockerfile; dirty-tree policy. A branch/tag is a trigger, never the artifact identity. |
| Local Git | Resolve one explicit path under an approved workspace. Record clean commit or explicitly consented dirty snapshot with content digest and file allowlist. Do not stash/reset/clean user work or execute hooks. A dirty snapshot must not be attested as pristine commit bytes. |
| Existing artifact | OCI repository and manifest/index digest, selected platform/config digest; chart archive digest/dependencies; SBOM and signature/provenance references. Reject mutable release references even if an optional display tag exists. |
| Build contract | OS/architecture, CPU ISA, pinned bases/toolchain/dependency lock hashes, build args allowlist, reproducible recipe, tests and time/CPU/RSS/disk/network budgets. Hardware-specific builds explicitly advertise requirements. |
| NF topology | gNB, CU, DU, CU-CP/UP, AMF, SMF, UPF etc.; placement/affinity/NUMA/hugepages/devices; singleton/replica constraints; software capability and 3GPP release/profile claims with evidence. Unknown roles cannot bypass interface validation. |
| Interfaces | Provides/requires with peer identity, transport/address family/endpoints/ports, DNS/service exposure, TLS/API versions, PLMN/TAC/S-NSSAI/DNN, cell/radio parameters, GTP/PFCP routes/MTU; avoid embedding private user credentials. |
| Packaging | Validated chart, values schema and KNF/VNFD/NSD references; OSM backend/Helm/API compatibility; cluster-scoped objects, CRDs/hooks/Jobs and external side effects fully enumerated. |
| Ownership | Deployment manager (`osm-kdu`, separately certified OSM declarative lane, or `external-readonly`); tenant/OSM project/VIM/cluster/namespace bindings. Actual UUIDs/UIDs are resolved and checked at planning time. |
| Privileges | Requested RBAC, capabilities, host paths/network/PID modes, devices, egress and storage access. Default deny; explicit review for radio/hardware privileges, dedicated trust boundary when isolation requires it. |
| State and storage | PVC/data owner, database/schema versions, migration compatibility, backup/restore verification, retention, secret references and data sovereignty requirements. No automatic PVC deletion. |
| Update contract | Disruptive/rolling/staged support with evidence; immutable fields; session/state transfer or drain primitives; validated OSM capability; maintenance budget and compatible old/new peer versions. Absence means no running update. |
| Rollback contract | Previous artifact/values/migration identities, supported rollback method, restored-state validation and point of no return; owner approval. No rollback-safe claim without a successful lab rehearsal. |
| Validation | Static/build/package/interface tests, allowed active tests, fixtures, required metrics/units/source/freshness, expected identity changes, acceptance windows and explicit interruption/session-loss budgets. |
| Observability | Readiness/liveness/startup probes, metrics/log adapters, source timestamp availability, bounded labels, exporter auth and retention, operational runbook. Missing optional metrics stay UNAVAILABLE. |

Repository acceptance, artifact build, catalog publication and running deployment
are four separate approval/status boundaries. A provider cannot self-approve a
privileged exception or claim its tests as independent platform verification.

## Source intake and provenance

GitHub events: verify sender installation/repository scope, webhook signature and
unique delivery ID; resolve commit independently; deduplicate replayed events.
Local Git uses the same manifest, build and validation path after immutable source
capture. Private Git uses narrowly scoped read identities outside manifests/logs.
Disallow arbitrary URL schemes, credential-bearing URLs, unsafe archive paths,
symlink escapes and unapproved recursive submodules. Treat Dockerfiles, Helm
hooks, build scripts and test code as executable untrusted input.

Build once on an isolated worker. Emit a release manifest binding source snapshot,
recipe/toolchain, OCI digest, SBOM, signatures, chart/package hashes and test-evidence
hashes. Promote exactly those bytes across environments; never rebuild under the
same release identity after approval. Registry/catalog readback must match hashes.
Air-gapped import follows the same integrity/ownership checks and uses explicitly
approved archives, reusing the existing verifier patterns.

## Telecom acceptance ladder

1. **Static accepted:** contracts, schemas, secure inputs, interface compatibility,
   Helm lint/render, descriptors and resource policy pass. No RF/core health claim.
2. **Artifact accepted:** isolated build and dependency/security/provenance checks
   pass. Image starts only in an authorized sandbox with no production connectivity.
3. **Integrated lab accepted:** compatible reference peers; CU/DU/NGAP/PFCP/SBI
   validation as relevant. No production subscriber or radio use by default.
4. **Service accepted:** separately authorized UE test establishes registration,
   authentication, PDU session, bidirectional traffic and required performance.
   Measure throughput/latency/loss/jitter, RAN CPU PSI/deadlines and session counters;
   define provider/environment thresholds rather than invent universal values.
5. **Update/rollback accepted:** altered release actually observed, continuity budget
   met, old/new peers and schemas compatible, rollback rehearsal successful.
6. **Environment approved:** independent owner/security/operator approval bound to
   exact release, target graph, disruption plan, time window and rollback envelope.

An external provider without sufficient test visibility can be cataloged or kept
read-only, but cannot receive a false service-accepted or update-safe designation.
No UE is deployed or any active probe performed in P0.
