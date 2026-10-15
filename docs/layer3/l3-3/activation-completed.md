# L3-3 approved single live collection — completed

Collection timestamp: 2026-10-14T14:30:06.336688+05:30. Exactly one corrected observe-only collection ran; no retry, deployment, service restart, continuous collector, workload/configuration mutation, commit or push occurred.

Kubernetes pod/node/Warning-event reads succeeded, with zero adapter failures. The journal audit count rose from 1 (L3-2) to 2; the added record is this one L3-3 collection. Audit chain verification passed. The journal backup is `.runtime/l3-3-backup/assurance.sqlite`; detailed owner-only pre/post evidence is in `.runtime/l3-3-preflight`, and the exclusive execution gate/results are in `.runtime/l3-3-once`.

## Collected telemetry

| Source | Fresh collection state | Result |
|---|---|---|
| Kubernetes workloads | LIVE | 22 observed pods, untruncated; protected CU/DU/fog each WORKLOAD_READY and all 13 Open5GS pods WORKLOAD_READY |
| Node | LIVE | Ready=True; MemoryPressure, DiskPressure, PIDPressure, NetworkUnavailable=False |
| Warning-event API | LIVE | 10 historical HPA FailedGetResourceMetric records; each record STALE by its event timestamp |
| Prometheus / Alertmanager | LIVE | Read APIs accessible; monitoring pipeline DEGRADED because four known target scrapes are down |
| CPU PSI some, 5 minutes | LIVE | 11.166% waiting-pressure rate; this is not CPU utilization or a service-degradation measurement |
| OSM protected services | LIVE | Both READY/running/IDLE; existing RAN instantiate operation COMPLETED |
| KPM | UNAVAILABLE | No live KPM adapter; E2 SCTP absent in separate pre/post checks |
| Protocol setup, UE/PDU/user plane, latency | UNAVAILABLE | No unsupported end-to-end measurements inferred from Ready pods |

Separate pre/post SCTP evidence confirms F1-C for CU/DU and NGAP for CU. These reads do not prove F1/NG setup, UE registration or end-to-end traffic. Warning events describe unavailable HPA resource metrics, not failed Open5GS workloads; no new service-degradation alarm was synthesized from these old records.

## Alarm and incident changes

The four existing PrometheusTargetDown alarms became UPDATED, WARNING, LIVE and fresh at post-check. Their underlying connection-refused monitoring gaps remain unchanged:

| Target | Endpoint | Existing incident ID |
|---|---|---|
| kube-controller-manager | 172.30.18.32:10257 | e4c3f058-d636-4f5e-95d0-43595f828085 |
| kube-etcd | 172.30.18.32:2381 | dbf15a8d-7814-4d9b-ae42-f8ba514cab86 |
| kube-proxy | 172.30.18.32:10249 | 31e67ae4-b9fb-4862-a71d-86095bb8267b |
| kube-scheduler | 172.30.18.32:10259 | 5950eace-236c-4035-ad17-9901742e29fb |

All four incident IDs, WARNING severities and ESCALATED states were retained, with no new timeline transitions or external escalation/remediation. Total alarm rows rose 23→27 because node/CU/DU/fog healthy observations were initially recorded as CLEARED/resolved; active alarms remain exactly four. Incident count stayed four, action count stayed four, new remediation actions zero, pending disruptive approvals zero. Policy classifications remain 8 SUPPORTED / 27 PARTIALLY_SUPPORTED / 9 UNAVAILABLE.

## Measured resource consumption

| Measurement | Result |
|---|---|
| Collection wall time | 12.439608 seconds |
| Collector and waited-child CPU | 1.964619 CPU-seconds; supervisor CPU not included |
| Sampled aggregate collector process-tree RSS | 77045760 bytes (73.48 MiB) |
| Linux child rusage maximum RSS | 50068 KiB (48.89 MiB); separate accounting from sampled tree sum |
| Kubernetes subprocess elapsed total | 0.650385 seconds |
| Kubernetes sampled child-tree peak RSS | 49.56 MiB |
| Kubernetes combined output | 470246 bytes total across three commands |
| Kubernetes list attempts | 3, all successful; no command retries |
| Monitoring HTTP requests | 6, zero failures, zero cache hits; latency 2.50–7.87 ms |
| OSM reads | 3 successful object reads (two NSs, one prior completed operation); wire/auth request counts not independently instrumented |

