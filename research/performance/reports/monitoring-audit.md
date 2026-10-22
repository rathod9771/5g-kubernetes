# Monitoring audit — 22 October 2026

## Observed HP environment

Namespace `id-1b4ff6edc2bb` was read using the supplied
`/approved/private/kubeconfig.yaml`, without displaying its contents. CU, DU, fog
and13 Open5GS pods were Ready in the inspected inventory. No UE pod was found
by the inspected names/images/labels; this is not proof no external UE exists.
Kubernetes1.29.15 and OSM19 are supplied/configured environment versions, not
newly certified by this audit. Current RAN image specs are OAI CU2026.w13,
DU2026.w25. Kubernetes imageID values are runtime identities and are not
misrepresented as independently registry-verified OCI manifest digests.

Prometheus: `http://endpoint-b0dd4ab974a5.invalid:9090`. 25 targets,21 up,4 down:
controller-manager,etcd,kube-proxy,scheduler. These are monitoring endpoint
failures, not confirmed control-plane outages. No monitoring settings changed.
The /metrics/cadvisor target scrapes every10s; RAN exporter15s; latency probe10s.
Other kubelet endpoints use30s: generic job name alone is insufficient for cadence.
Scrape-target rows with ambiguous job-only cadence retain null; full source paths
were checked separately. Prometheus was read through bounded GET calls only.

Metrics-server's v1beta1.metrics.k8s.io APIService was not registered and its pod
metrics API failed. Resource requests/limits/status remain available via the
Kubernetes API; actual usage is measured through cAdvisor/Prometheus, not kubectl top.

29 provisioned Grafana dashboard ConfigMaps were found (Kubernetes, nodes,
Prometheus, Grafana, etc). This verifies provisioning metadata, not a login/session
or the complete set of dashboards created privately through Grafana's UI.
No radio dashboard was established by this ConfigMap inventory.

## RAN exporter: confirmed defects and bounded evidence

Live `ran-exporter-script` SHA256:
`6280f5cd0f21f7293ba98cd4d08c1ba4568952e2269a9175a171b33d7a15063a`.
Its embedded selector is app=cloud-ran-oai-gnb while the live DU is app=fran-oai-du.
It defines UL SNR but contains no `g_snr_ul.labels` setter. Its endpoint is
scraped, but `oai_gnb_exporter_lines_parsed_total` is0 and radio sample families
are absent from active query results. Exporter HTTP health is not radio collection.
The repository's old discovery call sits outside the stream try/except; the
candidate removes that failure path. No unsupported claim that a particular
live thread was inspected or proven dead is made.

Bounded timestamped log reads: CU169 lines/18756bytes, DU200 lines/10276bytes.
No matching dlsch_rounds,ulsch_rounds,average RSRP or MAC TX statistics were found.
These are bounded samples, not exhaustive lifetime logs. No UE radio format is
verified on these exact images. No raw logs or IMSI/K/OPc values are published.

Candidate source changes only: architecture-aware namespaced image/role discovery,
separate CU/DU/monolithic containers and pod UID labels, bounded REST log polling,
discovery/log failure recovery, per-field timestamps, duplicate suppression,
pod replacement cleanup, TTL expiry even during API failure, UL/DL parsing
independent of SNR/BLER field order, decimal RSRP, invalid/composite-SNR rejection.
Only explicit source fields are emitted. HARQ/MAC totals remain source-snapshot
gauges that can reset; do not apply process-counter semantics without UID/reset
handling. Existing OAI metric names and aggregate lines-parsed counter retained.
New collection-health/freshness/saturation metrics distinguish data health.

The candidate uses stdlib service-account TLS REST; no runtime pip install,
no SDK dependency, no watch stream, no pod exec. Role is limited to namespaced
get/list pods and get pods/log, with no watch/write verbs. This RBAC/source was
not applied. Both old and new code remain subject to approval before activation.

A separate srsRAN parser accepts only already-forwarded documented scheduler JSON:
PUSCH SNR dB and native MAC bitrates kbps. It never interprets srsRAN logs with
OAI regexes, converts CQI to DL SNR, or converts PUSCH RSRP dB into dBm. No JSON
metrics or remote-control interface was enabled. Pinned25.04 parser/transport
compatibility remains UNVERIFIED; current upstream JSON documentation is not
proof the pinned binary emits it. Reference:
https://docs.srsran.com/projects/project/en/latest/user_manuals/source/outputs.html

## Core, mesh and existing Layer2/3

Active native families include fivegs_amffunction_rm_reginitreq/reginitsucc/
reginitfail, SMF pdusessioncreationreq/creationsucc and UPF session/packet counters.
Their presence is verified; this task did not convert aggregate counters into
per-UE registration/PDU durations or collect their values as benchmark outcomes.

Istio sidecars are present, but no active istio_* request series or SBI application
latency histogram was found. Existing network_tcp_connect_* metrics represent
TCP connection establishment, not HTTP transaction latency, packet jitter,
UE-to-UPF latency, or user-plane throughput. A5min TCP mean was measured; the
failure-counter series was absent, so no missing->zero or100% success inference.

Existing /api/layer2-metrics aggregates BLER/HARQ/MAC/native Core metrics and
labels TCP-connect time as control_plane_latency_ms. Source availability and
last-success timestamps must accompany those dashboard numbers. It is not an
independent throughput/degradation experiment. Source: ran-selector/backend.py
layer2_metrics; monitoring/latency-probe/latency_probe.py.

Layer3 provides alarms/incidents/proposals and bounded read-only collectors.
The legacy watcher failover implementation is denied in this baseline. No watcher
or remediation was enabled. Historical alarm timestamps alone do not establish
fault-onset, detection delay, recovery time or false-positive rate.

## OSM and CI

The existing ReadOnlyOSM adapter was reused. Production snapshot loading rejects
default credentials (`ConfigError`, DEFAULT_CREDENTIALS_REJECTED) before NBI
access. No credential contents are exposed and no guard was bypassed. Current
NS READY/running/IDLE and operation duration are not independently verified
through NBI in this work. Ready Kubernetes pods are a different observation.
Provide a separately approved private, schema-compatible configuration with a
non-default scoped read-only identity/CA; do not alter production or P4 config.

GitHub public API: completed successful run39724140683 at exact baseline
 e0602ec8067ac20452064fc728f77996f30a691a. The named regression/Helm/package-validation
step lasted150seconds, with1s timestamp resolution. This is one historical CI
step, not Docker build, catalog publication, total release verification or RAN
deployment duration. No workflow was dispatched or rerun.
