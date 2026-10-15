# L3-4A SQLite correction — offline work complete

The affected file is `/home/rclab/5g-kubernetes/.runtime/assurance.sqlite` (159744 bytes,
100 audit records during investigation), journal_mode DELETE, header versions 1/1.
The original journal, incidents and failed-trial evidence remain unchanged. No live
collection/trial, dashboard reload, service restart, WAL migration or production
SQLite reset was performed. Safety remains OBSERVE_ONLY, watcher inactive, LIVE_AUTO
false, production executor DISABLED; current dashboard PID 578791 / NRestarts=0.

## Exact confirmed cause and evidence limits

The guard's `journal()` used a read-only connection with a three-second busy timeout.
One of its three SELECTs (telemetry, governance, or periodic audit) could not acquire
SQLite read access and raised `database is locked`. The log did not capture the exact
SELECT, SQLite extended error code, kernel lock state or transaction owner, so those
cannot be recovered as facts. Collector PID 1278489 was the contemporaneous writer
candidate: audit rows 85–98 show telemetry, governance and twelve observation commits
through the failing interval; supervisor PID 1278485 wrote start/cycle/stop records.
Dashboard writers are another possible contributor. No ownership trace proves which
transaction held the blocking lock at the precise failing instant.

The confirmed access design creates contention: DELETE-mode commits can require
exclusive database access; every dashboard snapshot/read reserves a writer using
BEGIN IMMEDIATE; Store construction repeatedly invokes schema SQL (executescript also
implicitly commits); the collector previously committed telemetry/control/each alarm/
refresh separately with FULL sync. The guard read ran before its protected-check branch
and any SQLite exception aborted it. Writer connections already closed in finally.
The guard's connection context manager committed/rolled back but did not explicitly
close; delayed cleanup is a risk, not a proven lock owner. No external API call was
found inside the collector/engine SQLite write transactions. The guard needs no database
write access. Collection/lifecycle file locks are distinct from SQLite file locks and
do not serialize dashboard reads or commits. Trial exit released the process locks;
no live owner is available to trace retrospectively.

Fault injection reproduces unavailable reads under an explicit DELETE-mode exclusive
writer. RESERVED writes permit committed-state reads; WAL on test copies permits
reader concurrency even during the injected writer. This demonstrates why WAL can
help but does not fix needless writer reservations, fragmented collection commits,
connection lifecycle or critical-check ordering on its own. WAL is **not required or
applied** by this correction; default DELETE behavior is tested.

## Source changes and smallest supported correction

- `scripts/assurance/store.py`: validate existing schema without repeated DDL; coherent
  mode=ro/query_only read snapshots with explicit closure, 0.2 s timeout; serialized
  BEGIN IMMEDIATE writes keep existing 3 s waits/FULL sync. Nested engine mutations
  join one outer transaction, and nested errors mark it rollback-only.
- `scripts/assurance/api.py`: snapshot telemetry and objects in one read transaction;
  no extra writer-reserving read. Busy journal returns HTTP 503 / UNAVAILABLE with
  LIVE_AUTO false and executor DISABLED, not healthy data.
- `scripts/assurance/cli.py`: all remote reads finish before an atomic local transaction
  encompassing telemetry, alarms, incidents, actions and audit. Cancellation rolls
  back the entire mutation, and transaction bodies/lifecycle operations are never retried.
- `scripts/assurance/guard.py`: bounded read-only retries (default total 0.15 s,
  25 ms per wait), explicit cleanup and UNKNOWN/UNAVAILABLE on contention. Critical
  callbacks run first; no health is inferred from a locked/missing journal.
- `scripts/assurance/guarded_trial.py`: persistent approval-only companion replacing
  the temporary guard, inert on import. Independent resource/protected checks precede
  SQLite reads. Unknown visibility creates PAUSE; persistent unknown >5 s or critical
  failure requests shutdown. It starts one bounded periodic process and never restarts it.
