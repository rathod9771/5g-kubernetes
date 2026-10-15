# Proposed corrected L3-4D verification — NOT EXECUTED

Separate live approval required. No service restart required. Preserve the consumed
L3-4C gate and all original evidence. New owner-only gate/evidence paths prevent reuse.

1. After approval, run the read-only preflight once:

   ```sh
   cd /home/rclab/5g-kubernetes
   ran-selector/venv/bin/python3 docs/layer3/l3-4d/preflight.py
   ```

   Review `.runtime/l3-4d-preflight/preflight.json`: both protected NSs
   READY/running/IDLE, unchanged operation history, CU/DU/fog and all 13 core pods
   Ready, F1-C/NGAP SCTP established, no pending lifecycle operations, watcher
   inactive. Verify production journal integrity `ok`, preserve audit prefix and
   four incident/alarm identities, verify OBSERVE_ONLY/no emergency stop,
   LIVE_AUTO false, executor DISABLED, free collection/periodic/lifecycle locks,
   current reviewed source hashes and Alertmanager endpoint. RLIMIT_AS remains
   unlimited; never raise/relax inherited limits. Stop on failed prerequisites.
2. Start exactly once:

   ```sh
   umask 077
   P0_RUNTIME_CONFIG=/home/rclab/5g-kubernetes/.runtime/dashboard-service.json \
   PYTHONPATH=/home/rclab/5g-kubernetes/scripts \
   ran-selector/venv/bin/python3 -m assurance.guarded_trial \
     --preflight-dir /home/rclab/5g-kubernetes/.runtime/l3-4d-preflight \
     --trial-dir /home/rclab/5g-kubernetes/.runtime/l3-4d-trial \
     --duration 150 --max-cycles 2
   ```

   The guardian launches periodic with interval 60 and `--stop-on-source-error`:
   any unavailable required source or execution failure stops this no-retry trial.
   No restart, catch-up collection or further invocation. Maximum one collector.
   Two attempts maximum, <=150-second collection window; outer timeout 165 seconds
   allows existing bounded cleanup. Collector wall <=90 seconds, CPU <=30 seconds,
   RSS <=512 MiB; Kubernetes child <=256 MiB, unchanged timeouts/output budgets.
   Guardian image RSS/HWM <=512 MiB. Current-image RSS/HWM/PSS and legacy rusage
   remain separate. Confirm missing live-process RSS fails closed; confirmed Linux
   teardown is explicitly marked EXITING_NO_ADDRESS_SPACE, not service health.
3. Inspect per-cycle `diagnostic` and process `diagnostics` in local result/audit:
   stage, safe class/message, pre-cleanup exit/signal, cleanup exit/signal, timeout,
   watchdog classification, shutdown reason, bounded stderr class excerpt. Unknown
   errors stay redacted. Do not re-run a failure to acquire more diagnostics.
   Critical protected-health/lifecycle/resource checks must remain independent.
   Stop on unhealthy/unknown safety, contention or watchdog failure; use SIGTERM
   for prompt controlled cancellation, or create `.runtime/l3-4d-trial/STOP` to
   prevent further starts while a bounded in-flight cycle finishes.
4. Recheck protected NS/pod identities/specs/restarts and operation history,
   F1-C/NGAP, seven dashboard APIs, journal integrity and audit prefix, four
   incidents and monitoring correlation. No control-plane outage claim from scrape
   failures. LIVE ages to STALE after 120 seconds; KPM remains UNAVAILABLE.
   Verify all children gone and all execution locks free. Preserve every start
   marker/result on success or failure. No monitoring repair or workload rollback
   is authorized; rollback means stop and retain evidence.

A successful run would validate the corrected diagnostic pipeline, not prove the
lost L3-4C exception retrospectively. Independent simultaneous tree-PSS/direct-proc
comparison still needs a bounded observer prepared before launch if included in
that approval; the guardian itself samples tree RSS and own PSS. No such live
observer or collection is started by the offline work in this phase.
