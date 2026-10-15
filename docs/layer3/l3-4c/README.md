# L3-4C guardian memory accounting — validated and corrected offline

The exact historical value **598236 KiB (584.21 MiB)** was a lifetime self high-water
counter carried by the guardian's launch path, not measured guardian-image RSS or
collector-tree RSS. The live launcher (`codex`, PID 629731) has VmHWM=598236 KiB,
exactly matching both archived guardian reports. A minimal program using the same
direct-exec path reproduces ru_maxrss=598236 KiB while its own VmRSS/VmHWM are only
11584 KiB (11.31 MiB). A controlled 96 MiB allocation followed by exec reproduces
108544 KiB lifetime max versus 11964 KiB current-image RSS/HWM. A heredoc child path
instead reports approximately 11 MiB. See `launch-reproduction.json`.

This resolves the discrepancy: the guardian recorded RUSAGE_SELF.ru_maxrss without
startup baseline or current-image samples and named it guard_peak_rss_kib. It never
represented the monitored tree. The Linux lifetime accounting retained execution-path
history; current-image /proc counters and simultaneous descendant RSS have different
semantics. No claim is made that the original guardian ever used only 11.31 MiB during
all of L3-4B—that is the minimal reproduction, not an absent historical sample.
Historical per-image guardian peak cannot be reconstructed retroactively, but the
high number itself is now reproduced and attributed to launcher high-water carryover.

## Investigation and reconciled semantics

- Fast VmRSS: resident mappings per PID; includes shared mappings at full size.
  VmHWM: this process image's RSS high-water, distinct from lifetime rusage carryover.
- PSS: resident shared pages divided among all mapping processes; diagnostic estimate
  of attributed physical memory. It is not the unchanged RSS watchdog metric.
- Virtual memory/VmSize: address-space mappings/reservations, not resident physical
  consumption. No RLIMIT_AS is added or relaxed; Go reservations remain permitted.
- Tree RSS: sum of concurrent intended descendant RSS; shared libraries/mappings are
  counted per process, intentionally conservative for existing 256/512 MiB budgets.
  Summing independent per-PID historical maxima would not be a simultaneous tree peak.
- cgroup memory.current: charged memory including file cache/kernel memory and other
  members/child groups. memory.peak is historical for that shared cgroup, not a new
  trial's peak. Neither counter is reset; no cgroup is created or altered.

The controlled parent + two children (16/24 MiB allocations) measured mean tree RSS
**75.48 MiB**, PSS **57.15 MiB**. Direct per-PID VmRSS and smaps_rollup RSS sums
matched at 75.48 MiB;
direct PSS matched approximately 57.15 MiB.
The benchmark parent's image RSS/HWM was 15.07 MiB.
Its existing shared VS Code/tool cgroup reported current
2.399 GiB and peak
2.764 GiB—clearly not this three-process tree alone.
These are controlled local observations, not a new production monitoring collection.

Alternative causes checked: bounded host argv prefixes showed no >64 KiB command
lines. Bounded read-only OSM diagnostics obtained a 4849-byte/three-item history and
63339/394903-byte NS documents. Controlled JSON/YAML parsing peaked at approximately
12–18 MiB current-image HWM, not 584 MiB. Raw responses/credentials were not displayed
or retained. One local diagnostic indentation error was corrected before completion;
no periodic monitoring was launched. Parser timings are in the two probe JSON files.

## Exact code changes

1. `scripts/assurance/memory.py`: common bounded /proc sampler, start-time identity
   validation before/after reads, descendant-parent validation, all-thread children,
   <=32 processes / <=128 tasks per sample / <=256 retained identities. Report explicit
   exits, optional unavailable PSS/cgroup counters, and fail closed for malformed,
   denied, missing-live-RSS, negative or reused-PID measurements. Fast exit races are
   rechecked; zombies/exited processes are explicit, not assumed healthy. Diagnostics
   expose RSS, image HWM, VmSize, rollup RSS/PSS and scoped cgroup current/peak.
