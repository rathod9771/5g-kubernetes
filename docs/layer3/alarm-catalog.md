# Alarm catalog and investigation runbooks

Every entry is a local candidate, not a certified telecom alarm definition. No entry executes production remediation. Severity floors, persistence, required evidence and plans are configurable in `config/assurance-policies.json`.

## KubernetesNodeNotReady

- Domain / severity: infrastructure / CRITICAL.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## KubernetesMemoryPressure

- Domain / severity: infrastructure / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## KubernetesDiskPressure

- Domain / severity: infrastructure / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## HostCPUContentionHigh

- Domain / severity: infrastructure / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: host_pressure, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## HostCPUFullPressureHigh

- Domain / severity: infrastructure / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: host_pressure, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## PodCrashLoop

- Domain / severity: infrastructure / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## ContainerOOMKilled

- Domain / severity: infrastructure / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## ContainerRestartsHigh

- Domain / severity: infrastructure / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## PersistentVolumeDegraded

- Domain / severity: infrastructure / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## RANPodUnavailable

- Domain / severity: ran / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## DUUnavailable

- Domain / severity: ran / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: pod_ready, f1_association. Conditions: {"pod_ready": false, "f1_association": false}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: propose_du_recovery; class C; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy, pod_ready, f1_association, ngap_association, f1_setup, ng_setup.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## CUUnavailable

- Domain / severity: ran / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: pod_ready, ngap_association, f1_association. Conditions: {"pod_ready": false, "ngap_association": false, "f1_association": false}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: propose_cu_recovery; class C; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy, pod_ready, f1_association, ngap_association, f1_setup, ng_setup.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## F1AssociationLost

- Domain / severity: ran / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: f1_association, pod_ready. Conditions: {"f1_association": false}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: investigate_f1; class C; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy, pod_ready, f1_association, ngap_association, f1_setup, ng_setup.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## NGAPAssociationLost

- Domain / severity: ran / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: ngap_association, core_healthy. Conditions: {"ngap_association": false}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: investigate_ngap; class C; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy, pod_ready, f1_association, ngap_association, f1_setup, ng_setup.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## RANConfigurationFailure

- Domain / severity: ran / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## RANCPUContention

- Domain / severity: ran / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: host_pressure, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## RANProtocolErrorRateHigh

- Domain / severity: ran / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## RANPerformanceDegraded

- Domain / severity: ran / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: RAN control or radio service potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## AMFUnavailable

- Domain / severity: core / CRITICAL.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## SMFUnavailable

- Domain / severity: core / CRITICAL.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## UPFUnavailable

- Domain / severity: core / CRITICAL.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## NRFDiscoveryFailure

- Domain / severity: core / CRITICAL.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## SBIRequestFailureRateHigh

- Domain / severity: core / CRITICAL.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## UERegistrationFailureRateHigh

- Domain / severity: core / CRITICAL.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## PDUSessionFailureRateHigh

- Domain / severity: core / CRITICAL.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## PFCPAssociationLost

- Domain / severity: core / CRITICAL.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## UPFUserPlaneDegraded

- Domain / severity: core / CRITICAL.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Persistent core potentially affected; critical escalation.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## FogApplicationUnavailable

- Domain / severity: fog / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: pod_ready, ownership_verified. Conditions: {"pod_ready": false}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Edge application SLS potentially affected.
- Recommendation: propose_fog_recovery; class C; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## FogProcessingLatencyHigh

- Domain / severity: fog / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Edge application SLS potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## FogProcessingErrorRateHigh

- Domain / severity: fog / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Edge application SLS potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## FogDNSDiscoveryStale

- Domain / severity: fog / MAJOR.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Edge application SLS potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## FogCPUThrottlingHigh

- Domain / severity: fog / MAJOR.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Edge application SLS potentially affected.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## OSMLifecycleOperationFailed

- Domain / severity: orchestration / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: operation_failed, osm_trustworthy. Conditions: {"operation_failed": true}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## OSMLifecycleOperationStalled

- Domain / severity: orchestration / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## OSMServiceDegraded

- Domain / severity: orchestration / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## OSMComponentUnavailable

- Domain / severity: orchestration / MAJOR.
- Capability: PARTIALLY SUPPORTED. Read-only API or SCTP/log evidence exists; no validated Prometheus rule.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 60s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## PrometheusTargetDown

- Domain / severity: observability / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## PrometheusScrapeFailed

- Domain / severity: observability / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## MetricsMissing

- Domain / severity: observability / WARNING.
- Capability: SUPPORTED. Metric family observed; proposed rule unapplied. Baseline, scope and duplicate-rule review required.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## MonitoringPipelineDegraded

- Domain / severity: observability / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## AlertmanagerUnavailable

- Domain / severity: observability / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## InsufficientClusterCapacity

- Domain / severity: governance / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## NetworkCongestion

- Domain / severity: governance / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: metric_present, service_degraded. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.

## EmergencyServiceProtection

- Domain / severity: governance / WARNING.
- Capability: UNAVAILABLE. Required current metric, semantics or end-to-end instrumentation not verified; no rule generated.
- Required observations: protected_state. Conditions: {}.
- Persistence: 0s; clear persistence: 60s; freshness: 120s; gap reset: 90s.
- Impact: Investigate scope; service impact not yet proven.
- Recommendation: observe_notify; class A; automatic eligibility false.
- Verify: monitoring_fresh, core_healthy.
- Cooldown: 600s; automatic retries: 0.
- Rollback / escalation: Preserve current deployment; operator reconciliation required. Record operator triage, preserve protected NS and escalate when evidence is missing.
