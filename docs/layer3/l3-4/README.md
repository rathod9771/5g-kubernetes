# L3-4 sustained observe-only assurance — offline preparation

Implementation is ready for a separately approved finite trial, not indefinite
production monitoring. No live collection, scheduling, deployment or service restart
was performed in this phase. The existing dashboard and legacy watcher configuration
were not changed. No protected workload, NS, OSM catalog, monitoring/control-plane
configuration, commit or push was changed.

## Implementation and review findings

- `scripts/assurance/periodic.py`: opt-in finite scheduler, monotonic start-to-start
  intervals, <=600 seconds / <=10 attempts, no catch-up bursts, source-error backoff,
  shutdown/STOP/emergency-stop checks, durable cycle/start/stop audits and result file.
  Each cycle records source timestamps/provenance, request counts, CPU/RSS/time,
  CPU PSI, service states and protected restart counts. The existing durable journal
  preserves alarm and incident identities across fresh child processes.
- `scripts/assurance/locking.py`: nonblocking OS locks for trial ownership and individual
  collection ownership. Lock files are never unlinked/replaced; CLI one-shot and
  periodic child processes share the same collection lock. Conflicts fail before reads.
- `scripts/assurance/cli.py`: shared collection lock, explicit Kubernetes/OSM config
  validation, dedicated read-only OSM reader. No new lifecycle/executor entry point.
- `scripts/assurance/osm.py`: OSM transport previously used 30-second timeouts,
  unbounded response reads and authorization refresh retries. Assurance now permits
  only two state-bound NS reads plus the state-bound operation, with one token
  authentication, four-second socket timeout, verified TLS/CA, no redirects/retries,
  2 MiB response cap and explicit counters. The lifecycle client's implementation and
  other users are unchanged. New OSM transport compatibility still requires live trial.
- `scripts/assurance/kubernetes.py`: cancellation support in the existing time/RSS/output
  watchdog; shutdown kills/reaps nested collector/kubectl process groups.
- `scripts/assurance/observe.py`: logical grouping of fresh down scrape endpoints on
  one host, with process health/service degradation explicitly UNAVAILABLE. Prometheus
  recovery now emits resolved evidence so existing clearing hysteresis can clear a
  pipeline-availability alarm. Missing data never clears workload alarms.
- `scripts/assurance/store.py`: normalize audit timestamps before hashing, avoiding
  integer-versus-SQLite-REAL hash mismatches. Existing production audit history is not
  rewritten. Copied-journal verification confirms compatibility.
- `tests/test_assurance_periodic.py`: scheduler, lock, shutdown, API-failure, clearing,
  correlation, audit and safety regressions. Existing CLI fixture in
  `tests/test_assurance_kubernetes.py` updated for the new reader/lock contract.

The engine/catalog hysteresis and incident-key rules remain unchanged. Policies still
have max_age=120 seconds, max_gap=90 seconds and clear_persistence=60 seconds.
LIVE_AUTO and production execution remain disabled. All 44 policy capability
classifications remain 8 SUPPORTED / 27 PARTIALLY_SUPPORTED / 9 UNAVAILABLE.

## Recommended interval and expected overhead

Recommend **60 seconds**, provisionally, for the proposed trial. It fits the 90-second
maximum sample gap and allows two recovery samples across the 60-second clearing
window. At 90 seconds, small positive jitter resets persistence. At 120 seconds,
normal gaps exceed the policy gap window and produce stale publication intervals.
No policy freshness windows were weakened to justify a slower cadence.

Model from L3-3's measured 12.4396 seconds, 1.9646 CPU-seconds and 73.48 MiB collector
tree peak, not sustained measurements:

| Interval | Average collector core equivalents | Nominal four-core CPU share | Monitoring GETs/hour | Kubernetes lists/hour | OSM GETs + auth/hour | Normal publication age estimate |
|---|---:|---:|---:|---:|---:|---:|
| 60 s | 0.03274 | 0.82% | 360 | 180 | 180 + 60 | 72.44 s |
| 90 s | 0.02183 | 0.55% | 240 | 120 | 120 + 40 | 102.44 s |
| 120 s | 0.01637 | 0.41% | 180 | 90 | 90 + 30 | 132.44 s |

Peak RSS is per concurrent collector tree, approximately 73.48 MiB in L3-3, and is
not multiplied by cycle count. The persistent supervisor adds memory: an offline
single fixture cycle measured 19.25 MiB supervisor peak, 0.008814 CPU-seconds and
2.1813 seconds including local audit finalization. Its timings are not live overhead.
The OSM transport and periodic audit changes mean the L3-3 costs are only planning
estimates. Collector-time duty at 60 seconds is 20.73%; this is not CPU utilization.
Nominal physical-core ratios do not predict hyperthreading/scheduling contention.