Kubectl may make discovery GETs beyond the three list commands, so list attempts are not claimed as exact wire-level Kubernetes API request counts. Pre/post verification requests are separate from the collection.

L3-2 baseline: 12.16 s, approximately 0.81 CPU s, 32.1 MiB parent RSS with failed kubectl startup. L3-3 wall time rose about 0.28 s and CPU about 1.15 s while adding successful Kubernetes execution. The new aggregate tree memory measurement is broader than the old parent-only RSS; they are not direct memory-overhead equivalents. No sustained monitoring load is enabled. All reviewed bounds remained in force: 90 s collection wall, 30 s inherited CPU, sampled RSS 512 MiB complete tree / 256 MiB Kubernetes child tree, 10 s subprocess / 5 s API request deadlines, 2 MiB output limits and three allowlisted list attempts. RSS watchdogs allow transient overshoot; no virtual-address cap was imposed or relaxed.

## Dashboard, freshness and protected-service evidence

Seven reviewed dashboard/selector endpoints returned HTTP 200. The captured fresh dashboard reported monitoring_confidence=LIVE, workload readiness LIVE, four current target warnings and four existing incidents. Offline rendering of the actual snapshot confirmed those labels and disabled automation. Warning-event details and explicit KPM provenance are exposed through the existing API; the unchanged UI has no dedicated Warning-event or KPM detail view.

The final cached dashboard read at age 158.91 seconds correctly returned STALE. Source provenance/timestamps remain evidence of the one successful collection; they are not ongoing live polling. No additional collection was performed to refresh them.

Both protected NSs remained READY/running/IDLE. All 16 protected pod UIDs, specs, image/container IDs and restart counts matched preflight; workload namespace Service UIDs/specs also matched. OSM operation history and active deployment state hash were unchanged. No pending or unexpected OSM operation appeared. Reviewed source/config hashes remained unchanged.

| Protected pod | Container restart counts |
|---|---|
| fran-oai-cu-6484757ccb-nfhr2 | cu=0 |
| fran-oai-du-55dbf6d7b4-fz2f8 | du=0 |
| fran-oai-fog-7bcf65bd4c-78q66 | fog=0 |
| open5gs-0004215456-amf-85f6df5688-7pt9k | istio-proxy=2, open5gs-0004215456-amf=3 |
| open5gs-0004215456-ausf-f55b9fdf5-8j2xg | istio-proxy=3, open5gs-0004215456-ausf=3 |
| open5gs-0004215456-bsf-66bc5b5b96-sqsrc | istio-proxy=3, open5gs-0004215456-bsf=2 |
| open5gs-0004215456-nrf-7d88c88664-wtgbp | istio-proxy=3, open5gs-0004215456-nrf=5 |
| open5gs-0004215456-nssf-8694d5d7f5-skk8q | istio-proxy=3, open5gs-0004215456-nssf=3 |
| open5gs-0004215456-pcf-744488d567-rq7sj | istio-proxy=3, open5gs-0004215456-pcf=13 |
| open5gs-0004215456-populate-6bf8d6dc6c-5hdv8 | populate=2 |
| open5gs-0004215456-smf-5c4b4cc4dd-tf6cv | istio-proxy=2, open5gs-0004215456-smf=6 |
| open5gs-0004215456-udm-777d45cd8c-dh7tt | istio-proxy=3, open5gs-0004215456-udm=3 |
| open5gs-0004215456-udr-67897987c9-2g69s | istio-proxy=3, open5gs-0004215456-udr=15 |
| open5gs-0004215456-upf-bdc7f958c-j7mjz | open5gs-0004215456-upf=2 |
| open5gs-0004215456-webui-5ff7d9cf5c-v7lj8 | open5gs-0004215456-webui=3 |
| open5gs-mongo-custom-0 | mongo=0 |

These nonzero Open5GS restart counts predate this collection; none increased across pre/post checks. CU/DU/fog each have zero restarts.

Watcher remained inactive, LIVE_AUTO=false, production_executor=DISABLED, mode OBSERVE_ONLY. Dashboard PID 578791 remained unchanged and NRestarts=0. Protected NS guards and deliberately disabled production executor were unchanged. No mutating Kubernetes or OSM lifecycle command was invoked.

Stop condition reached: one approved collection and post-verification complete. Remaining gaps are the four control-plane scrape endpoints, historical HPA resource-metric warnings, and uncollected KPM/protocol/session/latency measurements. No repair, retry, watcher or continuous collection is authorized by this completed action.
