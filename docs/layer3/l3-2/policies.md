# All 44 policy review results

Complete metric/type/label/threshold/window/evidence details are in [policy-validation.json](policy-validation.json). Supported source contracts are not approval to enable a production rule.

| Alarm | Status | Source | Severity | Action class |
|---|---|---|---|---|
| KubernetesNodeNotReady | SUPPORTED | Prometheus candidate | CRITICAL | A (production disabled) |
| KubernetesMemoryPressure | SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| KubernetesDiskPressure | SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| HostCPUContentionHigh | SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| HostCPUFullPressureHigh | UNAVAILABLE | Optional/unavailable metric adapter | WARNING | A (production disabled) |
| PodCrashLoop | PARTIALLY_SUPPORTED | Kubernetes container waiting.reason CrashLoopBackOff; live Prometheus series absent | WARNING | A (production disabled) |
| ContainerOOMKilled | PARTIALLY_SUPPORTED | Prometheus candidate | MAJOR | A (production disabled) |
| ContainerRestartsHigh | SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| PersistentVolumeDegraded | PARTIALLY_SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| RANPodUnavailable | PARTIALLY_SUPPORTED | Kubernetes Ready condition and validated role/NS ownership | MAJOR | A (production disabled) |
| DUUnavailable | PARTIALLY_SUPPORTED | Kubernetes DU Ready; kernel F1 SCTP optional evidence | MAJOR | C (production disabled) |
| CUUnavailable | PARTIALLY_SUPPORTED | Kubernetes CU Ready; F1 and NGAP kernel SCTP optional evidence | MAJOR | C (production disabled) |
| F1AssociationLost | PARTIALLY_SUPPORTED | Operator-collected CU/DU kernel SCTP tables; no scheduled protocol exporter | MAJOR | C (production disabled) |
| NGAPAssociationLost | PARTIALLY_SUPPORTED | Operator-collected CU kernel SCTP table; no scheduled protocol exporter | MAJOR | C (production disabled) |
| RANConfigurationFailure | PARTIALLY_SUPPORTED | Optional bounded timestamped OAI/srsRAN log parser; no live scheduler | MAJOR | A (production disabled) |
| RANCPUContention | PARTIALLY_SUPPORTED | Optional/unavailable metric adapter | MAJOR | A (production disabled) |
| RANProtocolErrorRateHigh | UNAVAILABLE | Optional/unavailable metric adapter | MAJOR | A (production disabled) |
| RANPerformanceDegraded | UNAVAILABLE | Optional/unavailable metric adapter | MAJOR | A (production disabled) |
| AMFUnavailable | PARTIALLY_SUPPORTED | Kubernetes AMF Ready; protocol availability not independently measured | CRITICAL | A (production disabled) |
| SMFUnavailable | PARTIALLY_SUPPORTED | Kubernetes SMF Ready; SBI/PFCP not independently measured | CRITICAL | A (production disabled) |
| UPFUnavailable | PARTIALLY_SUPPORTED | Kubernetes UPF Ready; measured user-plane test absent | CRITICAL | A (production disabled) |
| NRFDiscoveryFailure | UNAVAILABLE | Optional/unavailable metric adapter | CRITICAL | A (production disabled) |
| SBIRequestFailureRateHigh | UNAVAILABLE | Optional/unavailable metric adapter | CRITICAL | A (production disabled) |
| UERegistrationFailureRateHigh | PARTIALLY_SUPPORTED | Prometheus candidate | MAJOR | A (production disabled) |
| PDUSessionFailureRateHigh | PARTIALLY_SUPPORTED | Optional/unavailable metric adapter | CRITICAL | A (production disabled) |
| PFCPAssociationLost | UNAVAILABLE | Optional/unavailable metric adapter | CRITICAL | A (production disabled) |
| UPFUserPlaneDegraded | PARTIALLY_SUPPORTED | Optional/unavailable metric adapter | CRITICAL | A (production disabled) |
| FogApplicationUnavailable | PARTIALLY_SUPPORTED | Kubernetes fog Ready plus known NS ownership; application probe unconfigured | MAJOR | C (production disabled) |
| FogProcessingLatencyHigh | UNAVAILABLE | Optional/unavailable metric adapter | MAJOR | A (production disabled) |
| FogProcessingErrorRateHigh | UNAVAILABLE | Optional/unavailable metric adapter | MAJOR | A (production disabled) |
| FogDNSDiscoveryStale | SUPPORTED | Prometheus candidate | MAJOR | A (production disabled) |
| FogCPUThrottlingHigh | PARTIALLY_SUPPORTED | Optional/unavailable metric adapter | MAJOR | A (production disabled) |
| OSMLifecycleOperationFailed | PARTIALLY_SUPPORTED | Authenticated OSM operationState with NS/operation identity | MAJOR | A (production disabled) |
| OSMLifecycleOperationStalled | PARTIALLY_SUPPORTED | Authenticated OSM operation statusEnteredTime; 900s local candidate, not validated baseline | MAJOR | A (production disabled) |
| OSMServiceDegraded | PARTIALLY_SUPPORTED | OSM NS state plus Kubernetes workloads; protocol state independent | MAJOR | A (production disabled) |
| OSMComponentUnavailable | PARTIALLY_SUPPORTED | Kubernetes osm namespace readiness; new collector does not read this namespace | MAJOR | A (production disabled) |
| PrometheusTargetDown | SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| PrometheusScrapeFailed | PARTIALLY_SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| MetricsMissing | SUPPORTED | Prometheus candidate | WARNING | A (production disabled) |
| MonitoringPipelineDegraded | PARTIALLY_SUPPORTED | Read-only Prometheus API, timestamp(up) and active targets | WARNING | A (production disabled) |
| AlertmanagerUnavailable | PARTIALLY_SUPPORTED | Read-only Alertmanager API status/failure; metric rule unimplemented | WARNING | A (production disabled) |
| InsufficientClusterCapacity | PARTIALLY_SUPPORTED | Kubernetes allocatable/Ready; requests/scheduling correlation unimplemented | WARNING | A (production disabled) |
| NetworkCongestion | UNAVAILABLE | Optional/unavailable metric adapter | WARNING | A (production disabled) |
| EmergencyServiceProtection | PARTIALLY_SUPPORTED | Operational protected NS identity; no actual IMS/QoS emergency implementation | WARNING | A (production disabled) |

All thresholds require service baselines. OOMKilled label variant has not been observed; PVC Lost/Pending is not Longhorn replica health; empty successful scrapes can be legitimate; registration failure ratio needs operational baseline validation. These four example policies remain PARTIALLY_SUPPORTED.

The registration severity floor was reduced to MAJOR to match its candidate rule, and OOM was raised to MAJOR. Critical escalation requires explicit service impact or protected-service evidence. No live rule was changed.
