# Telecom orchestrator research CI/CD

P9 completes the researcher CI/CD loop with a guarded running-instance
update/rollback adapter on top of P8's create-only OSM path. See
[P9 status/implementation](P9.md) and [separately approved disposable test](P9-LIVE-TEST.md);
live execution stays uncertified by default. P8 adds researcher-driven initial
instantiation. See [P8 status/implementation](P8.md) and
[separately approved disposable test](P8-LIVE-TEST.md).

P7 implements authenticated source-to-registry publication and promotion gates. See
[P7 status/security limits](P7.md) and [separately approved real test](P7-LIVE-TEST.md).
Real infrastructure integration remains unverified; earlier phase records are historical.

The P1–P4 workflow below remains the offline foundation. P5 adds explicit source/build
jobs and approval-gated **disposable-project** catalog adapters, disabled without
private server policy. See [P5 implementation/status](P5.md) and
[isolated live-test procedure](P5-LIVE-TEST.md). No live activation occurred.

This foundation follows the seven frozen [P0 designs](p0/architecture-inventory.md).
It provides one complete **offline** RAN/Core workflow. No production deployment,
registry push, OSM request, automatic recovery or monitoring loop is enabled.
Do not restart the working dashboard to activate this source without separate approval.

## What works

- Private server-owned bearer identities, action roles, tenant-scoped object access,
  workload ownership, independent expiring one-use catalog/update approvals.
- Legacy dashboard API reads require a tenant-bound operator; legacy lifecycle writes
  return 423 even for an operator. Unsafe shell retry/force cleanup is retired.
  Existing Selector/monitoring read logic and layout remain; an operator token can
  be held in page memory. Layer 3 operator endpoints accept that centrally authorized
  identity in the host dashboard; standalone assurance retains its existing auth.
- Version 1 capability declarations for gNB, CU, DU, AMF, SMF, UPF, NRF, AUSF, UDM,
  UDR, PCF, NSSF and explicitly named `x-` extensions. Required role interfaces,
  transport, PLMN/TAC/S-NSSAI/DNN, resource/hardware and monitoring syntax are checked.
- GitHub commit, local Git object, immutable OCI image and HTTPS Helm metadata
  registration. Local Git verifies committed object/tree identity without including
  dirty edits or executing hooks. Registration alone remains DECLARATION_ONLY; the separate P5 job path acquires remote sources.
- Deterministic immutable release/package identities; real bounded local Helm lint
  and rendering; existing repository chart-input policy, KNF/NSD generation,
  reference validation and deterministic archives are reused.
- Idempotent tenant/actor-bound events, private SQLite job/audit journal, short
  transactions, no repeated schema write on reads, bounded busy handling and audit
  integrity checks. This is a new CI journal, not the production assurance database.
- Simulated catalog operations reuse `Catalog.upload/verify` and `osm_onboard.onboard`
  through an in-memory transport. Catalog and instance operations require separate
  approvals, roles and state transitions. Those original endpoints remain simulation-only; separate P5 publication endpoints have explicit private-policy/approval gates.
- Simulated update success/failure/uncertain outcome, single-flight lock, durable
  pending conflicts and explicit operator reconciliation. No automatic retry.
- Immutable RAN/Core interoperability records bind release/image/version identities,
  applicable protocol results and evidence digests/timestamps. Required missing or
  stale metrics make results inconclusive. LIVE evidence is rejected by the sandbox.
- Existing Layer 3 engine/catalog/store create sandbox incidents; a pure read adapter
  links existing L3 snapshots by exact service/cluster/provenance, retaining incident
  IDs and generating investigation/blocked rollback proposals only.
- Internal JSON and escaped HTML qualification reports include release lineage,
  package digests, protocols, telemetry, provenance, incidents and unresolved issues.
  `production_qualified` is always false. Dynamic matrix views label aged evidence
  STALE without rewriting historical records.