2. `scripts/assurance/kubernetes.py`: use the sampler for the existing conservative RSS
   watchdog, propagate measurement failures as bounded-process errors, preserve
   timeout/output/cancellation cleanup and capture root identity before sampling.
   Cleanup verifies start identity before signaling a reused root process group.
3. `scripts/assurance/guarded_trial.py`: replace global /proc discovery with the owned
   timeout/periodic/CLI/kubectl descendant tree; argv prefixes bounded to 4096 bytes.
   Record guardian image HWM, sampled own RSS/PSS, startup baseline, owned tree sample,
   and shared cgroup diagnostics. Keep raw legacy rusage clearly named diagnostic-only.
   Guardian own image RSS/HWM >512 MiB fails closed, including finalization; existing
   collector 512 MiB and Kubernetes 256 MiB RSS thresholds remain unchanged. Add bounded
   trial duration/count and fresh evidence-directory options for a smaller future trial;
   original defaults remain 600 seconds / ten attempts and no automatic restart.
4. `tests/test_assurance_memory.py`: 16 regressions for parent/children, non-leader
   threads, shared mappings, exits, reuse/parent mismatch, malformed/denied/missing
   entries, budgets, spikes, cancellation and lifetime-versus-image exec accounting.
5. Report/JSON measurements and an approval-only preflight/activation plan under this
   directory. No dashboard/static UI, workload, database, monitoring or RBAC deployment.

Process sessions/groups do not define ownership: a descendant remains included even
if it starts a new session. Parent/start identities bound ownership instead. Root/child
exits are tolerated explicitly; measurement errors stop protection rather than return
zero silently. Cross-process collection locks remain unchanged. The sampled peak is
retained per trial; image HWM captures guardian spikes between its slower samples.

## Tests, overhead and safety

**221 tests passed**, including 16 new memory regressions and all 16 existing offline
simulations. Existing SQLite/incident preservation, resource watchdog and protected-NS
safety tests passed. Compile and whitespace checks passed. Controlled processes were
stopped/reaped; no memory-test process is intentionally left running.

RSS-only sampling on the three-process tree: mean
1.515 ms, maximum
5.180 ms; CPU for 100 samples
0.129709 seconds. Detailed PSS sampling mean
4.375 ms; CPU/latency depends on process/thread count and
host scheduling. Smaps/PSS is diagnostic at the guardian's roughly five-second cadence,
not the roughly 20 ms child watchdog path. Cadences include traversal and bounded
health-check latency; very short spikes may be missed by tree sampling. Guardian
image HWM records its own between-sample high-water. RSS sum can overcount shared pages;
this preserves conservative memory protection rather than weakening limits based on PSS.

No full live guardian trial was run with the correction, so actual future guardian
footprint, larger Go process trees and sustained host PSI/DU impact still require
approval-based validation. L3-4B's 74.35 MiB collector-tree observation remains distinct
from the incorrect guardian lifetime-max label; old evidence is not rewritten.

Read-only safety verification: watcher inactive; dashboard active PID 1435287,
NRestarts=0; OBSERVE_ONLY, LIVE_AUTO=false, executor DISABLED, four incident IDs and
412 audit rows retained. No service restart, protected NS/workload mutation, UE,
periodic monitoring trial, commit or push occurred. Only bounded diagnostic OSM reads
and local controlled process tests were performed.

## Next activation

`activation.md` proposes two cycles at 60 seconds, <=150 seconds, new exclusive
preflight/trial paths, unchanged API/resource safeguards, no service restart and no
automatic retry. Compare current-image RSS/HWM/PSS to owned-tree RSS and diagnostic
cgroup counters, retaining startup legacy rusage separately. Stop for measurement,
resource, source, SQLite or protected-health failure; verify final child/lock cleanup
and STALE data. No command from that procedure has been executed. Separate approval
is required before any further live periodic collection.
