# L3-3 Kubernetes collection correction — offline preparation complete

The L3-2 hard 512 MiB address-space limit came from the temporary activation wrapper,
not the Kubernetes adapter or dashboard. It set both soft and hard RLIMIT_AS on the
parent; exec children inherited them. Saved diagnostics contain `fatal error: failed
to reserve page summary memory` in Go `runtime.(*pageAlloc).sysInit` before any API
access. Virtual reservation is not resident consumption. Offline `kubectl version
--client -o json` succeeds without that cap. The parent also imposed CPU 30 seconds;
an outer timeout imposed 90 seconds. Previously `command_json` had a 10-second process
timeout and CLI requests a 5-second deadline, but capture_output buffered without
an output limit. The corrected collector avoids that shared helper; other callers
remain unchanged.

Files changed in this phase:

- `scripts/assurance/kubernetes.py`: dedicated allowlisted reader and bounded JSON
  process boundary; finite inherited AS limits fail explicitly without being lifted.
- `scripts/assurance/bounded_once.py`: exclusive single-use supervisor with performance
  evidence, bounded process-tree RSS, CPU/time/output and no retry/scheduling.
- `scripts/assurance/cli.py`: use the new reader, retaining OBSERVE_ONLY journal/engine.
- `scripts/assurance/observe.py`: third list for Warning events, reader counters,
  explicit unavailable KPM source. No warning-event remediation rules were invented.
- `scripts/assurance/adapters.py`: container restart detail, node labels/conditions,
  timestamped warning events; truncated pod inventories cannot imply healthy service.
- `tests/test_assurance_kubernetes.py`: offline boundary and telemetry regressions.
- This report, activation plan, offline performance and test evidence.

No live collection, deployment, service restart, Kubernetes resource change, commit
or push occurred in L3-3. Unrelated existing workspace edits were preserved.

Resource correction: keep virtual-address space unrestricted for Go reservations;
use 256 MiB sampled child-tree RSS (512 MiB complete collection tree), 20 ms sampling,
10-second child timeout, 5-second request deadline, 2 MiB combined output, at most
32 processes per sampled tree, three list attempts, and process-group cleanup.
RSS watchdogs allow transient overshoot; they are not hard kernel quotas. The whole
collection supervisor has a 90-second wall deadline and inherited 30-second CPU cap.
The single-use directory prevents automatic repeated cycles. No credential, kubeconfig,
RBAC or TLS configuration was changed. Allowlisted requests are pods and Warning events
in the bound namespace plus nodes; no exec/log/secrets/mutation command is admitted.

Telemetry results are OFFLINE fixture results only: one Ready node with all pressure
conditions preserved, one ready CU/DU/fog each, 13 ready core pods, restart counts,
empty warnings and fresh/stale warning-event tests. The adapter emits LIVE only for
successful current reads; retained warning timestamps age individually to STALE;
API/permission/time/output failures emit UNAVAILABLE. No missing telemetry generates
a node/workload outage. Workload readiness does not prove protocol or end-to-end
service health. KPM remains UNAVAILABLE. Real corrected Kubernetes telemetry awaits
separate approved activation.

Current read-only service/dashboard checks: watcher inactive; dashboard service active
with unchanged PID 578791; LIVE_AUTO false, production executor DISABLED; cached
monitoring confidence STALE. The four existing target incidents remain WARNING,
with controller-manager 10257, scheduler 10259, proxy 10249 and etcd 2381 node-IP
scrape refusals from loopback-only listeners. A temporary SQLite backup was replayed
twice: incident count 4 before/after, all incident identities unchanged. The original
journal was never updated by these tests. Policy classification stays 8/27/9 (44).
Safety suites verify production denial, protected NS guards, emergency stop, simulation
isolation and no lifecycle endpoint. The collector calls only OSM read methods; no
lifecycle/remediation implementation was added.

See `offline-performance.json` for measured fixture timings/RSS and `test-results.json`
for final regression outcomes. L3-2 baseline was 12.16 s, approximately 0.81 CPU s,
32.1 MiB parent peak RSS with failed kubectl startup. Fixture measurements are not a
claim of improved live performance: API network/journal costs and real kubectl RSS
need the approved one-shot measurement. Live Kubernetes requests this phase: zero.
The proposed collector increases Kubernetes list attempts from two to three for
Warning events; Prometheus/Alertmanager budgets remain unchanged. Discovery traffic
is not measured by the list-command counters.

Exact proposed activation is in `activation.md`. It needs only a fresh collector
process, not a dashboard/service restart. Stop before executing that procedure.

Validation: **160 tests passed**, including 16 new Kubernetes/supervisor regressions
and all 16 existing offline simulations. Python compilation and diff whitespace
checks passed. The final fixture run used 0.099 s wall time, 0.098 CPU seconds
(parent plus children), 26.47 MiB parent peak RSS and 0.095 s subprocess time for
three fake-client list attempts; it made zero live API requests. Real offline
kubectl client-only startup under the corrected runner succeeded in 0.052 s,
using 0.064 child CPU seconds and 39.50 MiB peak child RSS. See `kubectl-startup.json`;
that is startup evidence, not a live collection benchmark.
