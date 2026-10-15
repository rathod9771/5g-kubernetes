# Security, resource cost and rollout prerequisites

No new credentials were created or cluster RBAC applied. Read-only inspection used
the existing authenticated/TLS-verified OSM client and current Kubernetes access.
Those existing operator credentials are not the recommended assurance runtime role.
The example RBAC separates namespace workload reads from cluster node reads; it has
no secret access, write verbs, exec/log access or cluster-admin. Bind additional
read-only roles explicitly for OSM/monitoring visibility if required. SCTP evidence
currently needs operator collection or a separately reviewed exporter because granting
arbitrary pods/exec would defeat this minimal privilege boundary.

Operator and webhook bearer tokens are distinct, at least 32 characters, and deny
writes when unset. Deploy behind verified HTTPS/authenticated ingress, with per-user
operator authorization, secret injection, network policy, rate limits and bounded
request concurrency. The existing dashboard deployment APIs retain their behavior;
these new token gates do not retrofit authentication to the legacy backend.
Shared-token operator auditing identifies a role, not a named individual; SSO/RBAC
for operators is a live prerequisite. Trusted webhook senders must not supply raw logs,
subscriber identifiers or credentials as operational identities.

Alarm observations retain only an explicit evidence-key allowlist. Free-form
annotations/logs and unknown observation fields are dropped. Auth headers are never
journaled. OSM exceptions suppress private transport/authentication details.
The Alertmanager status adapter strips `config.original` and keeps only version
metadata. The SQLite database is created with mode 0600 in the private runtime area;
audit hashes and triggers detect normal edits but do not defend against privileged
host tampering. Backup, disk capacity, journal retention and integrity checks must be
reviewed before operational ingestion.

Importing backend registration adds no live requests or background loops. The UI
refreshes the local journal at 30 seconds, with 500-row display caps and bounded audit
history. Read-only Kubernetes/Prometheus/OSM collection is an explicit one-shot CLI
operation, not a scheduled service. Prometheus requests have four-second timeouts and
2 MiB responses; series cap is 256. Kubernetes requests have explicit API timeout;
existing OSM client requests use its 30-second timeout. A future event collector must
reuse existing caches and set a low-frequency bounded cadence instead of adding
per-browser kubectl polling.

SQLite full-sync transactions prioritize durability; wall time includes disk waits.
There is no measured added steady-state cluster CPU cost because nothing was deployed.
The offline simulation resource measurement is recorded in `validation.md`; it is
not a prediction of live ingestion load. Correlation scans retained histories and
requires indexing/retention work for production event volume. The current UI displays
at most 500 records; counts are of the displayed journal slice, not a fleet-wide count.

Live rollout gates, each needing a separate operator approval:

1. Review source, offline validation, persistent journal path, standards boundaries
   and capability gaps. Keep legacy watcher stopped.
2. Deploy only the dashboard/read-only assurance modules with OBSERVE_ONLY and
   production executor disabled. Approve runtime authentication and least-privilege
   credentials. Validate cached evidence/provenance without changes to workloads.
3. Approve an explicit one-shot collection, then a bounded event collector only
   after label identity, freshness, persistence and load budgets are validated.
4. Review baseline and existing semantic alert overlaps. Validate PromQL with the
   installed promtool, installed CRD/Prometheus versions and planned receiver scope.
   Obtain separate approval for selected production alerts and Alertmanager fragment.
5. If requested later, test a specific recovery on a nonprotected disposable profile:
   lifecycle serialization, idempotency, pending-operation reconciliation, timeout,
   ownership, capacity, core/protocol verification, cooldown and rollback. Separately
   approve adding a production executor operation. No executor operation is currently
   registered, and selecting APPROVAL_REQUIRED does not register one.
6. Treat each RAN transition as a separate interruption/continuity test and approval.
   Emergency-service changes require actual IMS/SMF/PCF and authorized QoS evidence,
   independent of infrastructure alarm priority.

No deployment, alert enabling, watcher start, live remediation, commit or push was
performed. This task stops at source/artifacts and offline tests; the gates above
are future work requiring separate authorization.
