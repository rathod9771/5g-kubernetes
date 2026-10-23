# P1 — owned CU/DU detection and scoped SCTP corroboration

Base: published reproducible-installer `e0602ec8067ac20452064fc728f77996f30a691a`.
Development branch: `feature/l3-detection-corroboration`, separate clean worktree.
No live collection, deployment, service restart, RBAC change or recovery execution.

## Existing paths extended

- `adapters.Kubernetes.snapshot`: tri-state readiness, namespace binding,
  inventory completeness, pod address and container runtime identities.
- `adapters.ProtocolEvidence.scoped`: timestamped identity/peer validation.
- `ran_health.scoped_association`: reuse `established` with named address columns,
  role-specific ports and exact expected peer. Legacy parser/helper is preserved.
- `kubernetes.KubernetesReader.sctp`: explicit bounded kernel observer, with
  before/after inventory identity checks; shared subprocess supervision retained.
- `observe.ran_evidence` and `observe.collect`: expected workload observations,
  protocol corroboration, existing catalog events and service-health display.
- `model.normalize`: bounded namespace UID evidence, using existing provenance
  and normalization rules. No raw protocol table enters telemetry or the journal.

The scenario registry and existing validated active state supply expectations;
no new deployment-state file or independent lifecycle controller is introduced.
Existing policies, incident engine, SQLite persistence and executor are reused.

## Workload evidence contract

The caller must supply state validated through the existing runtime/state path.
This is not authentication for arbitrary caller-supplied state or an independent
live attestation of its namespace/cluster UID. Observations bind to that context,
exact namespace, active NS label, canonical registry app, component role and pod
UID. Missing namespace UID or incomplete/stale inventory prevents trustworthy
availability events. The expected app must have exactly one matching pod.

- Ready=True: READY; Ready=False: NOT_READY.
- Missing/Unknown Ready condition or terminating pod: UNKNOWN.
- No matching expected workload in a complete inventory: MISSING, not failure.
- Failed API, pagination, truncation, conflicting candidates or stale inventory:
  unavailable/unknown evidence, never healthy or failed by inference.

Explicit READY/NOT_READY observations produce resolved/firing CUUnavailable or
DUUnavailable events. Missing/unknown does not emit a failure or clearing event.
Resource keys include pod UID, so replacement cannot inherit old persistence.
Older alarms are retained and age stale; replacement does not fabricate a clear.
Namespace UID survives bounded normalization for incident/audit evidence.

## Protocol contract and corroboration

`collect(..., protocol_reader=observer)` is an explicit dependency-injection
interface. Its default is None. The existing CLI does not enable the observer,
change polling, grant exec permission, or start another monitoring service.
`KubernetesReader.sctp` is a compatible observer for future separately approved
activation. Custom observers are trusted internal adapters, not a public upload
route; declaring LIVE in JSON is not cryptographic source authentication.

Observer input: owned source pod, current expected peer pods, bound context and
active NS. Output: provenance LIVE, original observed_at, namespace/namespace UID,
cluster UID, NS ID, pod name/UID/IP, RAN container, peer UID/IP pairs and bounded
kernel table. Exact source/container/peer binding and age 0..120 seconds are checked.
SIMULATED, stale, future, malformed, mismatched or replaced evidence cannot
corroborate a live event. Provider inputs are copies, not mutable collector state.

The supplied reader re-lists namespace pods before and after the exact command
`cat /proc/net/sctp/assocs` in the selected RAN container. Pod/peer UID, IP, labels,
readiness, phase, container specification and runtime identity must remain stable.
No shell, arbitrary remote command, retry or mutation command is added.

- CU F1: established state 3, local port 38472, expected DU address.
- DU F1: state 3, remote port 38472, expected CU address.
- CU NGAP: state 3, remote port 38412, uniquely owned AMF peer address.
- F1 requires agreeing fresh CU and DU observations. Contradiction is UNKNOWN.
- A valid empty table can establish association absence. Malformed evidence cannot.
- An established expected-port association to another address is UNKNOWN unless
  the intended peer is also verified. This avoids treating unverified VIP/NAT
  paths as confirmed loss or accepting unrelated sockets as health evidence.
- SCTP never proves F1/NG Setup, registration, PDU success or measured user plane.

Existing policy conditions remain unchanged: DU recovery proposal needs explicit
not-Ready plus F1 absence; CU proposal additionally needs NGAP absence. Contradictory
or missing evidence falls back to observe/notify. NGAP investigation lacks an
independent core-health assertion here and therefore remains observation-only.
No operator approval can enable ProductionExecutor.execute().

## Bounds and safety

Default collection retains its original three-list budget. The opt-in SCTP
reader permits at most two observations, each with two identity inventories and
one exact kernel read: at most six additional subprocess attempts, no retries.
All reuse existing timeout, RSS, output, exit-status, cancellation and PID-safe
cleanup protections. SCTP evidence is limited to 64 KiB and 256 association rows.
A raw reader failure can be retained by existing sanitized process diagnostics;
raw table contents and adapter exception messages are not journal evidence.

No production configuration, workloads or live RBAC were inspected or modified
for this feature. No live kubectl/Helm commands or lifecycle APIs were executed.
No changes to lifecycle.py, state_schema.py, telecom/, workflows, historical
watcher, deployment scripts, SQLite schema or policies. No existing test removed.

## Offline acceptance

56 new tests extend the existing live-adapter and Kubernetes test modules:
healthy/unhealthy/unknown/missing workloads; ownership and namespace mismatch;
pagination and duplicate candidates; UID/peer/container replacement; correct and
wrong ports/peers; malformed/absent/stale/future tables; API errors; conflicting
sources; policy fallback and corroborated proposals; replay deduplication;
incident persistence; namespace identity normalization; exact reader commands,
readback, budgets and supervisor reuse; disabled production executor.

Run from the feature worktree with PYTHONDONTWRITEBYTECODE=1 and
PYTHONPATH=tests:scripts. The final reviewed regression set is:

```
python3 -m unittest test_assurance test_assurance_diagnostics \
  test_assurance_guardian_entry test_assurance_kubernetes \
  test_assurance_live_adapters test_assurance_memory test_assurance_periodic \
  test_assurance_sqlite test_ran_health test_publication_safety \
  test_monitoring test_dashboard_workspace test_dashboard_switch -v
```

Final result: **258 tests passed in 177.617 seconds**: 202 existing regressions
and 56 new tests. The standalone new-test run also passed all 56. The refactored
self-contained reader fixture separately passed its eight tests. Python AST,
allowed-path checks, credential-pattern scan and git diff --check passed.

The 16 existing sandbox simulations remain in test_assurance and cannot call
production remediation. Offline fixtures are not HP/Dell verification evidence.

## Remaining limits and proposed Phase 2

No live protocol acquisition or runtime compatibility acceptance is claimed.
First-phase transport topology is one unambiguous CU and DU in one namespace.
Multi-DU/H-CRAN relationships, host networking, service VIP/NAT endpoint mappings,
multiple/ambiguous AMFs and nonstandard image identities need explicit reviewed
contracts; no scenario or chart was changed to force support.

Phase 2 should add AMF workload/application distinctions and OSM terminal-failure
alarm translation through existing adapters. Coordinate pending_update/shared-lock
and telecom correlation interfaces with Claude before changing those shared
interfaces. Source qualification and any bounded observe-only live verification
require separate approval. CI/CD, release management and rollback remain Claude's
responsibility; production remediation stays disabled.
