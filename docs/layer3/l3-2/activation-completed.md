# L3-2 dashboard activation and single collection — completed with a telemetry gap

The user manually restarted `ran-selector.service`. The agent verified that the
reviewed assurance routes and JavaScript are loaded; no additional service restart
was performed. Existing RAN Selector APIs, `/assurance.js` and `/api/assurance` all
returned HTTP 200. Live snapshot DOM rendering was checked offline with no network
calls. Source/configuration hashes remain unchanged during this activation.

Exactly **one** approved observe-only collection finished at **14 October 2026 11:16:38 IST**.
The journal contains exactly one `read_only_collection` record. An exclusive
execution marker prevents another invocation. No scheduled collection or automatic
retry was configured. The 30s dashboard refresh reads only the local journal.

## Collection and incident results

- Six Prometheus/Alertmanager GETs succeeded, with no HTTP failures; three OSM GETs
  and one token-authentication POST succeeded. No lifecycle mutation was attempted.
- Four `PrometheusTargetDown` WARNING alarms were recorded: controller-manager
  `10257`, etcd `2381`, proxy `10249`, and scheduler `10259` on the node IP.
- Four linked WARNING incidents are locally ESCALATED for operator investigation.
  This means incident-journal escalation, not external paging or remediation.
  There are zero disruptive pending approvals and zero production executor calls.
- The CPU PSI some five-minute fraction was **16.08%** when collected. This is
  scheduling-pressure evidence, not CPU utilization or proof of RAN degradation.
- Monitoring pipeline state is DEGRADED because the four scrape gaps persist.
- The two Kubernetes collection attempts failed. Their subprocesses inherited the
  **512 MiB virtual-address limit added by the activation wrapper**, preventing Go's
  page-summary allocation before kubectl could start. The agent confirmed this with
  offline `kubectl version --client` under the same limit. This was a mistake in the
  collection boundary, not evidence of a Kubernetes outage. **No second collection
  was performed.** The collector correctly preserves Kubernetes/service telemetry
  as UNAVAILABLE and does not generate unsupported workload-failure alarms.

The virtual-memory cap should not be reused for Go child processes. Correcting that
boundary and running another collection require a separately authorized follow-up;
no workaround or live resource/configuration change was attempted here.

## Provenance and dashboard validation

Captured alarms and measurements have LIVE source provenance and their original
sample timestamps. The one-shot snapshot aged beyond 120s during validation and now
renders **STALE**. No refresh collection was used to keep it artificially live.
Kubernetes telemetry from this collection remains **UNAVAILABLE**; current KPM is
explicitly UNAVAILABLE in the API and operational audit. No simulated metric is
presented as live. The renderer displays alarms, incident history, unavailable/stale
labels, KPM-unavailable audit and disabled automation. All 44 policy classifications
are preserved: **8 supported, 27 partially supported, 9 unavailable**.

## Protected-service verification (separate read-only pre/post checks)

Both protected NSs remain READY/IDLE:

- Fog-RAN: `2c128db3-e27e-4dbe-8854-17751ba17680`.
- Open5GS: `1b1cb300-b571-43f2-a567-6540895d7d0f`.

CU, DU and fog are Ready; all 13 Open5GS pods are Ready. Pod UIDs, specifications,
container identities and restart counts match preflight. Active deployment-state
hash is unchanged. OSM operation history matches preflight, with **zero unexpected
lifecycle operations**. CU/DU F1 SCTP and CU NGAP SCTP remain established. This does
not establish fresh F1/NG Setup, UE registration, PDU or user-plane verification.
Current E2 SCTP remains absent, and KPM is unavailable.

The watcher remains inactive. Mode is OBSERVE_ONLY, LIVE_AUTO=false and production
executor DISABLED. Audit hash verification passed. No monitoring routes/targets,
control-plane bindings, Kubernetes resources, protected workloads or NSs were changed.
No commit or push was performed.

## Measured cost and limits

Collection plus journal recording: **12.16s elapsed**, **0.770s user CPU + 0.036s
system CPU** for the collector. Resident memory increased from **23,736 to 32,048 KiB**;
peak RSS was **32,836 KiB (32.1 MiB)**. Failed child-process CPU was negligible.
Hard limits were 90s wall time, 30s CPU and 512 MiB virtual address space; the last
limit caused the Kubernetes telemetry gap described above. Six monitoring GET
latencies were approximately 0.83–2.84ms. The active dashboard cgroup reported
**40,669,184 bytes (38.8 MiB)** resident/accounted memory at final validation;
its cumulative CPU includes startup and API checks and is not collector overhead.

See [activation-results.json](activation-results.json) for the sanitized results.
Private backups, exact incident IDs, full snapshots and diagnostic evidence remain
under `.runtime/l3-activation/`. The earlier authentication-blocker report is kept
as history. Stop condition is met: dashboard verified, one collection performed,
post-activation validation completed, and no additional cycle scheduled.
