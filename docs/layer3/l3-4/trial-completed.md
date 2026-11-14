# L3-4 approved trial — stopped during first attempt

The one approved trial started at 2026-10-14T16:08:37.182372+05:30 and stopped after **9.197094 seconds** (guard elapsed 9.788463 seconds), well below the 600-second limit. No restart/retry, repair, further collection, service restart, protected mutation, commit or push occurred.

## Counts and confirmed failure

- Attempted: **1**; fully successful: **0**; interrupted/failed: **1**.
- Remaining authorized slots skipped after abort: **9**. These were never launched;
  they are not nine attempted failures or nine catch-up cycles.
- Persisted telemetry snapshots: **1**; fully completed alarm-processing cycles: **0**.
- Supervisor result: shutdown; cycle status BOUNDED_PROCESS_FAILED.

The independent safety guard I added attempted a read-only SQLite journal query every five seconds with a three-second busy timeout. Its second check raised `OperationalError: database is locked` while the collector was writing the journal. The guard conservatively requested SIGTERM rather than continuing without safety visibility. The periodic supervisor then cancelled/killed/reaped its in-flight CLI. That cancellation produced the generic BOUNDED_PROCESS_FAILED label; it was **not** evidence of a CPU/RSS/output/time watchdog threshold being exceeded or a Kubernetes/OSM service failure.

The telemetry transaction and read_only_collection audit entry had already committed before cancellation. Twelve existing healthy/resolved observation rows updated before the remaining alarm-ingestion work was interrupted. No known active scrape warning received a new last_seen timestamp in this attempt. This is a partial journal update, not a completed cycle. The audit remains valid. The supervisor recorded cycle and shutdown evidence. The guard's journal-contention handling is a trial-readiness gap to review offline in a separately requested task; it was not repaired or bypassed during this execution.

## Telemetry and OSM adapter compatibility

Fresh persisted Kubernetes/node/Warning-event, Prometheus and Alertmanager data were LIVE. Node Ready=True, with MemoryPressure/DiskPressure/PIDPressure=False. CU, DU and fog were WORKLOAD_READY, and all 13 Open5GS pods were WORKLOAD_READY. The inventory was untruncated. KPM remained UNAVAILABLE; protocol setup, UE registration, PDU sessions, measured user plane and latency were not inferred from pod readiness.

The new OSM adapter authenticated and completed three bound object GETs (two protected NSs and the prior completed RAN operation), with zero failures, against installed NBI image `opensourcemano/nbi:releasenineteen-daily`. Both NS snapshots were LIVE/READY/running/IDLE. This establishes compatibility for the reviewed authentication/read paths, not all OSM APIs or sustained-cycle reliability. The image tag is deployment evidence, not an immutable server build identifier.

The cached dashboard reported LIVE at the post-check, then STALE at age **196.60 seconds** after collection. No extra collection refreshed it. Existing four warning rows correctly remain STALE because their last_seen values were not updated during the interrupted alarm-processing sequence.

## Incidents and correlation

The four original WARNING/ESCALATED incidents and IDs are unchanged, with zero new incidents and zero new remediation actions:

- 5950eace-236c-4035-ad17-9901742e29fb
- 31e67ae4-b9fb-4862-a71d-86095bb8267b
- dbf15a8d-7814-4d9b-ae42-f8ba514cab86
- e4c3f058-d636-4f5e-95d0-43595f828085

The persisted telemetry contains **one** MONITORING_ENDPOINT_UNAVAILABLE correlation group with controller-manager 10257, etcd 2381, proxy 10249 and scheduler 10259 on 172.30.18.32, all connection_refused. Control-plane process health and Kubernetes service degradation are explicitly UNAVAILABLE; the group does not assert an outage. This group is exposed through the existing assurance API. The unchanged dashboard UI still lists individual alarms/incidents and has no dedicated grouped-issue widget.

Single-attempt deduplication/identity preservation passed, but **successive-live-cycle deduplication was not tested**, because no second cycle ran. Five-cycle offline replay evidence from preparation remains separate evidence. Policy classification remains 8 SUPPORTED / 27 PARTIALLY_SUPPORTED / 9 UNAVAILABLE.

