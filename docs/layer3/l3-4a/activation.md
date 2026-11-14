# L3-4A corrected trial procedure — NOT executed

Separate approval must cover loading the corrected dashboard Python code and **one**
finite observe-only trial. No production database reset, WAL migration, monitoring
repair, lifecycle operation, remediation or indefinite scheduling is proposed.
The production journal remains DELETE mode. WAL was tested on copies only.

1. Verify existing L3-4 evidence/backups remain intact. Keep a SQLite backup of the
   current journal and reviewed dashboard source before reload. Recheck the protected
   Fog-RAN `2c128db3-e27e-4dbe-8854-17751ba17680` and Open5GS
   `1b1cb300-b571-43f2-a567-6540895d7d0f`, all 13 core pods and CU/DU/fog readiness,
   restart/container identities, F1-C/NGAP, no pending OSM operation, watcher inactive,
   OBSERVE_ONLY, LIVE_AUTO false, executor DISABLED and emergency_stop false.
2. The following approval-only preflight script performs the reviewed read-only checks
   and SQLite backup, preserving the metadata required by the guardian. It writes
   only owner-only local evidence/backup files. It must not be run as an offline test:

   ```sh
   cd /home/rclab/5g-kubernetes
   ran-selector/venv/bin/python3 docs/layer3/l3-4a/preflight.py
   ```

   Inspect `.runtime/l3-4a-preflight/preflight.json`. Confirm Alertmanager is still
   `http://10.104.28.42:9093`. Stop on changed identity/endpoint/health or any failed
   check. Do not repair resources or weaken limits to proceed. The script uses
   verified bounded OSM reads, bounded pod/Service discovery, and timeout-limited
   read-only SCTP inspection. Existing archive evidence is never overwritten.
3. The dashboard must reload the corrected `assurance.api`/`Store` code to remove
   its old write reservations. Only after approval and an authenticated admin path:

   ```sh
   sudo systemctl restart ran-selector.service
   ```

   No other service restart is included. Do not request/store a sudo password,
   change sudoers or repeatedly retry failed authentication. If admin authentication
   is unavailable, stop for the user to execute the exact command manually.
   Verify seven reviewed APIs return HTTP 200 and safety flags remain disabled.
   Preserve the post-reload PID/restart baseline for trial comparison. Source changes
   are already prepared; no package install, cluster resource or alert activation is
   needed. If deployment fails, preserve evidence and stop.
4. The trial directory `.runtime/l3-4a-trial` must not exist. No other collector may
   be active, and both `.l3-periodic.lock` and `.l3-collection.lock` must be free.
   Confirm soft RLIMIT_AS is unlimited; never raise incompatible hard limits.
   Start **exactly once**, in the foreground:

   ```sh
   umask 077
   P0_RUNTIME_CONFIG=/home/rclab/5g-kubernetes/.runtime/dashboard-service.json \
   PYTHONPATH=/home/rclab/5g-kubernetes/scripts \
   ran-selector/venv/bin/python3 -m assurance.guarded_trial
   ```

   The guardian constructs the same reviewed periodic command: interval 60 seconds,
   duration <=600 seconds, maximum ten attempts, outer timeout 615 seconds for
   bounded finalization. No automatic restart or catch-up burst is installed.
   Collector tree RSS watchdog 512 MiB, Kubernetes child RSS 256 MiB, per-cycle wall
   <=90 seconds (shortened at deadline), CPU 30 seconds, output 2 MiB remain unchanged.
   Per-source API limits and read allowlists remain unchanged.
5. Independent resource/process checks run before journal reads. Independent protected
   pod/restart/SCTP/NS/lifecycle checks run initially and at 60-second intervals, with
   bounded transports, before SQLite access. Record their extra request costs separately
   from the collector. These callbacks do not infer health from stale/busy journal data.
   The journal reader is mode=ro + query_only, a coherent read transaction, explicitly
   closed; bounded SQL read retries total <=0.15 seconds normally, with 25 ms waits.
   BUSY is UNAVAILABLE, never healthy. It creates a local PAUSE interlock preventing
   further starts while visibility is unknown. It keeps critical checks running, then
   requests shutdown if visibility remains unknown for five seconds. A critical failure,
   lifecycle conflict, overlap or watchdog violation stops immediately. The periodic
   worker also fails closed if its own governance read is unavailable.
6. Stop with foreground Ctrl-C/SIGTERM for active cancellation, or:

   ```sh
   touch /home/rclab/5g-kubernetes/.runtime/l3-4a-trial/STOP
   ```

   STOP prevents subsequent starts; in-flight cancellation rolls back the atomic
   telemetry/alarm/incident/audit transaction. A SIGKILL outside that transaction may
   leave normal SQLite recovery files; preserve them, never delete journal/WAL files
   or use immutable=1 to pretend recovery is unnecessary. The guardian handles a stop
   request by signalling the periodic process through its outer timeout; no protected
   component is restarted. Shutdown/result evidence is retained even if stop auditing
   cannot write because of contention. Do not delete the exclusive gate to retry.
7. Acceptance: exact attempted/successful/failed/skipped counts, zero overlap, telemetry
   and all alarm ingestion either fully committed or rolled back per cycle, valid audit,
   preserved four incident/alarm IDs and histories, one endpoint-only correlation group,
   no control-plane outage inference. Source errors stay UNAVAILABLE; aged snapshots
   become STALE after 120 seconds; KPM remains UNAVAILABLE. Keep 44 policy classifications
   unchanged. Check actual DB wait/critical-check latency and CPU/RSS/PSI/DU evidence;
   do not infer DU throughput/timing impact from Ready pods alone.
8. Recheck protected NS/pod/Service/operation/deployment-state invariants, F1-C/NGAP,
   watcher inactivity and disabled execution. Confirm all trial processes stopped and
   execution locks released; no background collection remains. Preserve result files,
   audit history, journal and backups. Stop after this trial, including any early failure.

Rollback: stop only the trial. If the corrected dashboard fails, restore only its
reviewed previous source revision under separate approved dashboard-only reload.
Do not restore an old database over the current journal or erase collection/audit
records. No cluster/workload/catalog/monitoring rollback is needed. Any WAL migration,
SQLite recovery requiring writes, subsequent trial, or indefinite deployment needs
an independent review/approval.
