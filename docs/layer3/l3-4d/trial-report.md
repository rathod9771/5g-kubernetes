# L3-4D bounded verification trial — completed

Exactly one guardian invocation ran with maximum two attempts, interval 60 seconds,
maximum collection window 150 seconds, one collector and stop-on-source-error.
**2 attempted, 2 successful, 0 failed, 0 interrupted, 0 skipped.** No retry/restart.
Periodic elapsed 62.732640 seconds; guardian elapsed 63.497265 seconds. Stop reason
max_cycles; guardian/timeout/periodic exit 0; guard_failure null. Peak sampled
simultaneous collectors 1; locks enforced serial execution.

| Attempt | Outcome | Wall seconds | CPU seconds | Collector-tree peak RSS |
|---|---|---:|---:|---:|
| 1 | LIVE | 2.836120 | 1.481943 | 72.074219 MiB |
| 2 | LIVE | 2.390119 | 1.613841 | 72.218750 MiB |

Both collectors returned valid JSON through a reader that requires child exit 0;
the successful child code is inferred from that contract, not separately stored.
Both per-cycle diagnostics null, all adapter failure counts zero. The prior
RSS-termination false failure did not recur across two collector processes and
six bounded kubectl lists. No watchdog/PID-reuse/measurement exception was reported.
The PF_EXITING edge branch itself has no live branch counter, so this run does not
prove it was exercised; deterministic offline regression covers the branch.
No live fault was injected to test nonzero propagation; that remains offline-tested.

## Memory, CPU and pressure

Guardian sampled peak RSS 31.898438 MiB, current-image HWM 32.539063 MiB,
peak sampled PSS 20.061523 MiB. Legacy lifetime self ru_maxrss remained
598236 KiB / 584.214844 MiB and was not used as current-image memory.
Guardian samples cover its own process; collector-tree peak 72.218750 MiB comes
from the bounded process supervisor. Shared cgroup memory remains separate:
first sample current 2119.007813 MiB, historical peak 2829.898438 MiB, including
unrelated launcher/editor processes. No cgroup was modified/reset.

One bounded mid-trial /proc check agreed exactly between guardian status RSS and
smaps_rollup RSS: 33447936 bytes; PSS 20878336 bytes. At that inter-cycle instant
the timeout/periodic tree RSS was 22257664 bytes and PSS 9580544 bytes. This was
not a collector-active peak or a same-instant comparison with its high-water mark.
The captured guardian HWM can exceed sampled RSS peaks between five-second samples.

Collector CPU total 3.095784 seconds, mean 1.547892 per cycle; mean collection
wall 2.613119 seconds. Guardian own CPU 1.099237 seconds excludes external guard
subprocess CPU. Host CPU utilization mean 33.692289%, peak 41.390123% (initial
near-zero sample excluded). Across 13 guardian samples, mean CPU PSI some.avg10
7.679231%, maximum sampled avg10 10.14%. These are shared-host ten-second averages,
not instantaneous pressure or causal attribution to monitoring. No protected-health
change was observed; no UE/user-plane/DU performance claim is supported.

All existing limits remained unchanged: guardian/collector RSS 512 MiB,
Kubernetes-child RSS 256 MiB, cycle wall <=90 seconds, CPU <=30 seconds, combined
output <=2 MiB, existing API deadlines and bounded cleanup. No resource limit or
safety guard was bypassed, no watchdog violation reported. RSS remains a sampled
watchdog, not a kernel memory quota.

## Telemetry and persistence

Both attempts collected LIVE Kubernetes pods/nodes/warning-event lists, OSM,
Prometheus and Alertmanager telemetry. Collector totals: six Kubernetes list
attempts, twelve monitoring HTTP requests, six OSM reads plus two authentications.
Guardian separately performed two Kubernetes lists, four SCTP reads, six OSM reads
and one authentication. Pre/post validation reads are separate from those totals.

SQLite integrity ok, full hash chain verified. All original 444 audit records
preserved byte-for-byte in the prefix; total 508 (64 appended). All four full
incident records and alarm identity set unchanged, no duplicates. Read-only
mid-trial journal visibility AVAILABLE on one attempt, 1.589607 ms wait; no
persistent contention recorded. This is not a measured worst-case lock latency.

Four known control-plane scrape failures retain the same MONITORING_ENDPOINT_UNAVAILABLE
correlation group and warning incidents. Connection refused at controller-manager
10257, etcd 2381, proxy 10249 and scheduler 10259 on 172.30.18.32 does not establish
control-plane outage or Kubernetes service degradation. KPM remains UNAVAILABLE;
UE/session/user-plane claims remain unavailable. Policy classes unchanged:
8 supported / 27 partial / 9 unavailable. Seven dashboard APIs returned HTTP 200;
cached monitoring confidence LIVE when verified, naturally STALE after 120 seconds.

## Protected-service and final safety verification

Fresh preflight and two independent in-trial checks passed. Final read-only
postflight confirmed both protected NSs READY/running/IDLE, no pending operations,
unchanged OSM operation history and active state. CU/DU/fog and all 13 Open5GS pods
Ready; F1-C and NGAP SCTP established. All 16 protected pod UIDs/specs/container
identities/restart counts and 29 namespace Service identities/specs unchanged.
All trial/observed child PIDs absent; collection, periodic and lifecycle locks free.
Legacy watcher inactive, LIVE_AUTO false, production executor DISABLED.
No services restarted, protected resources/configuration modified, UE deployed,
continuous monitoring enabled, commit or push performed.

Evidence: owner-only `.runtime/l3-4d-preflight`, `.runtime/l3-4d-trial` and
`.runtime/l3-4d-postflight`; sanitized [trial-results.json](trial-results.json).
No further collection is authorized by this completed trial. Historical L3-4C exact
exception remains unrecoverable; this successful trial supports the corrections
without proving the lost historical cause. Live failure-diagnostic formatting was
not exercised because neither attempt failed.
