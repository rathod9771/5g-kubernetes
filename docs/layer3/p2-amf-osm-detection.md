# Phase 2 — AMF workload and OSM operation-failure detection

Extends the existing assurance implementation on
`feature/l3-detection-corroboration`, following Phase 1 commits `6fe120c` and
`62ac76e`, based on published revision `e0602ec`.

## Phase 1 verification

Both commits were inspected. Their eight-file scope matched the P1 report, and
the isolated worktree was clean before P2. A fresh run passed all 258 tests in
164.880 seconds, including 56 P1 tests and the 16 existing sandbox simulations.
The executor, policies, CI/CD, shared lifecycle modules and historical watcher
were unchanged from the baseline. No production or experimental checkout edited.

## AMF evidence

`observe.amf_evidence` consumes the existing `Kubernetes.snapshot` output.
`observe.collect` exposes `amf_evidence` and `services.amf`, then feeds existing
AMFUnavailable events into the existing engine/persistence path.

Identity requires:

- Existing validated active-state context: cluster UID, namespace name/UID.
- Exact bound core NS ownership label and namespace.
- Exact application label `app.kubernetes.io/name=amf` or `open5gs-amf`.
- Nonempty bounded Helm application instance label and a pod UID.
- No conflicting component role and exactly one matching candidate.

This is authoritative API metadata in a trusted runtime context, not independent
cryptographic attestation of arbitrary state or label assertions. No new state
file, workload adoption or ownership-management implementation is introduced.

Ready=True emits a resolved workload observation; Ready=False emits firing.
Missing workload, Unknown/missing readiness, deletion, duplicate identity,
incomplete/paginated inventory, stale/future snapshots or failed API reads never
produce failure or clearing assertions. Source time and UID are retained; a
replacement cannot inherit an older pod's persistence/clearing identity.

Application health and UE registration remain UNAVAILABLE. No service_degraded
assertion is invented from pod readiness. The existing AMF policy still supplies
priority, persistence and observe/notify action; it never restarts the core.
Existing general core/RAN health and Phase 1 protocol behavior are preserved.

## OSM evidence

`adapters.OSM.snapshot` uses the existing authenticated read-only client. Its
request paths, three-GET budget, authentication policy, timeouts and no-retry
behavior are unchanged. No lifecycle/catalog method is added or called.

Failure qualification requires exact NS ID; the runtime project's ID in the NS
`_admin.projects_read` metadata; exact returned operation ID; operation-to-NS
binding; nonconflicting operation-project metadata if present; no conflicting
current operation ID; valid start/transition times; and a fresh source read.
Absent metadata does not become verified ownership. Conflicting id/_id aliases
are rejected. A legacy NS snapshot can remain available while its operation
qualification is explicitly UNKNOWN/UNAVAILABLE.

| Evidence | Classification and alarm behavior |
|---|---|
| Verified FAILED | Firing OSMLifecycleOperationFailed assertion |
| Verified COMPLETED | Resolved assertion for that exact operation identity |
| PROCESSING / STARTING | Processing; neither failure nor clearing |
| FAILED_TEMP, partial, rollback or unknown states | UNKNOWN for terminal-failure detection; no failure/clearing |
| API error, wrong ownership/identity, missing timestamps | Unavailable/unknown; no operation-failure inference |
| Source observation older than 120 seconds or in the future | No alarm update or clearing |

`observe.osm_failure_events` follows only the existing recorded
active_operation_id of the active RAN NS. It does not discover or adopt operations,
invent a core-operation field, or traverse pending_update operations.

Freshness is the time of the bounded authenticated state observation, not the
historical statusEnteredTime. A freshly confirmed FAILED operation can have an
old transition time. Both original operation timestamps survive normalization as
operation_started_at and operation_state_entered_at; the fresh observation time
separately drives existing persistence. Stale cached observations cannot update
or clear incidents. COMPLETED does not prove protocol or service health.

Alarm identity includes exact operation ID and NS scope. Repeated observations
retain incident identity. Processing/API loss cannot clear a failure. A confirmed
COMPLETED state for the same operation must satisfy existing clearing persistence.
A different successful operation cannot silently clear the older failed identity;
older unresolved conditions require reconciliation or age stale.

## Tests and safety

39 new tests extend test_assurance_live_adapters: 15 AMF tests and 24 OSM tests.
They cover healthy/unhealthy/missing/ambiguous workloads, ownership/application
identity, incomplete inventory, API errors, freshness, terminal and ambiguous
operation states, timestamps, collector integration, duplicate suppression,
clearing, replacement/new-operation identity and disabled production execution.
Final broad regression run: **297 passed, 0 failed, in 176.034 seconds**
(258 preserved P1 regressions plus 39 new tests). A separate final P2 run
passed all 39 in 4.309 seconds, including strengthened collector integration
assertions. The existing 16 sandbox simulations are covered by test_assurance.
Syntax, allowed-path checks, credential-pattern scan and git diff --check passed.

No live API, kubectl, Helm, experiment or service operation was executed. All
new fixtures are synthetic and use temporary journals; they are not production
verification. Existing policies, engine, SQLite schema, execution denial,
read-only OSM transport, CI/CD, shared lifecycle and historical watcher remain
unchanged. No push or merge. Original checkout fingerprints are checked again
before completion. No private runtime evidence or credentials are committed.

## Deferred interface requirements and Phase 3

Coordinate with Claude before changing shared code. Future integration must:

1. Use lifecycle.blocking_reason under the shared lifecycle lock, including
   pending_update, pending_instance and experimental_recovery.
2. Receive exact reconciled operation IDs and their NS/project/release bindings
   from the existing controller; do not create another tracker or retry engine.
3. Reuse telecom/correlation.py snapshots, matching NS/cluster/provenance and
   incident IDs; temporal association is not release causality.
4. Consume controller-provided reconciliation/rollback eligibility as proposal
   evidence only. Never implement rollback execution in assurance.

Recommended Phase 3: separately review CPU-contention and insufficient-capacity
observation semantics, and coordinate those interfaces. Any live qualification
requires separate approval. HA/custom AMFs, nonstandard ownership metadata and
OSM response variants remain unverified. No automatic recovery is enabled.
