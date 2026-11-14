# Guardian argument-shadowing correction (offline only)

The blocked code assigned the argparse Namespace to `args` at line 13, then
reassigned `args` to a bounded `/proc/<pid>/cmdline` list at former line 95.
The subsequent `args.duration` access (former line 114) would raise
`AttributeError`. Duration accesses also occur in construction of the timeout
and periodic command at line 67. No collection had run when this was identified.

`scripts/assurance/guarded_trial.py` now uses `cli_args` exclusively for parsed
options, `process_argv` for the command-line list, `member` for process memory
information, and `process` for the launched timeout Popen object. AST inspection
confirmed a single assignment to `cli_args`. The local `result` reuse occurs in
separate protected-check, journal-snapshot and final-report scopes and does not
change parsed options. Nested `query`, `read_cpu`, `pressure`, `journal`, and
`protected_check` do not assign CLI options.

The full entry-path review also identified default SIGTERM/KeyboardInterrupt
handling bypassing the controlled child-shutdown handler. SIGTERM/SIGINT now
request cancellation through that existing path, recording a failure, signalling
the timeout launcher, and waiting with the existing 20-second bound. Original
signal handlers are restored during finalization. No resource threshold changed.

New `tests/test_assurance_guardian_entry.py` executes the actual copied guardian
module using `python -m assurance.guarded_trial` in a temporary repository.
Production guardian, memory accounting, SQLite guard reader, `/proc` traversal,
GNU timeout and OS signals are real. Only external adapters and the periodic
payload are replaced with isolated local fixtures; fake kubectl is first in PATH.
No real runtime config, database, API credentials, Kubernetes or OSM endpoint is
accessible through the fixture. Local payload processes are reaped and checked
absent after shutdown.

Six entry-point regressions cover valid/invalid parsing, retained duration/cycle
arguments, fixed interval/outer deadline construction, real process-tree inspection,
RSS/HWM watchdog initialization, successful normal startup/exit, SIGTERM cancellation,
timeout exit, overlapping-process fail-closed detection, and invalid-governance
fail-closed detection. Existing memory regressions cover watchdog budget violations
and measurement failures. The normal startup test crosses the formerly failing
duration assertion after inspecting an actual command-line list.

## Activation compatibility

The existing activation command is unchanged: `--duration 150 --max-cycles 2`,
fixed 60-second interval, one collection, no automatic restart, observe-only.
Guardian/collector RSS limits remain 512 MiB; Kubernetes-child limit 256 MiB;
cycle wall limit 90 seconds and CPU limit 30 seconds. External API timeouts and
output caps, single-execution locks, exclusive start marker, journal visibility
checks, protected-health checks and disabled remediation remain unchanged.
The outer 165-second timeout still permits bounded finalization.

Separate confirmation is required before the live verification. Re-run the
read-only preflight under that confirmation to capture current health and source
hashes; the previous blocked preflight predates this correction. Preserve prior
backups and blocker evidence. No live collection, restart, workload change,
production journal write, commit or push was performed during this correction.

Memory measurement limitations documented in activation.md remain applicable;
this fix does not introduce additional PSS sampling or change memory semantics.

## Verification results

All **227 regression tests passed** (134 assurance tests in 139.870 seconds;
93 dashboard, lifecycle-safety, recovery, RAN-health and monitoring tests in
51.178 seconds). The six new real-entry-point tests also passed independently
in 42.966 seconds. All 16 offline foundation simulations passed. Syntax compilation,
CLI help through the repository venv/PYTHONPATH, AST variable review, and diff
whitespace checks passed. Read-only production journal verification returned
integrity `ok`, four incidents and 412 audit rows. No L3-4C trial directory or
guard-start marker exists. Structured results: `argument-shadowing-tests.json`.
