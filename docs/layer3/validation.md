# Validation and final deliverables

Curated reports are versioned; recorded test output and machine-specific measurement artifacts remain local. All tests
are offline; live checks were separately bounded and read-only. No automated test
terminates a real NS or Kubernetes resource.

## Deliverable inventory

- `scripts/assurance/`: normalized model, reviewed catalog, durable journal/store,
  alarm/incident engine, policy/safety/verification, lifecycle governance, read-only
  adapters, authenticated Flask endpoints, simulation and CLI.
- `config/assurance-policies.json`, `config/assurance-event.schema.json`: configurable
  alarm policies and a local event-schema reference.
- `ran-selector/backend.py`: additive lazy registration and renderer static route;
  preserves existing APIs, deployment locks, journals and user modifications.
- `ran-selector/index.html`: replace legacy Layer 3 action-log view with assurance
  panel; preserve workspace/window controls and other existing dashboard work.
- `ran-selector/assurance.js`: safe DOM rendering, incidents, provenance, proposals,
  controls, acknowledgement, approval/cancellation, maintenance and sandbox results.
- `tests/test_assurance.py`: normalization, deduplication, correlation/inhibition,
  hysteresis, flapping, missing/stale data, policy/severity/protection, shared lifecycle
  lock, concurrent deliveries, durable idempotency/cooldown, timeout ambiguity,
  approval/cancellation, incident transition validation, audit, API failures,
  emergency restrictions, all 16 simulations and production-call traps.
- `tests/test_dashboard_workspace.py`: update the legacy Layer 3 endpoint fixture
  and load the actual assurance renderer in the DOM identity test harness.
- `monitoring/assurance/`: 12 unapplied alert rules (standalone and PrometheusRule),
  Alertmanager merge fragment, least-privilege RBAC examples and review notes.
- `docs/layer3/`: architecture, standards mapping, capability and alarm catalog,
  operational policy matrix, security/rollout, simulations, validation and dated
  machine-readable live evidence/transition matrix/results.

## Evidence and limits

The live audit verifies Kubernetes and protected NS health at a specific instant,
Prometheus query/target APIs, monitoring Alertmanager readiness, and CU/DU F1 plus
CU-to-AMF NGAP SCTP associations. It also records failed KPM acceptance jobs, four
control-plane scrape failures, absent measurements and unverified installed features.
No pod readiness, OSM READY/COMPLETED or transport association is treated as proof of
protocol setup, UE registration, PDU sessions or measured user-plane health.

Only observation, local incident escalation, operator decision recording and
sandbox execution are supported actions. Class B allowlist and production executor
are absent. Targeted DU/CU/fog operations remain proposals. All profile transitions
require review; every generated transition has automatic eligibility false.

All synthetic traces should report SIMULATED, RESOLVED, passed synthetic verification,
valid audit and zero production calls. These outcomes do not prove a recovery works
on the installed stack. Tests independently trap network/process/production callbacks.

YAML examples parse offline and all rule families occur in the saved inventory.
Host `promtool` is unavailable; PromQL semantic/rule testing is an explicit rollout
gate, not reported as passed. No Prometheus or Alertmanager configuration was applied.
New dashboard code was tested offline; the running backend was not restarted.

No added steady-state cluster CPU was measured or incurred by a deployed service.
The sandbox benchmark below measures host CPU/RSS and full-sync journal disk waits.
The system has one node: preserve small query/concurrency budgets, avoid simultaneous
large workloads, and review ingest retention/indexing before production scale.

## Resource measurement

The 16-scenario sandbox run used **0.22 seconds user CPU**, **0.12 seconds system CPU**,
**22,400 KiB peak RSS** (about 21.9 MiB), and **100.67 seconds elapsed** on this host.
Most elapsed time was durable SQLite/disk synchronization; this is a bounded offline
run, not a background workload or a live assurance throughput benchmark.

## Final offline regression results

**125 tests passed**: 32 assurance, 21 dashboard, 28 lifecycle safety, 18 experimental recovery, 11 RAN protocol health, 15 monitoring cache and bounds. All 16 sandbox scenarios passed with zero production calls.
JavaScript syntax, Python compilation, YAML parsing and `git diff --check` passed.
Exact suite commands/counts/timings are in [test-results.json](test-results.json).
Production operations, alerts and dashboard rollout remain disabled/unapplied.
