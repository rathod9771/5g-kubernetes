# Proposed L3-4 bounded live observe-only trial — separate approval required

This procedure has **not been executed**. It installs no systemd unit, timer, cron,
watcher or dashboard control endpoint. No service restart is required: each cycle
loads the corrected collector in a fresh process, and the existing dashboard reads
the local journal. The static dashboard UI is unchanged.

## Proposed limits and duration

- Start interval: 60 seconds, measured start-to-start; no catch-up bursts.
- Duration: at most 600 seconds of scheduled collection, maximum 10 attempts.
  Normal last start is at 540 seconds, so ten successful 12-second cycles finish
  before the ten-minute ceiling. A slow cycle delays the next start; cycles never overlap.
- Per cycle: at most 90 seconds (shortened to trial time remaining), 30 inherited
  CPU seconds, sampled collector-tree RSS 512 MiB, output 2 MiB combined stdout/stderr.
- Kubernetes: three allowlisted list attempts, 10-second process deadline,
  5-second API request timeout, sampled child-tree RSS 256 MiB, output 2 MiB per command.
- Prometheus/Alertmanager: eight-request budget, normally six GETs, four-second timeout,
  2 MiB responses; no receiver/rule mutation.
- OSM: at most three GETs plus one token authentication, four seconds per request,
  2 MiB responses, verified HTTPS/CA, no redirect or authorization retry. Only the
  two bound NS objects and existing bound operation are readable. No lifecycle methods.
- Sampled RSS watchdogs run approximately every 20 ms; they allow transient overshoot
  and are not kernel allocation ceilings. No RLIMIT_AS is imposed or raised.
- The supervisor has a 30-second CPU cap across the whole trial, keeps only ten small
  cycle summaries, and writes hash-linked journal audit plus an fsynced result file.
- Source failures back off next start to 120 then 240 seconds; successful recovery
  resets to 60. Known down scrape endpoints do not count as API transport failure.
  A timeout, CPU/RSS/output/process violation stops the trial without automatic retry.

## Preconditions and local backup

After approval, recheck the protected NSs:
`2c128db3-e27e-4dbe-8854-17751ba17680` and
`1b1cb300-b571-43f2-a567-6540895d7d0f`. Both must be READY/running/IDLE,
with no pending lifecycle operations. Recheck the 13 Open5GS pods plus CU/DU/fog
readiness, container restart counts and F1-C/NGAP SCTP. Preserve pod/Service
UID/spec/container identity and OSM operation-history baselines for post-comparison.
These are read-only preflight checks, distinct from trial request accounting.

Confirm monitoring Alertmanager is still the reviewed Service at
`http://10.104.28.42:9093`; review a changed endpoint before using it. Verify the
runtime snapshot still binds the intended namespace/cluster and both protected NSs.
Do not change RBAC, kubeconfig, TLS, workloads or monitoring settings to force success.

From `/home/rclab/5g-kubernetes`, run these checks and review them before starting:

```sh
systemctl is-active layer3-watcher.service
systemctl show ran-selector.service -p MainPID -p ActiveState -p NRestarts
curl --fail --max-time 5 http://127.0.0.1:8090/api/assurance
PYTHONPATH=scripts python3 -c 'import resource; print(resource.getrlimit(resource.RLIMIT_AS))'
```

Watcher must be inactive (systemctl normally returns 3); governance OBSERVE_ONLY,
LIVE_AUTO false, executor DISABLED and emergency_stop false. RLIMIT_AS soft value
must be -1. Ensure no other assurance collector is active and the exclusive trial
and backup directories below do not exist. Stop on a failed prerequisite.

Preserve the existing journal without overwriting it:

```sh
ran-selector/venv/bin/python3 - <<'PY'
import sqlite3, os
from pathlib import Path
folder=Path('.runtime/l3-4-backup')
folder.mkdir(mode=0o700,exist_ok=False)
source=sqlite3.connect('file:.runtime/assurance.sqlite?mode=ro',uri=True)
target=sqlite3.connect(str(folder/'assurance.sqlite'))
source.backup(target)
target.close(); source.close()
os.chmod(folder/'assurance.sqlite',0o600)
PY
```