- The original six authenticated dashboard views at `/telecom`: External Workloads, Capability
  Registry, CI/CD Releases, Interoperability Matrix, Failure Analysis, Qualification
  Reports. No polling, persistent browser credentials or HTML injection from data.

## Reproduce the complete demo

Prerequisites: Python 3.12, Flask/PyYAML from `ran-selector/requirements.txt`, Git,
Helm 3.21.0; no cluster, registry, OSM credentials or live network is needed.
Use the actual Helm executable, not a local retry wrapper. These commands create
only a fresh private output directory, never production state:

```bash
cd /home/rclab/5g-kubernetes-cicd
PYTHONPATH=scripts python3 -m telecom.demo \
  --output /tmp/telecom-demo-unique-name \
  --helm /usr/local/bin/helm.real
```

Choose a nonexistent output path. On machines with an ordinary Helm install, use
`--helm helm`. The demo executes actual Flask test-client HTTP requests through the
same authentication and API code; no listening service is started. Python HTTP
transport is denied throughout. All chart inputs are local nonprivileged fixtures;
Helm runs offline with `KUBECONFIG=/dev/null`, an 8-second deadline, bounded output
and process-group cleanup. Validation also uses a 256 MiB actual-RSS watchdog,
credential-free environment and `GOMAXPROCS=1`. It does not run Helm install/upgrade or any container.

The sequence is registration → capability validation → sealed RAN/Core releases →
Helm/OSM validation and package generation → separate reviewer catalog approvals →
simulated catalog readback → separate reviewer update approvals → simulated instance
updates → protocol/metric fixture evidence → immutable pair matrix → synthetic
post-update DU incident via existing Layer 3 → qualification reports.

Output includes `demo-results.json`, `qualification.json`, `qualification.html`, four
KNF/NSD archives, and owner-only sandbox authentication/journal files. Demo credentials
are deliberately synthetic and usable only with the isolated demo identity file;
never reuse them or configure that file in a production service. Generated output
must stay outside Git. Package/release identities are deterministic for unchanged
inputs; evidence times, incident UUIDs and report IDs are runtime records.

The sample images under `example.invalid` and synthetic digests in
`examples/telecom/{ran,core}.json` are **not runnable NFs**. Protocol PASS fixtures
are explicitly SIMULATED assertions, not tests of a real F1, NGAP or PDU session.
The final report contains the synthetic incident and remains unqualified.

## APIs and configuration (for a separately approved sandbox activation)

No environment variables are configured or service restarted by this task.
The host dashboard lazily registers APIs; missing configuration fails closed:

| Setting | Meaning |
|---|---|
| `TELECOM_AUTH_FILE` | Owner-only regular JSON file; entries have `subject`, `tenant`, `roles`, `sha256` of a high-entropy bearer token. No plaintext tokens in source. |
| `TELECOM_RUNTIME_DIR` | Owner-only sandbox directory; creates `telecom.sqlite`, lock and separate per-tenant simulation journals. Never point at production assurance state. |
| `TELECOM_WORKSPACE` | Operator-approved local fixture/source root. Paths cannot escape it; chart capture rejects links/nonregular files and bounds traversal/file bytes. Treat all Helm templates as code; do not use a production dashboard host as an untrusted build worker. |
| `TELECOM_LEGACY_TENANT` | Explicit tenant for operator-only existing dashboard APIs. Missing or mismatching binding denies access. |
| `HELM_BIN` | Operator-owned actual Helm executable. No request can select an executable. |

Roles: `researcher` registers; workload owner with `builder` prepares releases;
`reviewer` independently approves; `publisher` simulates catalog; `operator` simulates
updates; `verifier` records evidence/correlates/reports. Reconciliation requires both
operator and verifier roles. All reads are tenant scoped; existing legacy dashboard
reads require the bound operator. Reviewer/operator identities have administrative
access within their tenant. There is no cross-tenant administrator wildcard.

