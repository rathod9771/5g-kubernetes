> Historical P0 design baseline at c63aa1c. See ../README.md for implemented MVP scope.

# Proposed security and service-continuity model

## Trust zones and principals

| Principal/zone | Allowed authority | Explicit exclusion |
|---|---|---|
| Researcher/source intake | Submit own source/contract and inspect authorized results | No production kubeconfig, OSM admin token, build-host Docker socket or deployment approval |
| Isolated build/test worker | Read immutable source, build in sandbox, publish only to quarantine registry | No network route/credentials to production control or data plane; no reuse of a privileged lab host |
| Artifact promotion service | Verify signatures/evidence and move approved immutable references | No NF lifecycle operations; cannot replace approved bytes silently |
| Catalog synchronizer | Scoped OSM project catalog create/read/verify under approved policy | No running NS update/terminate/delete; referenced catalog artifacts immutable |
| Deployment coordinator | Submit exact approved operations on owned lab/target graph | No researcher-defined arbitrary primitive/URL; protected or foreign targets always denied |
| Observation/Layer 3 | Bounded telemetry reads, local incident/audit operations, authenticated incident decisions | No lifecycle credential or callback; ProductionExecutor stays disabled; LIVE_AUTO rejected |
| Operator/reviewer | Scoped approve/reject/cancel/maintenance decisions | Cannot self-approve an unreviewed protected-target exception through ordinary CI |
| Rancher/platform administrator | Platform lifecycle and disjoint infrastructure ownership | No competing Fleet ownership of OSM workload resources |

Use centralized authenticated identity/roles at every entry point, including CLI,
dashboard, webhook and worker. Derive tenant/project authorization server-side;
untrusted claims in a manifest or labels are not proof. If browser sessions are
cookie based, enforce CSRF controls, secure/same-site cookies and origin checks.
API token callers use short-lived scoped tokens; require TLS and audience checks.
Reverse-proxy auth is useful but must not leave a bypassable unauthenticated backend.
SSO/OIDC/RBAC is proposed, not an existing implementation claim.

GitHub App or short-lived workflow identities should be split across source read,
registry publish, catalog and deploy roles. Branch protection, required reviews,
environment approval and policy checks protect promotion. Public/untrusted PR code
never runs on privileged self-hosted deployment workers or receives secrets. This
separation follows [GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).
A manual workflow event by itself does not prove that checked-out source is trusted.

## Workload ownership and blast radius

Persist owner + tenant + OSM project/VIM/cluster UUID + namespace UID + NS/VNF/KDU
identity + Helm release + Kubernetes UID mapping. Include shared Core, radio,
PVC/secret/CRD and catalog dependencies. Check actual live ownership before every
mutation. Resource names/selectors/labels are useful discovery hints, not adequate
write authorization. A same-name replacement with a new UID invalidates approval.

The two protected NSs and all their dependents are denied by default across all
mutation methods. A foreign external Core/RAN is read-only regardless of nominal
connectivity. Adoption requires a separate reviewed transfer; never apply
`takeOwnership`, force or bulk namespace cleanup. OSM-owned NFs are mutated only
through their certified OSM lane. Platform GitOps excludes those resource sets.

Least-privilege observer RBAC: bounded get/list of relevant resources, no Secrets,
write verbs or pods/exec by default; restricted node reads only where needed. SCTP
inspection currently uses exec in some helpers, which needs separate scoped review
or a purpose-built exporter/owner-supplied evidence. Do not broaden observer roles
to arbitrary exec as a convenience. Deployment privileges are separate from observer
identities; future OSM role limitations must be tested rather than assumed.

Privileged RAN Pods and hardware access require stronger isolation than a namespace.
Use dedicated trusted nodes or clusters, explicit device/hugepage/NUMA/CPU contracts,
resource quotas, admission policies and an enforcing network-policy data plane.
Flannel presence alone is not proof that NetworkPolicy enforcement exists. Researcher
code cannot request a hostPath/socket or hostNetwork exception by self-declaration.

## Supply chain and secrets

Resolve immutable Git/source, OCI, chart, package, toolchain/base/dependency and test
identities. Use signed release manifests and independently verified provenance/SBOM;
digests prove bytes, not trust or vulnerability freedom. Lock third-party Actions
and dependencies for release pipelines, enforce license/vulnerability policy with
expiring reviewed exceptions. Cache artifacts per trust domain to prevent poisoning.
Review Helm hooks, CRDs and rendered security contexts; lint is not a sandbox.

Store credentials and SIM/subscriber material in private secret systems; manifests
carry references and versions, never values. Prefer short-lived brokered access.
Do not copy production kubeconfigs/SIM keys into research CI. Masking is defense
in depth, not permission to log raw API/adapter errors. Bounded diagnostics include
stage/error class/sanitized excerpt/exit code/signal/watchdog reason without tokens,
subscriber strings or unrestricted logs. Retain encrypted backups separately with
access controls; scan source, images and exported artifacts before publication.

HTTPS host/CA verification and no bearer redirects remain mandatory. Restrict source
URLs/egress to approved endpoints, including DNS/IP revalidation against SSRF and
internal metadata targets. Local file intake confines real paths and symlinks to an
approved workspace; do not execute repository hooks or templates on the coordinator.
No shell interpolation of webhook/manifest fields.

## Continuity and audit

Before updates: verify compatible peers, state/schema/secret versions, drained or
preserved sessions, tested backup restoration, rollback evidence, spare capacity
and interruption budget. Do not claim zero downtime from replicas or readiness.
CU/DU and UPF state may be session bound; AMF/SMF/database changes may invalidate
rollback even if Kubernetes can restore a prior template. NF provider, service owner
and operator must agree on an update's actual disruption class.

Audit source → build → artifact → catalog → plan → approval → operation → runtime
verification → soak → rollback lineage. Reuse append-only/hash-linked journal design;
export signed checkpoints to a separate controlled retention sink for stronger
admin-tamper evidence. Avoid uncontrolled SQLite writers and external API waits
inside transactions. Database unreadability is an unknown state, not healthy evidence.

The stop switch is fail-closed: no new operations when identity, approval, ownership,
required telemetry or audit integrity is unknown. It does not promise cancellation
of accepted OSM actions. Independent protected-health/resource checks remain outside
DB dependencies. No automatic Layer 3 executor or remediation is part of this plan.
