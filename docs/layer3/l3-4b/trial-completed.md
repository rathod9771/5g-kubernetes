# L3-4B corrected periodic trial — completed

Exactly one approved observe-only trial completed **10/10 attempts**: ten completed,
zero failed, interrupted or skipped. Scheduling duration **543.875761 s**,
stop reason max_cycles. Starts were approximately 60 seconds apart; no overlap,
automatic retry or restart occurred. The tenth attempt finished before the 600-second
limit. The user manually reloaded ran-selector.service; the agent restarted no service.
New dashboard PID 1435287, started 17:14:51 IST after the SQLite corrections, remained
active with NRestarts=0. Activation verified seven HTTP 200 APIs, unchanged 100-record
audit prefix/four incidents, DELETE mode and disabled automation before the trial.

## SQLite behavior, telemetry and incidents

Ten atomic collection updates completed. No persistent journal contention, governance
failure, source failure, guard failure or resource watchdog violation occurred.
SQLite BEGIN IMMEDIATE serialization and the single execution lock prevent concurrent
writers/collectors; the observed maximum simultaneous collector count was one.
The guardian's independent critical checks preceded journal access and passed on
9 checks. It did not infer health from stale/busy data.

Supplementary bounded read-only observer probes: 8 AVAILABLE, each one
attempt; mean wait **1.829 ms**,
maximum **2.528 ms**. These are measured
samples, not every internal guardian read. Guardian per-read wait/PAUSE history and
writer lock timing were not retained, so zero transient busy calls across every
connection is not asserted. No persistent contention or contention-triggered stop
was observed. The journal remains DELETE; no WAL migration/schema reset occurred.

The new OSM adapter succeeded for every cycle: three bound GETs and one token
authentication each, zero failures, against installed NBI image
opensourcemano/nbi:releasenineteen-daily. Kubernetes pod/node/Warning-event reads,
Prometheus and Alertmanager/group data were LIVE when fresh. CU/DU/fog were
WORKLOAD_READY and all 13 Open5GS pods WORKLOAD_READY. Node Ready=True; memory/disk/PID
pressure conditions=False. KPM stayed UNAVAILABLE; protocol setup, UE/PDU/user-plane
and latency measurements were not invented from pod readiness.

All four original WARNING/ESCALATED incident IDs and alarm IDs persisted without
new incidents or remediation actions. The endpoint correlation group consistently
contained controller-manager 10257, scheduler 10259, proxy 10249 and etcd 2381 on
172.30.18.32 with connection_refused. Classification MONITORING_ENDPOINT_UNAVAILABLE;
control-plane process health and Kubernetes service degradation UNAVAILABLE.
The API exposes one correlation group; the unchanged UI displays four individual
alarms/incidents rather than a dedicated grouped widget. Monitoring pipeline remains
DEGRADED due to these known monitoring gaps, not a confirmed control-plane outage.

The original 100 audit rows remained byte/value-identical; the extended audit now
contains **412 records**, hash chain valid and integrity_check=ok.
Existing historical interrupted L3-4 evidence remains intact. Journal collection
records now total 13: three previous records (including the historical partial one)
plus ten successful L3-4B collections. Healthy/resolved observation updates do not
represent new service-outage incidents. Policy counts remain 8/27/9 for all 44 policies.

## Resources and request accounting

| Metric | Measured result |
|---|---|
| Cycle wall mean/min/max | 2.644698 / 2.126560 / 3.435596 s |
| Collector CPU total/mean | 16.304381 / 1.630438 CPU s |
| Collector-tree peak RSS | 77963264 bytes (74.35 MiB) |
| Independent guardian own CPU | 7.803486 CPU s |
| Host aggregate utilization mean/peak | 35.06% / 74.29% (106 samples) |
| Local CPU PSI some avg10 mean/peak | 8.21% / 21.15% |
| Local CPU PSI first/last | 6.28% / 6.23% |
| Prometheus CPU PSI some/5m mean/peak | 8.808% / 10.288% (10 samples) |
| Collector monitoring HTTP GETs | 60, no failures |
| Collector Kubernetes list attempts | 30, no failures |
| Collector OSM GETs/authentication | 30 / 10, no failures |

Host utilization and PSI include all workloads and cannot establish collector causality.
The known collector CPU corresponds to about 0.02998
CPU-core equivalents averaged over the trial (0.75%
nominal four-core share). Guardian own CPU is additional; its Kubernetes subprocess
CPU and periodic-supervisor CPU were not separately retained, so a complete monitoring
CPU total is unavailable. Guardian rusage maximum reports **598236
KiB (584.21 MiB)**, outside the watched collector tree. No
live guardian VmHWM sample was captured to validate this high figure against the
19.25 MiB offline fixture estimate; total co-resident monitoring memory therefore
remains uncertain and needs investigation before indefinite deployment. The collector
512 MiB tree / 256 MiB Kubernetes limits remained in force and were never exceeded.

The offline model used L3-3's 12.4396 s, 1.9646 CPU s, 73.48 MiB collector peak per
cycle. Actual mean wall/CPU were 2.645 s / 1.630
CPU s; collector memory peak 74.35 MiB is close to the prior measurement.
The corrected atomic journal writes substantially reduced the observed wall time;
exact attribution of the improvement to I/O versus other scheduling/network variation
was not instrumented. The 60-second nominal CPU model predicted 0.82% four-core share
for collector-only CPU; actual collector-only mean-cycle equivalent was
0.68%. Guardian measurements are not represented in that
collector-only model.

Guardian validation requests are separate: 9
pod/Service list commands, 18 read-only SCTP
exec commands, 27 OSM GETs and one authentication.
Pre/post activation/validation requests are additional. Kubectl discovery/exec
wire-level requests are not independently counted; list-command counts are not
asserted as exact Kubernetes HTTP totals.

CPU PSI had a transient peak, but first/last local avg10 were similar. No DU
readiness/restart/SCTP change was observed. DU slot timing, scheduling jitter,
throughput, RF/session interference remain UNAVAILABLE: no UE or end-to-end
measurement was deployed, and absence of performance interference cannot be proven.

## Final state and stop verification

Both protected NSs remain READY/running/IDLE; OSM operation history unchanged,
no pending/unexpected lifecycle operation. All 16 protected pod UIDs/specs/container
IDs/images/restart counts match preflight. 29
workload-namespace Service UIDs/specs and active deployment-state hash match.
CU/DU F1-C and CU NGAP SCTP associations remain established; E2 remains absent.
Seven dashboard APIs remain HTTP 200. Legacy watcher inactive, LIVE_AUTO=false,
production executor DISABLED, governance OBSERVE_ONLY, dashboard PID unchanged.

All recorded timeout/periodic/CLI processes stopped, both execution locks are free,
and a final process scan found no active assurance collector. No service/timer/cron
or automatic restart was installed. The final cache-only dashboard read at age
216.48 s correctly marked STALE while KPM remained UNAVAILABLE.
No extra collection refreshed the snapshot.

Detailed owner-only evidence: `.runtime/l3-4a-trial/result.json`;
`.runtime/l3-4a-preflight/runtime-guard.json`, `observer-samples.jsonl`,
`activation-verification.json`, `restart-baseline.json`, `post-validation.json`,
`integrity-final.json`, `persisted-telemetry.json`, `freshness-final.json` and backups.
Sanitized aggregates/per-cycle summaries are in `trial-results.json` beside this report.
No commit/push, monitoring/workload/catalog change or protected service restart occurred.

Stop condition reached. Further collection, memory-accounting investigation, monitoring
repairs, KPM enablement or indefinite deployment require separate authorization.
