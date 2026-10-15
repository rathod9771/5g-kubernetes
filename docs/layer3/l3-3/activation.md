# L3-3 proposed activation — NOT executed; separate approval required

Only one fresh-process observe-only collection is proposed. No dashboard restart is
required: the corrected reader is loaded by the collection CLI; dashboard reads the
existing journal. Do not use `/tmp/l32-approved-single-collection.py` or set RLIMIT_AS.
Do not configure a watcher, timer, cron job, production rules, or remediation.

From `/home/rclab/5g-kubernetes`, after separate approval:

1. Recheck protected NSs `2c128db3-e27e-4dbe-8854-17751ba17680` and
   `1b1cb300-b571-43f2-a567-6540895d7d0f`: READY/IDLE, no pending lifecycle
   operation; check all 13 Open5GS pods, CU/DU/fog readiness and F1-C/NGAP SCTP.
   These are separate preflight reads, not collector retries. Stop if identity,
   operation state, readiness, or connectivity differs from the approved baseline.
2. Verify the current service endpoint remains the reviewed monitoring Alertmanager
   Service at `http://10.104.28.42:9093`; if it changed, review its replacement
   before executing. Confirm the runtime configuration still references the
   protected workload namespace and verified kubeconfig. No RBAC grants are proposed.
3. Run these exact local checks; examine their results before proceeding:

   ```sh
   systemctl is-active layer3-watcher.service
   curl --fail --max-time 5 http://127.0.0.1:8090/api/assurance
   PYTHONPATH=scripts python3 -c 'import resource; print(resource.getrlimit(resource.RLIMIT_AS))'
   ```

   Watcher must be `inactive` (systemctl normally exits 3); dashboard must show
   `live_auto_enabled=false` and `production_executor=DISABLED`.
   RLIMIT_AS soft value must be `-1` (unlimited virtual address space). Do not raise
   system/inherited hard limits to force execution; stop on incompatible limits.
4. Preserve the journal with SQLite backup, using this exact local-only command:

   ```sh
   ran-selector/venv/bin/python3 - <<'PY'
   import sqlite3, os
   from pathlib import Path
   folder=Path('.runtime/l3-3-backup')
   folder.mkdir(mode=0o700,exist_ok=False)
   source=sqlite3.connect('file:.runtime/assurance.sqlite?mode=ro',uri=True)
   target=sqlite3.connect(str(folder/'assurance.sqlite'))
   source.backup(target)
   target.close(); source.close()
   os.chmod(folder/'assurance.sqlite',0o600)
   PY
   ```

   Keep the L3-2 dashboard revision/backups. No dashboard code/configuration needs
   deployment in this phase. The journal backup is diagnostic, not an instruction
   to erase or roll back audit records.
5. Perform exactly one supervised collection:

   ```sh
   P0_RUNTIME_CONFIG=/home/rclab/5g-kubernetes/.runtime/dashboard-service.json \
   PYTHONPATH=/home/rclab/5g-kubernetes/scripts \
   ran-selector/venv/bin/python3 -m assurance.bounded_once \
     --alertmanager-url http://10.104.28.42:9093 \
     --evidence-dir /home/rclab/5g-kubernetes/.runtime/l3-3-once
   ```

   The evidence directory must not exist. It is created exclusively before execution;
   a failed attempt consumes the gate. Never delete it to retry. The supervisor
   bounds wall time to 90 seconds, inherited CPU to 30 seconds, sampled process-tree
   RSS to 512 MiB and combined stdout/stderr to 2 MiB. Child Kubernetes reads have
   10-second process deadlines, 5-second API request deadlines, sampled tree RSS
   256 MiB, combined output 2 MiB, and three allowlisted list attempts. No virtual
   address limit is imposed. RSS checks occur every approximately 20 ms and are
   watchdog thresholds, not kernel-enforced allocation ceilings; transient overshoot
   is possible. All subprocess groups are killed/reaped on completion/failure.
6. Verify HTTP 200 from `/`, `/assurance.js`, `/api/config`, `/api/scenarios`,
   `/api/status`, `/api/pods`, `/api/assurance`. Inspect the saved `telemetry.json`
   and `result.json`: node Ready/pressure conditions, CU/DU/fog workloads, core count
   13, per-container restart counts, timestamped warning events, three Kubernetes
   list attempts, Prometheus and Alertmanager provenance. Preserve policy counts
   8 SUPPORTED / 27 PARTIALLY_SUPPORTED / 9 UNAVAILABLE; none are newly promoted.
   Compare incident IDs against the preflight snapshot: four existing target
   incidents should update, not duplicate. KPM remains UNAVAILABLE.
7. Recheck protected identities/specs/restarts, OSM operation history, safety flags
   and watcher inactivity. Verify source data ages to STALE after its freshness
   window. Record missing data as UNAVAILABLE. Stop. Do not repeat the collection
   on any error or restart any service.

Expected monitoring traffic remains six HTTP requests (budget eight), three OSM GETs
for the protected state (plus at most one fresh authentication request), and three
Kubernetes list commands instead of two. Kubectl discovery may add GET requests;
list attempt counters are not asserted to be wire-level HTTP counts. No sustained
cadence is enabled. Minimum collector RBAC is list pods/events in the bound namespace
and list nodes cluster-wide, plus API discovery access. Existing credential grants
are unchanged; this command allowlist does not narrow server-side RBAC. Any RBAC
change requires independent approval.
