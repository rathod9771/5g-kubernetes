> Historical P0 design baseline at c63aa1c. See ../README.md for implemented MVP scope.

# P0 gap analysis and stop gates

Severity here is for enabling external researcher onboarding and controlled CD,
not a claim that a live exploit or service outage was observed. The published
baseline is useful for controlled laboratory operation; it is not a certified
multi-tenant telecom CI/CD platform.

| ID / priority | Confirmed source evidence or uncertainty | Required gate / correction in a later approved phase |
|---|---|---|
| G01 Critical before external exposure | `backend.py` mutation routes `/api/deploy`, adoption/reconciliation have no in-application caller authorization; the only global before-request hook limits monitoring load. App binds all interfaces with Flask's development server. External auth/proxy/network controls were not inspected. | Authenticated identity, role/tenant authorization, approved operation intent and protected-target checks at every mutation boundary; production HTTP serving, TLS and CSRF protection if cookie sessions are used. Do not expose existing mutation routes to researchers. |
| G02 Critical before CD | `_deploy_verified` can terminate/delete the currently active RAN before creating a replacement. L3/recovery protection and core-state checks do not apply a universal protected-Fog/Core deny rule to every manual mutation path. | One policy-enforced mutation boundary, protected NS/resource denylist and dependency graph; explicit maintenance/disruption approval. No current protected NS is a trial target. |
| G03 Critical before adapter reuse | `scripts/instantiate-with-retry.sh` contains public demo authentication, TLS verification bypass, automatic termination/forced deletion/retry and treats PARTIALLY_COMPLETED as completion. It is not the corrected Python lifecycle path. | Exclude this legacy helper from every future pipeline; retire/gate it in a separately approved correction, add source-path regression checks. No automatic destructive retry or force deletion. |
| G04 High | `Catalog.upload` uses same-identity PUT when an entry exists. Experimental scenarios have unused/exact-ID guards; ordinary scenarios lack a general live-reference immutability guard. | Default append-only release identity and exact version/hash mapping. Active/referenced artifacts immutable; replace only proven-unused artifacts with separate approval. |
| G05 High | Local update findings record a same-package/no-diff CHANGE_VNFPKG trial with no Helm upgrade; generic upgrade/rollback methods are absent from the current client. | Version/backend-specific capability certification with a real changed artifact and workload verification. Unsupported paths stop; never synthesize success from HTTP 202/COMPLETED. |
| G06 High | Source build runs privileged Docker/tool installation, all host cores, native CPU code generation, local containerd import. | Isolated build nodes, explicit CPU ISA/platform targets, digest artifacts, signing/SBOM, fixed budgets; no builds or daemon access on DU/production host. |
| G07 High | OAI CU/DU digest deferral is explicitly accepted by current image policy. Source pin alone does not pin toolchains/base layers; monitoring templates install Python dependencies at startup. | Immutable resolved image/chart/dependency digests for promoted releases; provenance/signature/license/vulnerability policy; prebuilt pinned exporters. Reference deferrals do not pass researcher release gates. |
| G08 High | Single active-RAN/core YAML and same-host flock are not tenant isolation, a multi-NF service graph, HA coordination or distributed fencing. | Owner/tenant/project/cluster/namespace/NS/VNF/KDU/release/resource-UID graph and durable CI intent records; one writer initially, fenced durable lease before multiple workers/hosts. |
| G09 High | No implemented immutable GitHub/local source ingestion, artifact registry promotion, environment-bound approval or general researcher manifest. | Versioned onboarding contract, source snapshot provenance, authenticated/deduplicated events, build/publish/deploy credential separation. |
| G10 High | No validated telecom-aware running update/rollback preserving UE/bearer/PFCP/database state. Existing dashboard replacement is disruptive. | Explicit disruption classes, version/schema compatibility, retained prior artifacts/backups, lab rollback proof and maintenance approvals; no hitless claims. |
| G11 High | `reference-versions.json` pins Kubernetes 1.29.15. Upstream lists 1.29.14 as final and EOL 2025-02-28. Rancher 2.15.1 support matrix does not establish support for this kubeadm combination. | Reconcile vendor/build provenance and certify a supported Kubernetes/Rancher/OSM/CNI/CSI/Helm matrix in an isolated environment; no production upgrade during P0. |
| G12 High | Installer pins community ingress-nginx controller 1.10.1/chart 4.10.1; upstream announced retirement in March 2026. | Qualify a maintained ingress replacement or documented support arrangement with OSM TLS/routing tests; separate planned migration, not an immediate live edit. |
| G13 High | Existing radio templates use privileged containers. Namespace boundaries alone are insufficient isolation for hostile NFs or builds. | Trust classification, dedicated nodes/clusters for privileged/radio workloads, minimal devices/capabilities, explicit exceptions and enforced network isolation. Confirm policy enforcement with the actual CNI. |
| G14 Medium/High | RAN exporter picks the first Running matching Pod, labels by RNTI only and does not age out stale samples in code. Startup installs unpinned dependencies. | Scope by tenant/NF/Pod UID and series lifecycle, bounded cardinality, timestamps/sample-age and restart semantics. Unchanged scrape freshness is not radio-measurement freshness. |
| G15 Medium/High | KPM is deliberately UNAVAILABLE; TCP-connect latency is not UE data-plane performance. Four control-plane scrape problems are historical evidence, not fresh health checks. | Capability-based tests and provenance; retain monitoring-failure correlation without claiming control-plane outage. Required missing telemetry blocks promotion. |
| G16 Medium/High | SQLite audit has append-only triggers/hash links and bounded transactions, but administrator replacement remains possible; no general job outbox/HA retention system. | Reuse safe transaction patterns; add durable CI execution records and external audit checkpoints/retention when approved. Keep telemetry journal independent of deployment credentials. |
| G17 Medium | Installer docs contain earlier disabled-GitOps wording superseded by a later true-setting note. Code reproduces GitOps environment with placeholder repository URLs, not working researcher source sync. | Reconcile documentation and explicitly select OSM KDU versus declarative application lane. Do not install another Flux/Fleet/Argo controller to hide missing repository configuration. |
| G18 Medium | Hosted workflow validates offline; self-hosted workflow is manually read-only. Actions use version tags and Python requirements lower bounds. Hosted execution/runner isolation not inspected. | Immutable workflow/dependency pins, branch/environment controls, short-lived app identity, isolated runners and artifact verification. Keep untrusted PRs away from deployment credentials. |
| G19 High for carrier claims | Reference installer is explicitly a single-node laboratory candidate; four physical cores constrain build, soak and spare-capacity tests. | Reserve DU resources, off-host builds/tests, capacity admission and realistic availability objectives. Separate HA/multi-site qualification; do not claim carrier-grade continuity from this lab. |

## Evidence interpretation

The OSM local no-diff test does not establish the cause of all update behavior.
OSM v19 documentation describes both CHANGE_VNFPKG behavior and KDU upgrade/rollback
primitives. A same-descriptor control case, software-version conditions, API payload
shape, connector backend and actual artifact change must be distinguished. Current
runtime support remains UNVERIFIED. No guessed API was exercised.

Sources: [OSM v19 NF/KDU lifecycle documentation](https://osm.etsi.org/docs/user-guide/v19/how-to/nfv/deploying-network-services.html),
[Kubernetes release history](https://kubernetes.io/releases/),
[Rancher 2.15.1 support matrix](https://www.suse.com/suse-rancher/support-matrix/all-supported-versions/rancher-v2-15-1/),
[Ingress NGINX retirement](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/).

No blocker was corrected during P0. These are prerequisites for later implementation
and deployment authorization, not permission to change protected services now.
