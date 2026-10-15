# Proposed observe-only activation — separate approval required

This is a concrete plan, **not an executed deployment**. The requested approval is
limited to the dashboard assurance module and one read-only collection. It does not
include production alert rules, receiver changes, watcher startup, scheduled collection,
E2 activation, metrics listener repairs or remediation.

1. Review `test-results.json`, `policy-validation.json`, `monitoring-and-kpm.md` and
   the collector request/memory measurements in `observe-validation.json`.
2. Confirm the existing private runtime configuration still binds the active scenario
   to Fog-RAN NS `2c128db3-e27e-4dbe-8854-17751ba17680` and persistent Open5GS NS
   `1b1cb300-b571-43f2-a567-6540895d7d0f`. Back up an existing local assurance journal
   using SQLite's backup API if one exists. Never replace `active-ran.yaml`.
3. Load the reviewed source into **ran-selector.service only**, using its existing
   validated service configuration. This needs a dashboard restart/reload after
   approval; no infrastructure, OSM, RAN, fog or core restart is included. Do not
   modify or start `layer3-watcher.service`. Confirm `/assurance.js` and `/api/assurance`
   are served, and LIVE_AUTO=false / executor=DISABLED are returned.
4. Discover the current monitoring Alertmanager Service IP from Kubernetes and confirm
   its namespace/name. The reviewed endpoint is recorded in `investigation.json`; do
   not assume a ClusterIP survives future resource changes. The current Prometheus
   and Alertmanager read APIs accepted unauthenticated requests over the local/private
   paths. Review access controls before exposing the dashboard; optional bearer access
   must use verified HTTPS and an explicit origin binding.
5. Run exactly one collection from the repository root, replacing the placeholder
   with the reviewed current endpoint:

   ```sh
   PYTHONPATH=scripts python3 -m assurance.cli --observe-once \
     --alertmanager-url 'http://REVIEWED-ALERTMANAGER-IP:9093'
   ```

   This makes bounded read-only external requests and writes only the local
   `.runtime/assurance.sqlite` journal. It enforces OBSERVE_ONLY. It cannot call a
   lifecycle executor. Operator mutations remain disabled unless a separately
   configured operator token is present. No webhook route/token is needed for this
   collection, and no Alertmanager route changes are requested.
6. Validate Layer 3: four target alarms, pipeline DEGRADED, CU/DU/fog WORKLOAD_READY,
   13 core pods WORKLOAD_READY, separate OSM READY/IDLE, CPU PSI with source freshness,
   and protocol/session KPIs UNAVAILABLE when not collected. After 120s, aged data
   must be labelled STALE. Confirm pending approvals never expose an execute operation.
7. Set emergency stop through the authenticated local operator endpoint if operational
   write access is explicitly approved/configured. Verify its durable state and
   production executor denial. Bearer values must stay outside tracked files/logs.
8. Stop after the single collection and inspect evidence. A periodic collector needs
   another review/approval. Proposed cadence is **60 seconds**, one collector process
   at a time, with at most eight monitoring HTTP requests per batch (six expected),
   two Kubernetes reads and three OSM GETs for this state. Fresh-process OSM token
   authentication may add one POST to the token endpoint. This is authentication,
   not an NS lifecycle operation. A 60s cadence fits the existing 90s maximum gap and
   120s freshness window. Never run one collector per browser.

The UI refreshes only the local journal every 30 seconds while its panel is open.
Source freshness is measured from `timestamp(selector)` values, not expression
execution time. The inspection-only variant is available without a journal write:

```sh
PYTHONPATH=scripts python3 -m assurance.cli --inspect-once \
  --alertmanager-url 'http://REVIEWED-ALERTMANAGER-IP:9093'
```

Rollback of this deployment is local: stop any approved future collector, restore the
previous dashboard source/environment and reload only the dashboard under approval.
Preserve the audit journal and deployment state. No NS terminate/reinstantiate, Helm
changes, CPU quotas, traffic changes or network alterations are part of rollback.
Metrics repair, E2/KPM enablement and any live alert activation need independent plans
and permissions. Eight supported policies describe verified observation contracts;
they are not approved production alerts or baselined service guarantees.