## Start — execute only after separate approval

Run in the foreground from the repository root:

```sh
umask 077
P0_RUNTIME_CONFIG=/home/rclab/5g-kubernetes/.runtime/dashboard-service.json \
PYTHONPATH=/home/rclab/5g-kubernetes/scripts \
/usr/bin/timeout --signal=TERM --kill-after=5s 615s \
ran-selector/venv/bin/python3 -m assurance.periodic \
  --alertmanager-url http://10.104.28.42:9093 \
  --interval 60 --duration 600 --max-cycles 10 \
  --evidence-dir /home/rclab/5g-kubernetes/.runtime/l3-4-trial
```

The outer timeout gives bounded finalization time. Do not delete/reuse the trial
directory after success or failure. No automatic restart is installed.

## Stop

Foreground Ctrl-C or SIGTERM requests shutdown; an active collection is cancelled
and its process tree is killed/reaped, then the supervisor records stop evidence.
Alternatively, in another terminal:

```sh
touch /home/rclab/5g-kubernetes/.runtime/l3-4-trial/STOP
```

The STOP file is checked before each start and every second during waiting; an
in-flight cycle may finish within its existing deadline before the file is noticed.
A durable emergency_stop flag or governance change is checked on the same schedule;
they prevent further cycles but do not retroactively cancel a committed observation.
Use Ctrl-C/SIGTERM for immediate in-flight cancellation. Do not use unrelated
service restarts as a stop mechanism.

## Validation and acceptance

- At most ten `periodic_cycle` audit entries, no simultaneous collections or lost
  global collection lock. Existing one-shot CLI uses that same lock.
- Inspect `.runtime/l3-4-trial/result.json` and the audit journal for actual cycle
  duration, CPU, RSS, backoff, stop reason and failures. Child rusage RSS is a running
  maximum; sampled per-cycle tree RSS is the per-cycle peak metric.
- Fresh Kubernetes, nodes, Warning-event API, Prometheus, Alertmanager/group APIs and
  OSM should be LIVE; missing/stale sources must never be represented as healthy.
  KPM, uncollected protocol/session/latency measurements remain UNAVAILABLE.
- Four persistent WARNING scrape alarms retain their alarm/incident IDs; the logical
  endpoint correlation group contains four targets on one host. It does not prove
  a control-plane process outage or Kubernetes service degradation. No new incident
  is expected for unchanged targets. Fresh recovery requires two successful samples
  spanning the 60-second clearing window with no >90-second gap. Do not intentionally
  repair/break targets to test clearing during this trial; that is covered offline.
- All 44 policy classifications remain 8 SUPPORTED / 27 PARTIALLY_SUPPORTED /
  9 UNAVAILABLE; no production rules or remediation are activated.
- `/`, `/assurance.js`, `/api/config`, `/api/scenarios`, `/api/status`, `/api/pods`,
  `/api/assurance` remain HTTP 200. The existing dashboard displays service/incident
  status and journal audit; correlation details and trial metrics are available
  through telemetry/audit and the result file, not dedicated UI widgets.
- Compare CPU PSI first/last fresh samples and DU readiness/restarts/SCTP with the
  preflight baseline. Stop for changed protected readiness/connectivity/restarts,
  unexpected OSM operations, overlapping execution, resource-limit violation or
  evidence of host/DU performance harm. No UE is deployed to measure throughput.
  Do not claim PSI/DU latency impact is absent from readiness alone.
- Recheck both protected NSs, all protected pod/Service invariants, OSM operation
  history and deployment-state hashes. Watcher must remain inactive, LIVE_AUTO false,
  executor DISABLED, dashboard PID/restart count unchanged.
- After stop, allow the cached snapshot to age past 120 seconds and verify STALE.
  Do not start another cycle just to refresh the dashboard.

## Rollback / failure handling

Stop only the trial using the procedures above and preserve its result/audit/evidence.
No production configuration, unit or workload was deployed, so no production rollback
or service restart is necessary. Retain the journal and backup; do not replace the
current journal with the backup and erase audit history. Correct any source issue
in a separately approved task. No automatic restart, resource-limit relaxation,
remediation, UE deployment, commit or push is authorized.