All writes use JSON, a unique `event_id`, authentication and a body <=64 KiB:

| Endpoint after `/api/telecom/` | Required fields |
|---|---|
| POST `workloads` | `manifest`, `event_id` |
| POST `releases` | `workload_id`, approved local `chart` path, `event_id` |
| POST `approvals` | `release_id`, `action` (`catalog`/`update`), exact `target`, `event_id`, optional `ttl` <=300 |
| POST `catalog-simulations` | `release_id`, `approval_id`, `event_id` |
| POST `update-simulations` | `release_id`, `approval_id`, `event_id`, optional `outcome` |
| POST `reconciliations` | `release_id`, exact `operation_id`, `outcome` (`NO_EFFECT`/`COMPLETED`), `event_id` |
| POST `interoperability` | `ran_release`, `core_release`, `tests`, `metrics`, `event_id` |
| POST `failure-analysis` | `release_id`, reviewed `alarm_type`, `event_id` |
| POST `qualification-reports` | `matrix_id`, `event_id` |

GET view paths: `workloads`, `capabilities`, `releases`, `interoperability`,
`failure-analysis`, `qualification-reports`. Report HTML:
`qualification-reports/<id>.html` with the same Authorization header. The dashboard
renders qualification data safely; the offline CLI also exports standalone HTML.

Catalog target: `{"project":"<principal tenant>","mode":"SIMULATED"}`.
Update target: exact `ns_id`, `cluster_uid`, `namespace_uid`, `project_id` UUIDs plus
matching `tenant` and `mode: SIMULATED`. These are sandbox identity declarations,
not proof of live workload ownership. Both protected NS IDs are always rejected.
A live ownership verifier and execution adapter do not exist in this MVP.

Evidence maps require `outcome`, `provenance`, `observed_at`, `evidence_digest` per
protocol/metric. Only SIMULATED or UNAVAILABLE are accepted. F1 is required when the
RAN declares F1; other required checks are NGAP, registration, pdu_session and
user_plane. A complete Core role set is required for SIMULATION_PASS. Metrics must
be declared in the capability contract. Transport/config compatibility is static;
it does not establish peer connectivity. N6/IP has a null port because it is not a
port-based application endpoint.

## Tests and limits

```bash
PYTHONPATH=scripts HELM_BIN=/usr/local/bin/helm.real \
  python3 -m unittest discover -s tests -p 'test_telecom_mvp.py' -v
HELM_BIN=/usr/local/bin/helm.real python3 -m unittest discover -s tests -p 'test_*.py'
```

Pre-existing route/business-logic regressions use the explicit test-only
`legacy_logic_client` adapter after the HTTP boundary changed. Their existing
assertions remain. This is not a production bypass: the new tests exercise real
Flask clients for authentication, tenant isolation, denial before legacy handlers,
operator read compatibility and the full offline workflow. No app TESTING flag,
request parameter or role can enable production mutation.

See [security review](SECURITY.md), [implementation/verification scope](LIVE-VERIFICATION.md)
and [validation evidence](VALIDATION.md). The persisted CI states are PREPARED →
SIMULATED_CATALOG_SYNCED → SIMULATED_UPDATED, or SIMULATED_FAILED/UNKNOWN_OUTCOME.
Unknown outcomes block the target across jobs until explicit simulated reconciliation.
Approvals are one-use; a new operation after reconciliation needs a fresh approval.

The full P0 target architecture is intentionally not claimed complete. Remote
GitHub acquisition/webhooks, remote OCI/chart downloads, executable image builds,
SBOM/signing, real catalog writes/updates, SSO and carrier continuity certification
are deferred. The implemented image-to-package sandbox is the complete demo path;
`build: NOT_RUN` and source authority remain explicit in releases/reports.

P5 execution extensions and their explicit remaining limits are documented in [P5.md](P5.md). The proposed isolated live test is [P5-LIVE-TEST.md](P5-LIVE-TEST.md); it has not been executed.
