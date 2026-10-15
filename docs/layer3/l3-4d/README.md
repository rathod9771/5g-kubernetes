# L3-4D bounded-process investigation (offline)

## Historical finding: original exception remains unrecoverable

The affected launch chain was guardian -> GNU timeout -> `assurance.periodic` ->
`assurance.cli` -> bounded kubectl/API adapters. Original timeout PID 1616828;
periodic PID 1616829 was captured by guardian; collector CLI PID and its actual
exit/signal were not retained. Periodic returned exit 1, propagated by timeout;
guardian returned 1 with `guard_failure: null`. No guardian termination is recorded.

The collector definitely started: it committed a telemetry snapshot and alarm
observations, growing audit from 412 to 444. Kubernetes pods, Prometheus,
Alertmanager and both OSM sources were LIVE; node/event reads unavailable (2 of
3 Kubernetes reads failed). Existing failure reasons only say Node API unavailable
and Warning event API unavailable; they do not establish RBAC, network or code
causes. KPM was intentionally unavailable. Four incident records were unchanged.

The failure was detected at periodic `_execute`'s `KubernetesReader.run_json`
boundary, not established as a specific child exception. That boundary maps
startup, nonzero exit, timeout, RSS/output/memory-measurement and JSON failures to
`KubernetesReadError`; periodic caught that without binding/persisting the error
and wrote only BOUNDED_PROCESS_FAILED. The reader discarded private stderr and
never recorded child return code, stage or cleanup cause. Its output/exception
buffers disappeared on exit; `trial-output.log` only repeats the generic result.
Raw original stdout/stderr cannot be recovered from these files. Exact original
exit/signal, Python exception, and precise failing stage are UNVERIFIED.

Measured collection wall 3.451986 s, CPU 1.426211 s, sampled RSS 71.992188 MiB and
output 44720 bytes were below 90 s / 30 CPU-s / 512 MiB / 2 MiB bounds. This gives
no evidence of a timeout or quota breach, and does not rule out an unobserved spike.
CLI args were correctly preserved after the prior correction. No evidence proves
CLI parsing or guardian cancellation caused the historical failure.

## Confirmed offline defects and corrections

1. **Process-exit memory race.** Real short-lived local Python subprocesses
   reproduced `RSS unavailable for live process` at stage memory_measurement in
   6/40 runs; their cleanup return code was 1 with a normal ValueError stderr class.
   Linux can clear `mm` before zombie state is published. `memory.process_memory`
   previously required RSS unless already Z/X, misclassifying that teardown window.
   It now checks the Linux PF_EXITING flag (0x4, verified in installed
   `/usr/src/linux-headers-6.8.0-147/include/linux/sched.h`) only when RSS is absent.
   Confirmed teardown is labelled EXITING_NO_ADDRESS_SPACE. Live missing RSS
   without that flag still fails closed; PID birth checks, descendant limits,
   timeouts and memory caps remain intact. No sleeps/retries or higher budgets.
   After correction: 0/40 unexpected measurement stages. This is a plausible
   explanation for the historical failure and two nested adapter failures,
   **not proof of their exact cause**.
2. **Last-attempt exit propagation.** The full offline guardian/periodic/CLI fixture
   showed a failed final attempt's process_failure reason overwritten by max_cycles,
   incorrectly returning success. max_cycles now only replaces normal duration
   completion. Original L3-4C failed before its last permitted attempt, so this
   separate defect does not explain that event.
3. **No-retry trial policy.** Guardian now explicitly supplies
   `--stop-on-source-error`; required-source unavailability stops the live trial
   rather than entering the prior source-error backoff path. Existing offline
   backoff tests remain valid for the explicit legacy/default Trial policy.

## Diagnostic correction

New `diagnostics.py` admits only reviewed whole messages and known exception-class
names. Arbitrary exception messages, commands, URLs, environment/configuration,
tracebacks and stderr content never enter diagnostic records. Up to 4096 trailing
stderr bytes are inspected in memory for an allowlisted Python exception class;
the stored excerpt is only that class or a private-text omission marker. Raw
buffers remain within the existing combined 2 MiB stream cap. Each reader stores
at most eight fixed-shape diagnostic records. No raw output is written to disk.

Reader failures now carry stage, exception class, safe message, child exit/signal
observed before cleanup (null means unknown), timeout/watchdog classification,
shutdown reason, bounded stderr class excerpt, stdout byte count and PID. After
cleanup, cleanup_exit_code/signal are recorded separately: a watchdog SIGKILL must
not be misreported as the initial cause. SIGXCPU identifies a CPU-limit signal;
other child signals are explicitly unverified rather than assumed RSS breaches.

Periodic records the structured error in cycle evidence and audit; adapter reader
stats preserve failures even when adapters report UNAVAILABLE. Unexpected generic
collection exceptions are sanitized too. Collector CLI suppresses arbitrary Python
tracebacks and emits a safe exception-class line for the parent. Guardian startup
and running failures also use semantic redaction, preserving reviewed safety
assertion messages. Early parent cancellation is caught, recorded and cleaned up.
No production remediation code or SQLite schema changed.

## Scope, evidence and limitations

Original `.runtime/l3-4c-*` evidence and all 444 audit records are preserved.
Owner-only `.runtime/l3-4d-offline/journal-copy.sqlite` was used for two offline
persistence replays. Original audit prefix, four full incident records and alarm
identities remained intact on the copy; the production journal was opened only
read-only and verified unchanged. See `journal-integrity.json`.

Existing memory/CPU/output/cancellation/identity watchdogs, collection and lifecycle
locks, OBSERVE_ONLY, disabled production executor and simulation isolation remain
in force. No production config, service or protected workload changed; no live
collection, API polling, restart, UE deployment, commit or push was performed.
Cached service-health evidence is from the prior trial; this offline phase makes
no new assertion of current protected-service health.

Historical exact cause remains unknown. A corrected live run requires separate
approval and fresh preflight; the new diagnostics will identify a recurring failure
without a diagnostic retry. Semantic redaction intentionally sacrifices arbitrary
error text to protect credentials/subscriber secrets. Sampled RSS is not a kernel
quota, and cgroup memory is shared context. Independent tree-PSS/direct-proc memory
verification remains separate from this bounded-process correction.

See [activation.md](activation.md) and [test-results.json](test-results.json).

## Final validation

**248 unique regression tests pass**, including all 16 offline simulations. The
first assurance discovery ran 154 tests; its only failure was the obsolete
raw-exception-message assertion, which was changed to require semantic redaction
and an active fail-closed watchdog. The final combined rerun passed all 128 memory,
diagnostic, dashboard, lifecycle-safety, recovery, RAN-health and monitoring tests.
Other assurance suites, including the eight executable-entry tests, passed in the
first run. Syntax compilation and whitespace checks passed. Diagnostic tests cover
startup/nonzero exit/Python exception, missing/malformed output, SIGTERM/SIGINT,
RSS/CPU/output watchdogs, parent cancellation, cleanup, source-error no-retry policy,
lock release, redaction and exit propagation. See test-results.json for run details.

Changed source files: diagnostics.py (new), kubernetes.py, memory.py, periodic.py,
cli.py and guarded_trial.py under scripts/assurance. Regression files:
test_assurance_diagnostics.py (new), test_assurance_memory.py and
test_assurance_guardian_entry.py under tests. All additional files are local L3-4D
documentation/evidence and the inert approval-only preflight. No budget was raised;
the extra /proc stat read occurs only in the missing-RSS teardown edge case, and
diagnostic formatting occurs only on failures. No live performance claim is made.
