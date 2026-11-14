# L3-4C approved verification trial — stopped on first failure

One invocation used the reviewed 60-second interval, 150-second duration and
two-attempt maximum. **1 attempted, 0 completed successfully, 1 failed, 0
interrupted, 1 remaining slot not attempted**. No retry, restart or extra collection.
Runner reason `process_failure`, cycle `BOUNDED_PROCESS_FAILED`, exit status 1.
Periodic elapsed 3.941552 seconds; guardian elapsed 6.637615 seconds. Original
subprocess exception/private stderr was suppressed by the existing adapter and
not retained, so the exact root cause is **unverified**. Do not infer a memory
budget violation solely from this generic status. No source correction was made.

## Memory and CPU evidence

- Guardian sampled RSS 22.617188 MiB; current-image HWM 31.871094 MiB;
  sampled PSS 12.595703 MiB. Only one runtime sample was captured.
- Legacy self `ru_maxrss` remained 598236 KiB / 584.214844 MiB at startup/end,
  labelled separately and not used for current guardian watchdog decisions.
- Collector-tree peak RSS 71.992188 MiB, below the 512 MiB threshold.
  Nested Kubernetes-child sampled peak RSS 49.593750 MiB (256 MiB cap).
- Shared cgroup current 2270.468750 MiB, historical peak 2829.898438 MiB;
  includes unrelated launcher/editor processes, not exclusive application memory.
- Guardian's `/proc/status` RSS and `/proc/smaps_rollup` RSS agreed exactly in
  the captured sample (zero-byte difference). Independent direct observer could
  not start before the early termination: timeout PID was already absent. Its
  launch error was FileNotFoundError for `/proc/1616828/stat`; no retry occurred.
  Independent comparison and live collector-tree PSS remain UNAVAILABLE, so the
  full requested measurement-tolerance validation was not achieved.
- Collection CPU 1.426211 CPU-seconds, wall 3.451986 seconds; guardian own CPU
  0.496869 CPU-seconds. Sampled guardian/tree RSS safeguards remained configured;
  no guardian memory-limit or PID-reuse exception was reported. A successful
  sustained watchdog verification cannot be claimed for this stopped trial.
- One host CPU PSI `some.avg10` sample: 3.99%; avg60 6.40%, avg300 7.27%.
  Prometheus five-minute CPU-pressure value was 8.294208%. Too few samples for
  meaningful trial average/peak PSI or DU-interference analysis. The initial
  utilization calculation is 0% from a near-zero interval and is not a reliable
  trial CPU-utilization estimate.

## Partially committed telemetry and journal

A collection snapshot committed before the outer execution boundary reported
failure. Pods, Prometheus, Alertmanager and both OSM sources were LIVE when
collected; nodes and Kubernetes warning events UNAVAILABLE, as was KPM.
Workloads were WORKLOAD_READY. Three Kubernetes list attempts (two failures),
six monitoring HTTP requests (zero failures), three OSM reads plus one token
request (zero failures) were recorded. Independent guardian made one Kubernetes
list, two SCTP reads, three OSM reads and one authentication request. Pre/post
health-validation traffic is separate from collection/guardian counts.

Four existing scrape failures remain one MONITORING_ENDPOINT_UNAVAILABLE group;
control-plane process health and Kubernetes service-degradation claims remain
UNAVAILABLE. Original incident records/IDs and alarm identity set unchanged;
no duplicates. Policy classes remain 8 supported / 27 partial / 9 unavailable.
SQLite integrity `ok`; original 412-row audit prefix unchanged; 444 rows after
trial (32 appended). No database reset or migration. Post-check dashboard cache
STALE, seven APIs HTTP 200. No DB contention was recorded, but independent
observer lock-wait samples are unavailable; absence is not a zero-wait measurement.

## Final safety

Both protected NSs READY/running/IDLE; OSM operation history unchanged, no pending
lifecycle operation. CU/DU/fog and all 13 Open5GS pods Ready; F1-C and NGAP SCTP
associations established. All 16 protected pod identities/specs/container states/
restart counts and 29 namespace Services unchanged. Active state unchanged.
Legacy watcher inactive, LIVE_AUTO false, production executor DISABLED.
No trial processes or observed trial PIDs remain; collection, periodic and
lifecycle locks free. No protected configuration/workload mutation, service
restart, UE deployment, commit or push occurred.

Failure evidence is preserved in owner-only `.runtime/l3-4c-preflight`,
`.runtime/l3-4c-trial`, and `.runtime/l3-4c-postflight`. Sanitized structured
results: [trial-results.json](trial-results.json). Further investigation and any
new live trial are separate work; this invocation will not be restarted.
