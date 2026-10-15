> Historical P0 design baseline at c63aa1c. See ../README.md for implemented MVP scope.

# Phased implementation and acceptance plan

P0 ends with design artifacts only. Every later phase needs its own authorized
implementation scope; any live registration, catalog write, active test, UE use,
service update, monitoring trial or infrastructure change needs explicit approval.
Neither protected Fog-RAN nor Open5GS NS is a default validation target.

| Phase | Deliverable and existing reuse | Exit evidence / activation boundary |
|---|---|---|
| P0 — architecture audit (this task) | Inventory, gaps, contract, adapter design, state machine, security/continuity model, evidence limits | Published baseline verified, design documented, clean unchanged checkout; no implementation or live test. |
| P1 — contracts and safety foundations, offline | Versioned researcher manifest/compatibility model extending registry/state validators; common mutation authorization/protected-resource policy; deny legacy retry path; explicit ownership map; mock job/approval/audit interfaces | Negative tests cover unauthenticated/foreign/protected/ambiguous targets, stale approvals, missing secrets, parser/archive attacks, scope bypasses, every mutation entry point and no effect before authorization. No deployment identity issued. |
| P1b — platform compatibility qualification design/lab | Reconcile Kubernetes version provenance and support lifecycle; OSM/Rancher/Helm/CNI/CSI/ingress version matrix; plan maintained ingress and credential changes in a separate isolated environment | Reproducible supported stack and restore/upgrade rehearsal. Existing reference host remains untouched; production migration is a separate project/approval. |
| P2 — source intake and isolated artifact CI | GitHub App/webhook or local Git snapshot into the same contract; reuse pinned image/provenance/package checks; remote isolated builds, quarantine registry, SBOM/signatures and bounded diagnostics | Synthetic GitHub/local/dirty-tree cases, replay/SSRF/path tests, build exit/timeout/resource cleanup, digest/promotion tampering, no production network/credential reachability. Only approved registry writes, no OSM writes. |
| P3 — catalog reconciliation | Reuse Catalog.verify, immutable package preparation, unused-guard patterns; implement compare/plan/readback and release UUID/hash map | Read-only/dry-run first. Separate sacrificial OSM project for approved create/readback; duplicate/referenced-package/partial-create/unknown-outcome tests. Never synchronize protected or foreign packages. |
| P4 — OSM capability adapter certification | Extend existing Python lifecycle client under common coordinator lock/intents. Use disposable stateless test KDU and actual changed artifact; investigate supported NS action path and descriptor-update semantics separately | Bound operation IDs/payload shape tested on exact installed backend, runtime bytes actually change, successful rollback restores expected bytes, no direct Helm bypass. Timeout/crash/response-loss/partial completion and conflict reconciliation tested. This does not certify a telecom NF update. |
| P5 — telecom composition and verification | Bring RAN against reference Core, bring Core against reference RAN, then bring both; reuse ran_health, native exporters, fresh source evidence and L3 incident correlation | Independent isolated peers; authorized RFsim/test subscribers/UE/data traffic; F1/NGAP/PFCP/SBI plus registration/PDU/data-plane tests, capability/negative cases, privacy and resource budgets. No automatic UE or physical radio execution from a Git event. |
| P6 — controlled running-NF update/rollback | Per-NF capability profile, dependency-aware plan, environment approval, frozen release, exact OSM operation, verification/soak, compatible compensation | Lab stateful update/rollback rehearsal, continuity/session-loss targets met, schema/backup restore proof, capacity/radio safety, bounded failed-rollback escalation. Only then separately approve an owned nonprotected target/maintenance window. |
| P7 — sustained observation and operational hardening | Existing Layer 2/L3 adapters, SQLite/guardian safeguards, cache/provenance and incident identities; off-host audit retention; certified HA coordinator if needed | Approved bounded trial before sustained operation; source freshness/dedup/watchdog and resource/DU-impact evidence, retention/restore and tenant-isolation tests. LIVE_AUTO and ProductionExecutor remain disabled. |

## Initial managed scope and deferred functionality

Start with Helm 3 Kubernetes KNFs/CNFs with explicitly supported descriptor shapes.
An operator-based NF requires explicit CR/CRD ownership and versioned controller
capability tests; VM VNFs, bare metal/PNFs and externally operated endpoints remain
read-only integration or separately scoped future backends. Do not make current
Python/Helm paths pretend to support all NF forms. Kubernetes's normal controller
hierarchy remains: the chosen manager owns desired top-level resources, native/NF
controllers own their declared descendants. The prohibition is against competing
managers reconciling the same desired objects, not against normal Pod controllers.

Defer automatic protected-service recovery, radio/core switching, multi-site HA,
live subscriber migration, generic state transfer, automatic destructive rollback,
and fleet-wide unattended updates. No standard interface name alone certifies any
of those capabilities. H-/Fog experimental profiles remain explicitly experimental
until their own acceptance is complete.

## Required validation matrix for later phases

- **Source/build:** clean Git, local Git without remotes, explicit dirty snapshot,
  submodules/LFS, untrusted hooks/Dockerfiles/charts, disallowed protocols, immutable
  output replay, signature/provenance tampering, timeout/CPU/RSS/disk limit and cleanup.
- **OSM:** project/cluster mismatch, duplicate names, wrong UUIDs, referenced catalog
  mutation, single submission, lost response before/after acceptance, terminal partial
  states, expired auth, malformed bodies, bounded polling, old/new client schema and
  connector/backend variants, operation lock recovery and external drift.
- **Telecom:** missing/mismatched PLMN/TAC/slice/DNN, duplicate cell/DU IDs, unsupported
  peer release, F1/NGAP transport up but application setup down, PFCP absent, one-way
  GTP-U route, MTU failure, missing core discovery, timing/CPU pressure, stale metrics,
  RFsim versus hardware capability, service continuity and data-plane verification.
- **Stateful recovery:** pre/post migration compatibility, encrypted consistent backups,
  changed secret versions, previous chart/image retained, session/bearer loss accounting,
  rollback failure, operator freeze and no automatic deletion of ambiguous resources.
- **Security:** unauthenticated mutation, cross-tenant reads/writes, scope escalation,
  privileged template/host-device request, secret/log leakage, forged webhook/approval,
  approval expiry/drift, resource-name/UID reuse and protected dependency targeting.
- **Monitoring:** missing metric never healthy, source/sample age, STALE transition,
  duplicate delivery/cross-cycle incident identity, correlated scrape faults without
  fabricated outage, simulated events cannot reach deployment executor, independent
  guard safety on DB contention, cancellation and resource-watchdog failures.

## Decision and unresolved inputs

The design is ready for review and an offline P1 scope. General researcher CD and
running-instance updates are **not activation ready**. Resolve G01–G13 gates as
applicable before exposing build/deploy services or issuing production credentials.

Inputs to settle before implementation include: supported NF forms and trust levels;
initial tenant/environment owner; allowed source/registry locations and offline needs;
approved platform version matrix; exact installed OSM connector/source/schema;
continuity/session-loss objectives; permitted maintenance windows; radio/compute
capacity; secret issuer; approval roles; audit retention and backup recovery targets.
No missing answer was treated as authorization in P0.
