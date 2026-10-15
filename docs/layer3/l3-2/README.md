# Phase L3-2 — live observability and service assurance

Read-only investigation and offline source preparation are complete. No observe-only
service deployment, watcher start, production alert enabling, Alertmanager route
change, Kubernetes resource change, restart or remediation was performed.

The [monitoring/KPM investigation](monitoring-and-kpm.md) establishes all four scrape
root causes and corrects the earlier KPM interpretation. The full **44-policy** review
is in [policy-validation.json](policy-validation.json): **8 SUPPORTED, 27 PARTIALLY_SUPPORTED,
9 UNAVAILABLE**. The narrower supported count reflects label/type/semantic review;
metadata-only declarations are distinguished from currently emitted series. Twelve
example expressions parse and execute successfully through the live Prometheus query
API. They remain unapplied, with thresholds pending operational baseline approval.

The implemented collector uses bounded GET-only transport, verified TLS for optional
origin-bound bearer credentials, no redirects, response/series/request limits, cached
identical requests, Prometheus source-sample freshness, active-target-only discovery,
Alertmanager group identities/status, and NS-owned CU/DU/fog selection. It preserves
missing/stale state and reduces absent corroboration to observation/escalation.
Initial healthy/resolved observations no longer appear as active alarms. Native webhook
redelivery is idempotent; condition onset is never silently replaced with reception
freshness. The one-shot collector supplies explicit observation timestamps from fresh
source reads and needs no production Alertmanager webhook route.

Dashboard source now displays CPU PSI, monitoring DEGRADED and individual failed
scrapes, alongside alarms/incidents, CU/DU/core/fog workloads, OSM, pending approvals,
maintenance, emergency stop and provenance. The [offline snapshot replay](dashboard-replay.json)
produced exactly four active target alarms and valid audit, using a temporary journal
and original captured timestamps; aged evidence correctly appeared STALE. Browser
DOM tests validate the renderer and window behavior. The running dashboard process
was not restarted; source validation is not a claim of live UI activation.

Safety remains structural: no assurance execute/lifecycle endpoint; LIVE_AUTO rejected;
ProductionExecutor always raises; simulator owns a temporary sandbox journal and has no
production callback. Existing manually operated dashboard deployment APIs are separate;
this is a proof about Layer 3's call paths, not a claim that every API in the repository
is incapable of lifecycle work. The collector's measured OSM transport trapped every
non-GET operation except token authentication. Emergency stop persists and is checked
at action validation, even though production execution is already disabled.

## Performance evidence and limits

The successful [one-shot capture](observe-validation.json) took **2.548s elapsed**,
**0.802s user CPU + 0.026s system CPU** in the collector. Resident memory grew from
**24,180 to 30,460 KiB** (about 23.6 to 29.7 MiB, +6.1 MiB). The rusage peak of
598,236 KiB was already present before collection (inherited historical peak) and is
not the collector's measured resident footprint. Kubernetes CLI child CPU and server
processing CPU are not included in the collector's self CPU; no fleet-wide load claim
is made. No continuous collector was deployed, so added steady-state query frequency
is zero.

Per successful batch: **six monitoring HTTP GETs** (four Prometheus, two Alertmanager),
**two Kubernetes reads**, **three authenticated OSM GETs**, **one token-auth POST** in
a fresh process. Two immediate repeated HTTP requests caused **zero extra requests**.
Each measured monitoring GET took about 1.4–11.5ms; failure count was zero after fixes.
The 29-event local replay took 9.885s elapsed but only 0.187s collector CPU, dominated
by full-sync journal/disk waits. It ran offline, not in the production journal.

The proposed 60s cadence would consume about 0.35% of four-core CPU capacity based on
this collector's measured self CPU alone; this is an extrapolation, not a deployed
measurement, and excludes child/server CPU and journal ingestion. Keep one instance,
measure after the first approved run, and evaluate disk waits before scheduling it.
The 30s dashboard refresh reads only SQLite and creates no monitoring API fan-out.

Unsupported current measurements include CPU PSI full, current E2/KPM, RAN BLER sample
series, matched PDU failures, SBI/NRF/PFCP failure/association state, measured user-plane
loss/latency and fog processing/error histograms. Present host/container counters alone
do not prove radio or application degradation. Emergency IMS/priority/QoS treatment is
unverified and never fabricated. Fog server source currently exports request/processed/DNS
counters, not a processing latency histogram; client benchmark capability is separate.

See [test-results.json](test-results.json) for final test counts/evidence and the
[exact activation plan](activation.md) for the bounded deployment approval request.

## Final regression evidence

**144 offline tests passed**, including all 125 prior foundation/regression tests
and 19 new live-adapter/safety regressions. All 16 simulations passed with production,
network and subprocess call traps. Python compilation, JavaScript syntax and diff
whitespace checks passed. [Files changed](files-changed.json) are local only.
