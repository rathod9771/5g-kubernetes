# Implemented scope versus future verification

| Capability | MVP status | Remaining work |
|---|---|---|
| Authentication/tenant/role/owner policy | Implemented and tested offline | Real identity issuer/rotation, TLS gateway and operational RBAC rollout need separate approval. |
| GitHub/local Git registration | Implemented immutable metadata; local committed object/tree read | Authenticated webhook/remote source acquisition, dirty-snapshot intake and repository/build isolation are not implemented. |
| OCI/Helm registration | Immutable declaration and local chart packaging implemented | Remote download, registry content/signature/SBOM verification and image execution/build workers are not implemented. |
| Capability registry | Versioned immutable provider declarations with static policy checks | Vendor parser/runtime/hardware/3GPP acceptance and actual monitoring support need live/lab evidence. |
| Release/KNF/NSD pipeline | Deterministic local Helm lint/render and package bytes with explicit source/build status | Full source-to-image automation and signed environment promotion are deferred, not mislabeled as completed verification. |
| OSM catalog | Existing client reused through in-memory simulation, separate approval/readback | Scoped real catalog create/readback/immutability and installed v19 compatibility need disposable-project certification. |
| Instance updates | Approved simulation, locking, unknown-state blocking/reconciliation | No production adapter. OSM KDU update/rollback and actual workload ownership/state continuity need separate lab implementation/certification. |
| Interoperability | Immutable version/digest pair records; simulated protocol/metric evidence and freshness | Real F1/NGAP/N4/registration/PDU/user-plane tests require approved peers, test subscribers and UE/traffic authorization. |
| L3 correlation/reports | Existing engine/snapshot linkage reused; proposal-only JSON/HTML reports | Real release-to-observation identity mapping and read-only live incident ingestion need scoped integration verification. No remediation activation. |
| Dashboard | Six added authenticated views; original read logic preserved for bound operator | No service restart performed; active dashboard activation, reverse proxy/CSP/identity configuration require separate review. |
| Stateful rollback / carrier continuity | Proposal only; deliberately unavailable | Schema/data/session migration, retained prior release, tested backups, radio capacity, traffic steering and measured continuity/rollback evidence. |
| Platform compatibility / scale | Existing reference baseline retained | Kubernetes pin/Rancher/OSM/CNI/CSI/ingress support reconciliation, HA and distributed fencing are not solved by this MVP. |

Protected Fog-RAN `2c128db3-e27e-4dbe-8854-17751ba17680` and Open5GS
`1b1cb300-b571-43f2-a567-6540895d7d0f` remain excluded from trials and mutations.
Current protected-service health was not queried. No Kubernetes/OSM resource,
service, live catalog, legacy watcher, LIVE_AUTO or remediation setting was changed.
No Git commit or push is authorized/performed.
