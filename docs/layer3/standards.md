# Standards mapping and conformance boundaries

References below pin a release/version so clause numbers are reproducible. They are
not assertions that the installed software implements every referenced feature.
A = function described by a standard; B = aligned local design decision;
C = laboratory simulation; D = unsupported or unverified installed capability.

| Reference and clauses | A: standardized subject | B: implemented alignment | C / D boundary |
|---|---|---|---|
| [TS 28.312 v18.3.0](https://www.etsi.org/deliver/etsi_ts/128300_128399/128312/18.03.00_60/ts_128312v180300p.pdf), 4.2, 4.3, 4.4 | Intent management, closed loops, distinction between rule/policy/intent | Configurable assurance rules, protected-service constraints and proposals | D: no standardized Intent IOC/service, translation service or intent-fulfilment API |
| [TS 28.532 v18.6.0](https://www.etsi.org/deliver/etsi_ts/128500_128599/128532/18.06.00_60/ts_128532v180600p.pdf), 11.1, 11.3, 11.4, 11.5 | Generic provisioning, performance file reporting, heartbeat and streaming services | Separate observation/decision/execution modules; explicit timestamps and unavailable observations | D: Flask endpoints are local APIs, not standardized MnS interfaces; alarm schema is local |
| [TS 28.535 v18.2.0](https://www.etsi.org/deliver/etsi_ts/128500_128599/128535/18.02.00_60/ts_128535v180200p.pdf), 4.2.4, 4.2.5, 4.2.6; 6.1.6, 6.1.8 | Assurance loop phases, governance/coordination, limiting actions and execution supervision | Incident timeline, approval gates, stop control, fail-closed validation and outcome journal | C: execution/verification only synthetic; D: no 5GC/NWDAF-assisted SLS integration |
| [TS 28.541 v18.8.0](https://www.etsi.org/deliver/etsi_ts/128500_128599/128541/18.08.00_60/ts_128541v180800p.pdf), 4.2; 4.3.1–4.3.5; 5.3.1–5.3.3 | RAN function/cell relationships and AMF/SMF/UPF resource models | Explicit CU/DU/core identities and dependency grouping | D: labels are not full NRM IOCs/DNs; cell-level NRM adapter not implemented |
| [TS 28.552 v18.7.0](https://www.etsi.org/deliver/etsi_ts/128500_128599/128552/18.07.00_60/ts_128552v180700p.pdf), 5.1.1.1–5.1.1.3, 5.1.3.1; 5.2.1, 5.2.2; 5.3.1 | RAN delay/PRB/throughput/F1-U loss, AMF registrations, SMF session measurements | Read-only PM availability audit, registration candidate ratio with minimum attempts | D: no PRB, RLC/MAC, loss, measured UE throughput or failure-pair semantic conformance evidence; PM names alone do not prove compliance |
| [TS 23.501 v18.8.0](https://www.etsi.org/deliver/etsi_ts/123500_123599/123501/18.08.00_60/ts_123501v180800p.pdf), 4.2, 5.7.2.1, 5.7.2.2, 5.16.4 | 5GS architecture, 5QI/ARP and emergency services | Persistent core protection; distinguish infrastructure criticality from emergency service | C: protected policy events only; D: no verified emergency IMS chain, authorized priority/QoS enforcement or emergency traffic |
| [TS 23.502 v18.8.0](https://www.etsi.org/deliver/etsi_ts/123500_123599/123502/18.08.00_60/ts_123502v180800p.pdf), 4.2.2.2.2, 4.3.2.2 | Registration and PDU-session establishment procedures | Separate transport, setup, UE-registration, PDU and measured user-plane states | D: no fabricated registrations or sessions; no UE continuity claim after fallback |
| [ETSI GS ZSM 002 v1.1.1](https://www.etsi.org/deliver/etsi_gs/ZSM/001_099/002/01.01.01_60/gs_zsm002v010101p.pdf), 6.1.2, 6.5.7, Annex C (informative) | Architectural building blocks, supporting policies and closed-loop composition | Modular observe/analyze/decide/validate/act/verify/record design | B: local architecture choice; no claim of implementing every ZSM management service |
| [ETSI GS ZSM 009-1 v1.1.1](https://www.etsi.org/deliver/etsi_gs/ZSM/001_099/00901/01.01.01_60/gs_ZSM00901v010101p.pdf), 7.2.2, 8.1, 8.2.5.4, 9.2.4 | Loop data flow, governance, pre-execution coordination and execution management | Reviewed policies, operator approval, lifecycle serialization, execution reservations and audit | C: reserved execution is sandbox-only; D: live coordination with all peer controllers not validated |

The normalized severity and lifecycle choices are telecom-inspired implementation
choices, not a claim of implementing a 3GPP fault-management interface. SCTP kernel
state is transport evidence; it does not prove F1 Setup, NG Setup, RRC, registration,
PDU completion or end-to-end data delivery. These distinctions are aligned with the
resource/architecture/procedure separation above; no protocol conformance claim is made.

The observed `fivegs_amffunction_rm_regemergreq` / `regemergsucc` families are not
proof of emergency registration success, emergency PDU sessions, IMS emergency
handling or policy enforcement. TS 23.501 5.16.4.5–5.16.4.9 and 5.7.2.1–5.7.2.2
define relevant network/QoS subjects; the local safeguard never calls them implemented.

Official stack references: [OSM v19 healing](https://osm.etsi.org/docs/test-user-guide/v19/how-to/nfv/healing-network-services/)
describes VDU/VM healing, not a validated recovery path for our protected KDU profile;
[OSM v19 FAQ](https://osm.etsi.org/docs/test-user-guide/v19/others/faq/) discusses
lifecycle/scaling/declarative capabilities. [Kubernetes observability](https://kubernetes.io/docs/concepts/cluster-administration/observability/),
[Prometheus alert rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/),
[Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/),
and [Rancher monitoring](https://ranchermanager.docs.rancher.com/v2.14/integrations-in-rancher/monitoring-and-alerting)
inform optional integrations. Documentation describes available products; installed
capabilities are established separately by the dated audit.