## Measured resources and pressure

| Measurement | Result |
|---|---|
| Trial scheduling duration | 9.197094 s |
| Interrupted collection process wall | 7.629593 s |
| Collector and waited-child CPU | 1.350091 CPU-seconds; supervisor CPU excluded |
| Sampled collector-tree peak RSS | 76668928 bytes (73.12 MiB) |
| Child rusage maximum RSS | 50560 KiB |
| Guardian CPU | 0.067688 CPU-seconds |
| Kubernetes subprocess time | 0.394948 s |
| Kubernetes list attempts | 3 successful, 0 failures |
| Monitoring HTTP requests | 6, 0 failures |
| OSM requests | 3 GETs + 1 authentication, 0 failures |
| Host CPU utilization | Mean/peak 34.52% / 34.52%; only 1 valid approximately five-second sample |
| Local CPU PSI some avg10 | Mean 6.48%, peak 6.60%; 2 samples (6.36%→6.60%) |
| Prometheus CPU PSI some/5m | 8.117%; one collected sample |

Host utilization includes OAI and every other workload, not just the collector. PSI is waiting pressure, not CPU utilization. The interval is too short to attribute a PSI increase or interference to this collector. There is no sustained average/peak estimate from ten cycles. DU readiness, restart count and F1-C remained unchanged; DU throughput, slot timing, jitter and RF/session interference are **UNAVAILABLE** without suitable measurements. No UE was deployed.

The offline model assumed successful cycles: 12.4396 s, 1.9646 CPU s, 73.48 MiB collector tree, nominal 0.82% four-core CPU share at a 60-second cadence. The interrupted attempt used 1.350 CPU s and 73.12 MiB tree peak. Peak memory is consistent with that single-cycle baseline, but partial CPU/wall values cannot establish lower completed-cycle costs or sustained overhead. Three list attempts are not exact Kubernetes wire counts because discovery GETs are not instrumented. Pre/post verification requests are separate; runtime guard Kubernetes/SCTP/OSM requests were zero, as it stopped before its first 30-second external health check.

## Final protected-service and process verification

Both protected NSs remain READY/running/IDLE, with no pending or unexpected lifecycle operation. All 13 Open5GS pods plus CU/DU/fog remain Running/Ready. All 16 protected pod UIDs/specs/container IDs/images/restart counts match preflight; 29 workload-namespace Service UIDs/specs match. Deployment-state and reviewed-source hashes match. OSM operation history is unchanged. CU/DU F1-C and CU NGAP SCTP remain established; E2 remains absent.

Seven dashboard/selector APIs returned HTTP 200. Dashboard PID 578791 and NRestarts=0 remain unchanged. Legacy watcher is inactive, governance OBSERVE_ONLY, LIVE_AUTO=false and production_executor=DISABLED. Audit hash verification passed. The journal collection-record count is now 3 (two prior records plus this partial snapshot), not three successful cycles in this trial.

The timeout/periodic/CLI process identities recorded by the guard are gone, both execution locks are free, and a final process scan found no running assurance collector or kubectl process. No background/automatic restart mechanism was installed. A transient kubectl during an earlier broad scan had exited by final verification; no unrelated process was stopped.

Evidence: `.runtime/l3-4-trial/result.json`, `.runtime/l3-4-preflight/trial-output.log`, `runtime-guard.json`, `persisted-telemetry.json`, `trial-audit.json`, `post-validation.json`, `process-cleanup.json`, and `freshness-final.json`. The original backup remains `.runtime/l3-4-backup/assurance.sqlite`; no rollback erased audit history. A sanitized detailed result is `trial-results.json` beside this report.

Remaining gaps: guard/journal contention, incomplete per-alarm processing on cancellation, unverified successive live cycles, four scrape endpoints, historical HPA resource-metric warnings, unavailable KPM/protocol/session/latency telemetry, no dedicated correlation UI, and unmeasured sustained PSI/DU impact. Stop condition reached. Any repair or further live trial requires a separate task/approval.
