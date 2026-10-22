# Architecture-aware RAN exporter — candidate, not deployed

The production ConfigMap is unchanged. Source fixes in the performance worktree
require separate review/approval before any ConfigMap, Deployment or RBAC update.
See [audit](../../research/performance/reports/monitoring-audit.md),
[tests/results](../../research/performance/reports/validation.md) and
[activation boundaries](../../research/performance/experiments/collection.md).

## Discovery and resource policy

Explicit TARGET_NAMESPACE is mandatory. Default POD_LABEL_SELECTOR is empty:
list at most128 namespace pods, then select only recognized OAI/srsRAN images and
CU/DU/gnb roles. An optional selector narrows discovery. This handles F-RAN DU
app=fran-oai-du instead of hard-coding app=cloud-ran-oai-gnb. Pod UID and container
identity prevent replacement/RNTI collisions. No discovery across namespaces.

The candidate is standard-library Python: no runtime pip install, Kubernetes SDK,
prometheus_client package, pod exec or remote-control write. It uses service-account
TLS REST GETs, a rotating token read and CA validation; credentials are not logged.
Role permissions are namespaced pods get/list and pods/log get, no watch/mutations.
The existing CPU/memory resource budgets are retained in the candidate manifest.

15s normal collection,30–60s error backoff,6s API-body budget with2s socket timeout,
8 source containers,200 log lines/64KiB,2048 measurement series,512 remembered line
hashes/source,120s per-field TTL. Source-clock tolerance5s; samples further in the
future are rejected. Failure backoff is cancellable and successful discovery
reconciles pod replacement. Saturated windows mark collection incomplete. Only
one collector worker runs; exposition uses at most4 HTTP request threads.

## Data and health

OAI explicit text fields: decimal RSRP, UL/DL SNR and BLER independent of field
order, HARQ rounds/errors and MAC TX/RX totals. Composite SNR strings are not
silently truncated; malformed/out-of-range BLER fields are not emitted. Source
reported cumulative totals are gauge snapshots tied to pod UID, not exporter
process counters. Missing fields never become numeric zero. Real formats/units
must be verified for the selected image; synthetic tests are not lab acceptance.

srsRAN uses a separate documented scheduler-JSON parser, only if that JSON is
already forwarded into pod logs. PUSCH SNR and native MAC bitrate retain dB/kbps
units. No CQI->DL SNR or RSRP dB->dBm inference. The current pinned25.04 transport/
format is not certified by newer upstream documentation; no metrics flags or
websocket remote-control interface is enabled by this code.

Metrics include namespace/scenario/implementation/component/pod/pod_uid/container;
radio samples additionally carry rnti/cell. Per-metric timestamps are retained.
UE series expire even if the API is unavailable or another UE remains active.
Log replay does not refresh timestamps. At most5s positive source-clock skew is
accepted, including submicrosecond timestamp rounding at serialization boundaries.

- ran_exporter_discovery_success / discovery_failures_total
- ran_exporter_source_collection_success
- ran_exporter_source_radio_statistics_fresh
- ran_exporter_source_last_log_timestamp_seconds
- ran_exporter_source_last_radio_timestamp_seconds
- ran_exporter_source_log_window_saturated
- ran_exporter_source_lines_received_total / lines_parsed_total
- ran_exporter_measurement_timestamp_seconds{metric=...}
- oai_gnb_exporter_lines_parsed_total (legacy aggregate retained)

GET /healthz indicates worker liveness only; it does not assert fresh radio data.
GET /metrics exports the above health/data distinction. A running exporter with
no active UE/log statistics correctly has no per-UE radio samples.

No apply/install commands were run. Before any approved exporter-only activation,
preserve exact prior ConfigMap/Deployment/RBAC for rollback, check namespace scope,
confirm real log formats/clock behavior and validate Prometheus source freshness.
Do not restart a protected CU/DU/Core to force statistics or run UE tests without
separate approval.