- `scripts/assurance/periodic.py`: read-only governance, PAUSE interlock, fail-closed
  unavailable governance, and owner-only result evidence even if stop audit is busy.
- `tests/test_assurance_sqlite.py` and periodic fixture/tests: real lock/process/cancellation
  faults, closed-connection/read concurrency, atomic rollback and pause behavior.
- This report, executable approval-only preflight, activation procedure and JSON evidence.

No policy thresholds, protected NS guards, executor implementation, monitoring targets,
RBAC, source timeouts or resource limits were weakened. No production database mode
change or incident migration was performed.

## Integrity and regression evidence

**205 tests passed**, including 18 SQLite fault/concurrency tests and one additional
periodic pause-interlock test; all 16 existing simulations passed. Coverage includes
reading dashboards/guards during writes, concurrent alarm writers, transitions and
clearing, deliberate busy locks, short guard deadlines, cancellation and process kill,
rollback/journal recovery, repeated cycles/deduplication, connection cleanup, WAL-copy
compatibility, API calls outside transactions and critical-check/watchdog independence.
Existing CPU/RSS/output/time watchdog regressions remain passing. Compilation and
whitespace checks passed.

A temporary backup of the production journal was cancelled mid-collection: the entire
new transaction rolled back to the original snapshot. Five subsequent fresh-event
cycles reconciled the prior L3-4 partial state without duplicate incidents. All four
incident IDs, existing alarm IDs, incident objects/histories, old audit prefix and
correlation group were preserved, and the hash-linked audit verified. Production
journal SHA256 was unchanged. The failed L3-4 trial remains an interrupted historical
record; it was not retroactively marked successful or erased. Data reconciliation
requires fresh observations, not replaying stale evidence as LIVE.

## Offline performance

- Injected guard lock wait: mean 0.100121 s,
  maximum 0.100150 s with a 0.1 s test budget.
- Busy guard CPU: mean 0.001724 CPU s; benchmark interpreter
  peak 19.00 MiB. Retries sleep; they do not spin.
- Atomic copied-journal update (four persistent targets plus redelivery): mean
  0.348778 s end-to-end transaction/write phase, mean 0.061822 CPU s,
  test-process peak 27.80 MiB.
- Isolated durable audit-write latency: mean 0.185133 s; full samples in
  `lock-performance.json`. Timings include fsync and host scheduling variation;
  no instrumentation retrospectively separates BEGIN wait, body and commit in L3-4.

These are offline processing/lock measurements, not full live collector timings or
actual sustained PSI/DU effects. API/network stages are not executed in the benchmark.
The transaction now commits once instead of per-alarm commits, reducing sync/lock
opportunities; CPU overhead measured for the database correction is small, but live
four-core pressure and end-to-end completion remain unverified pending approval.

## Corrected trial and risks

`activation.md` provides the exact procedure: backup/preflight, dashboard-only reload
under explicit approval, then `python -m assurance.guarded_trial`, 60-second interval,
maximum ten attempts / 600 s, no overlap/restart/remediation. No command was executed
from that procedure during this phase. Both production and historical evidence are
retained; rollback must never overwrite the journal with an old backup.

Remaining risks: original exact lock owner/statement is unknown; severe filesystem
stalls may exceed bounds and correctly stop monitoring; the larger atomic transaction
holds a RESERVED write lock for its processing/commit duration, so other writers still
serialize. Read-only recovery of a hot rollback journal can require an explicitly
reviewed writable SQLite recovery; preserve sidecars rather than deleting them or
using immutable=1. Existing server credential permissions are unchanged. New code,
guard integration and read-only dashboard behavior are not yet deployed/live-validated.
WAL compatibility is tested only on copies and is not assumed to solve all contention.
Source freshness, KPM/PSI/DU and monitoring-gap limitations from L3-4 remain. Stop before
live activation; any new trial needs separate approval.
