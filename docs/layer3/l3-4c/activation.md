# Proposed memory-verification trial — NOT EXECUTED

Separate approval required. This is a smaller trial than L3-4B: **two attempts at
60-second intervals, <=150 seconds**, one collector, no automatic restart. No service
restart is required; corrected guardian/collector modules load in fresh processes.
Do not reuse/delete the previous trial gate or evidence.

1. Recheck both protected NSs READY/running/IDLE, no pending operation, all 13 Open5GS
   pods and CU/DU/fog Ready, restart/UID/spec invariants, F1-C/NGAP, watcher inactive,
   LIVE_AUTO false, executor DISABLED and journal/audit integrity. Preserve a backup.
   Execute the prepared approval-only preflight, not during offline preparation:

   ```sh
   cd /home/rclab/5g-kubernetes
   ran-selector/venv/bin/python3 docs/layer3/l3-4c/preflight.py
   ```

2. Review `.runtime/l3-4c-preflight/preflight.json`, current Alertmanager endpoint,
   protected identities and owner-only backups. Check collection/trial locks are free.
   RLIMIT_AS must be unlimited; do not relax inherited limits. Existing database stays
   DELETE, old audit/incident history remains unchanged. Stop on any failed prerequisite.
3. Start once, after approval:

   ```sh
   umask 077
   P0_RUNTIME_CONFIG=/home/rclab/5g-kubernetes/.runtime/dashboard-service.json \
   PYTHONPATH=/home/rclab/5g-kubernetes/scripts \
   ran-selector/venv/bin/python3 -m assurance.guarded_trial \
     --preflight-dir /home/rclab/5g-kubernetes/.runtime/l3-4c-preflight \
     --trial-dir /home/rclab/5g-kubernetes/.runtime/l3-4c-trial \
     --duration 150 --max-cycles 2
   ```

   Outer timeout 165 seconds permits bounded finalization. Preserve the exclusive
   guard-start marker and trial directory on success/failure; no re-invocation is
   automatic. Source API allowlists/timeouts/output caps unchanged. Collector-tree
   RSS cap 512 MiB, Kubernetes-child RSS cap 256 MiB, cycle wall <=90 seconds and
   CPU 30 seconds remain enforced. Guardian current-image RSS/HWM cap 512 MiB now
   fails closed separately; historical launcher rusage is diagnostic only.
4. Inspect memory_samples in the guardian result and per-cycle process stats:
   - Guardian startup/current VmRSS and current-image VmHWM, optional PSS.
   - Legacy lifetime self rusage startup/end, explicitly NOT tree/current-image RSS.
   - Intended timeout/periodic/CLI/kubectl descendants only, stable PID/start identity,
     all-thread descendant traversal, <=32 processes / <=128 tasks per sample,
     <=256 retained identities. RSS-only watchdog sampling is about 20 ms; guardian
     PSS/cgroup diagnostics about five seconds. PSS missing is explicit, RSS failure
     stops the trial. Sampled watchdogs permit transient overshoot; not kernel quotas.
   - Existing cgroup current/peak are shared-context diagnostics, not exclusive
     guardian memory. Peaks are never reset and no cgroup is created/moved/modified.
   - No unexplained guardian image growth, no PID reuse/measurement error, no budget
     violation. Every scope/units/counter must be labelled. Do not infer low total
     physical use by summing RSS because shared mappings count repeatedly.
5. Stop on protected-health/lifecycle/resource/measurement failure or persistent
   SQLite contention. Use Ctrl-C/SIGTERM for cancellation, or:

   ```sh
   touch /home/rclab/5g-kubernetes/.runtime/l3-4c-trial/STOP
   ```

   STOP prevents subsequent starts; an in-flight cycle remains bounded. If either
   attempt reports failure, stop before any backoff attempt; do not retry or weaken
   checks. Preserve samples, flags, partial/atomic audit evidence and process identities.
6. Verify two attempts maximum, fresh data LIVE, KPM UNAVAILABLE, original four incident
   IDs retained, one monitoring-endpoint group without control-plane outage assertion,
   valid audit prefix, unchanged protected resources/NS histories, dashboard HTTP 200,
   watcher stopped and automation disabled. Confirm all trial children gone and locks
   free, then cached data ages to STALE after 120 seconds. No extra collection.

Rollback is stopping the trial and preserving evidence. No workload/configuration,
service, database migration or alert deployment is included. Indefinite monitoring,
additional verification or monitoring repairs need independent approval.

## Offline argument-shadowing correction

The guardian CLI Namespace and process command-line list now have distinct names;
controlled SIGTERM/SIGINT cancellation follows bounded child shutdown. See
[argument-shadowing-fix.md](argument-shadowing-fix.md) for entry-point regression
coverage. The activation command and resource thresholds above are unchanged.
Re-run the preflight only after separate live confirmation; its prior blocked
snapshot predates these corrected sources. No trial was executed by this fix.