Normal publication estimates exclude jitter, variable filesystem/network latency and
up to 30 seconds of existing dashboard display refresh. A cycle may approach the
90-second watchdog bound; scheduling then delays the next start, and persistence may
reset safely. A source error backs off to 120 then 240 seconds, deliberately permitting
STALE/UNAVAILABLE data rather than overloading a failing API. Known down target scrapes
alone do not invoke backoff. RSS checks are sampled thresholds with possible transient
overshoot, not hard kernel memory quotas. Four-second HTTP timeouts are socket inactivity
timeouts; the outer cycle deadline bounds DNS/streaming stalls.

Three Kubernetes list attempts are not exact wire-level counts because discovery
GETs may occur. Expected logical requests per normal cycle: six monitoring GETs,
three Kubernetes lists, three OSM object GETs and one token POST (authentication only).
No request is generated per browser; the dashboard continues reading its local cache.

CPU PSI sustained impact is **UNAVAILABLE**. L3-3's 11.166% CPU PSI some/5m sample is
historical context, not an attributable collector effect or current baseline. OAI DU
throughput, slot timing, jitter and RF/session impact are also **UNAVAILABLE** without
appropriate evidence. Ready pods and SCTP do not prove absence of performance impact.

## Correlation, deduplication and clearing

The four controller-manager 10257, scheduler 10259, kube-proxy 10249 and etcd 2381
scrape refusals on 172.30.18.32 form one logical monitoring endpoint group. Earlier
L3-2 evidence identified loopback-only metric listeners versus node-IP targets.
Current offline grouping says only MONITORING_ENDPOINT_UNAVAILABLE. It does not
confirm unhealthy control-plane processes or a degraded Kubernetes service.

The logical group does not merge, renumber or delete the four durable incidents.
Five repeated 60-second cycles on a temporary production-journal backup, including
redelivery of each event, retained exactly four incidents and all original IDs.
Audit verification passed; the production journal hash was unchanged. See
`correlation-validation.json`. Endpoint grouping is available in cached telemetry;
the unchanged dashboard has no dedicated grouped-incident widget.

Clearing tests require fresh resolved evidence spanning 60 seconds, no sample gap
above 90 seconds, and no stale observation. Missing API data, stale data and a single
recovery cannot clear a persistent alarm. Complete recovery resolves the existing
incident without remediation; genuine later recurrence may create a new incident
under the engine's existing rules. Prometheus outages are availability assertions,
not Kubernetes/control-plane outage assertions.

## Offline tests and safety

**186 regression tests passed**, including 26 new periodic/OSM tests. Python compilation
and diff whitespace checks passed. See `test-results.json` for counts/commands. Coverage includes repeated
cycles, duplicate delivery, persistent identities after worker restart, clearing and
gap reset, missing/stale sources, Prometheus/Kubernetes/OSM outages, backoff recovery,
shutdown/STOP/emergency-stop, cross-process execution lock, CPU/RSS/output/time limits,
bounded OSM auth/read budgets and forbidden lifecycle calls. Simulated incidents run
in temporary journals and cannot invoke the production executor. All 16 foundation
simulations remain offline.

Current read-only runtime checks found layer3-watcher inactive, dashboard active with
PID 578791 and NRestarts=0, governance OBSERVE_ONLY, LIVE_AUTO=false and executor
DISABLED. The cached dashboard is STALE and retains four incidents. These are safety
checks, not a new telemetry collection or a fresh protected-service health assessment.
The last live protected-service health evidence remains L3-3. No trial was started.

## Proposed activation and remaining limits

The exact separately approved trial procedure is in `activation.md`: foreground
60-second collection, maximum ten cycles / 600 seconds, outer 615-second timeout,
unchanged resource bounds, explicit start/stop, pre/post protected-service comparison,
freshness/incident/request/performance acceptance and local-only rollback.

Indefinite deployment, journal retention/rotation, supervision/restart policy and
operator-facing trial/group widgets need later review. The four scrape gaps, historical
HPA metric warnings, KPM/E2 and protocol/session/latency telemetry remain unresolved.
Server RBAC/credentials are unchanged; command/path allowlists limit this collector
but do not narrow existing server-side credential permissions. STOP-file/emergency-stop
checks are between cycles or every second in idle waiting; SIGINT/SIGTERM cancels an
in-flight process immediately through the watchdog. In-flight cancellation or forced termination can leave an
already committed collection record or incomplete per-alarm ingestion; evidence is
preserved and no retry/reconciliation is automatic. The last valid snapshot simply
ages to STALE after shutdown. No service restart is needed for the proposed trial.
