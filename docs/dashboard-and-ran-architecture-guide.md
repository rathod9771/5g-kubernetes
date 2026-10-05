# 5G Orchestrator Dashboard and RAN Architecture Guide

## 1. What this platform does

Select a supported RAN architecture and software implementation. The dashboard sends the request to the backend, which validates the scenario and package. OSM performs lifecycle orchestration through its Northbound Interface (NBI); Kubernetes runs the containerized network functions (CNFs). Open5GS provides the 5G Core, and OAI or srsRAN provides the RAN software. Prometheus and Grafana provide telemetry. Layer 3 can request corrective orchestration according to configured and validated policy.

```text
User
  ↓
RAN Selector Dashboard
  ↓
Backend
  ↓
OSM NBI
  ↓
OSM
  ↓
Kubernetes
  ├── Open5GS Core
  ├── OAI / srsRAN
  ├── Istio
  └── Monitoring
```

## 2. 3GPP foundation

3GPP TS 38.401 defines NG-RAN architecture. A gNB may consist of gNB-CU and gNB-DU functions communicating over F1. TS 38.470 specifies F1 general aspects and principles. NGAP provides control-plane communication from the gNB/CU to the AMF over N2.

3GPP-defined functions and interfaces include gNB, gNB-CU, gNB-DU, F1 and NG. Centralized RAN, Cloud RAN, vC-RAN, H-CRAN, F-RAN and O-RAN are project/deployment architecture labels, not six separate 3GPP node definitions. H-CRAN and F-RAN are deployment/research concepts built using 3GPP functions. O-RAN builds on NG-RAN functional splits and adds O-RAN Alliance open-interface and RIC concepts.

Primary references: ETSI publication of 3GPP TS 38.401:
https://www.etsi.org/deliver/etsi_ts/138400_138499/138401/16.08.00_60/ts_138401v160800p.pdf

ETSI publication of 3GPP TS 38.470:
https://www.etsi.org/deliver/etsi_TS/138400_138499/138470/18.05.00_60/ts_138470v180500p.pdf

## 3. Scenario architecture

### C-RAN / Centralized RAN

CU/DU processing is centrally hosted on Kubernetes. DU connects to CU through F1; CU connects to Open5GS AMF through NGAP. RF can use simulation; SDR/USRP operation is a future workflow, not implied by the simulated demo. OAI and srsRAN are enabled.

```text
UE/RF
  ↓
DU
  │ F1
  ↓
CU
  │ NGAP
  ↓
Open5GS AMF/Core
```

### Cloud RAN

CU/DU run as cloud-native Kubernetes workloads. Cloud hosting and profile resource policy distinguish this deployment; its underlying CU/DU, F1 and NGAP concepts remain the same as C-RAN. OAI and srsRAN are enabled. Explicit memory requests support scheduling; the srsRAN profile intentionally omits hard memory limits following observed cgroup OOM behavior.

### vC-RAN

Virtualization/containerization, Kubernetes resource requests and an optional HPA profile distinguish vC-RAN. CU/DU remain stateful telecom functions with F1/NGAP associations. HPA is optional, conservative and disabled by default for srsRAN. Metrics API support is required for activation; unrestricted horizontal CU scaling is not claimed. OAI and srsRAN are enabled.

### O-RAN

The enabled implementation uses OAI CU/DU, F1, NGAP and RFSIM. OAI is RAN-level validated. Optional E2 integration and KPM/RC support are experimental/planned: E2 association and FlexRIC completion remain pending. Successful E2 association is not claimed. O-RAN + srsRAN is **Disabled / Future**.

### H-CRAN

Heterogeneous RAN combines centralized/cloud processing with heterogeneous macro/small-cell resources as a deployment concept. Both implementations are **Disabled / Future**. This release has no validated H-CRAN deployment implementation.

### F-RAN / Fog RAN

Fog RAN moves some compute/network processing closer to the edge to reduce latency and backhaul demand. It is **Disabled / Future** and not validated in this release. Its presence in the roadmap or historical packages does not make it deployable from this dashboard.

## 4. OAI and srsRAN selection

OAI and srsRAN are software implementations, not deployment architectures themselves. Selection combines architecture + implementation: for example vC-RAN + OAI, vC-RAN + srsRAN or O-RAN + OAI. Switching performs lifecycle replacement of the active RAN; it is not a hot swap or seamless handover guarantee.

## 5. Dashboard functionality

### 5G Core

Shows Open5GS CNFs, including AMF, SMF and UPF, and their readiness/state. Pod readiness is useful evidence but does not independently prove end-to-end connectivity.

### UE / PDU Session

Shows the simulated OAI UE, registration evidence, PDU session, tunnel address and radio evidence where available. Validate the currently selected scenario explicitly; UE/PDU success is not claimed for every architecture/implementation.

### Slice/NW

Shows network/slice information: PLMN identifies the mobile network; TAC identifies the tracking area; SST and SD describe the slice; DNN identifies the data network. Configured values alone do not prove subscriber/session acceptance.

### Istio

Shows service-mesh/traffic visibility where configured. This does not imply that telecom SCTP or RF paths are automatically mesh-managed.

### Verify Clean

Checks expected deployment state and stale/conflicting resources. This is a read-only verification control, not a RAN scenario or deployment action.

### Rancher

Provides Kubernetes cluster/workload management visibility. Its workspace panel can embed the application when browser security permits, or offer an external link.

### Prometheus

Collects and queries available metrics. Prometheus is distinct from the Kubernetes Resource Metrics API required by HPA.

### Layer 2

Live network intelligence shows BLER, throughput, latency, HARQ, MAC TX/RX, AMF registrations and CPU/memory/resource metrics where available. Missing metrics are not evidence of zero traffic or perfect health. Grafana opens from Layer 2 in a **new browser tab**.

### Layer 3

```text
Monitor → Detect → Decide → Act → Verify
```

The degradation watcher observes evidence and can request corrective orchestration only according to configured/validated failover policy. H-CRAN failover is not active merely because H-CRAN appears in the roadmap; disabled scenarios are not demo targets.

### RAN Scenario

Operational Status, Technical Logs, Processes, Runtime and Recent Events are views inside the same persistent RAN window. They preserve the canonical scenario selection. Deploy/Switch is the explicit lifecycle action, separate from read-only inspection and refresh.

### OSM

Shows orchestration/NBI availability and lifecycle/state information. OSM manages deployment lifecycle; the dashboard does not replace that orchestration engine.

### Documentation and window controls

Documentation opens this guide inside the workspace. Application windows overlap and have z-order. Refresh updates only that panel; minimize preserves it in the dock; maximize/restore changes its geometry; close removes its UI window and polling. These window controls affect only the dashboard UI, not workload lifecycle. Existing automatic polling continues without an extra timer from manual refresh. Global refresh updates dashboard-wide summary/state.

## 6. Layer 1 / Layer 2 / Layer 3

- **Layer 1 — Automated Deployment:** validated scenario/package selection and OSM/Kubernetes deployment.
- **Layer 2 — Network Intelligence:** live evidence, metrics, logs and visualization.
- **Layer 3 — Autonomous Optimization:** configured degradation detection, decisions, corrective action and verification.

These are the presentation's conceptual project layers, not OSI layers or new 3GPP protocol layers.

## 7. Current validated status table

| Scenario | OAI | srsRAN | Status |
| --- | --- | --- | --- |
| C-RAN | Enabled | Enabled | Validated |
| Cloud-RAN | Enabled | Enabled | Validated |
| vC-RAN | Enabled | Enabled | Validated |
| O-RAN | Enabled | Disabled | OAI RAN-level validated; E2 pending |
| H-CRAN | Disabled | Disabled | Future |
| F-RAN | Disabled | Disabled | Future |

Validated RAN-level deployment does not establish UE/PDU validation for every row. Disabled selections remain visible and show: “This scenario is not enabled in the current validated release.”

## 8. Important terminology

| Term | Meaning |
| --- | --- |
| AMF | Access and Mobility Management Function; registration and mobility control. |
| NGAP | NG Application Protocol, the RAN–AMF control-plane protocol. |
| F1 | Interface between gNB-CU and gNB-DU. |
| CU | Central Unit, the higher-layer gNB functions. |
| DU | Distributed Unit, the lower-layer gNB functions. |
| RFSIM | Simulated radio transport used instead of physical RF hardware. |
| USRP | Software-defined radio hardware used in hardware RF workflows. |
| CNF | Containerized network function. |
| OSM | Open Source MANO lifecycle orchestrator. |
| NBI | Northbound Interface, the API used to request orchestration. |
| HPA | Kubernetes Horizontal Pod Autoscaler; requires metrics and a safe scaling policy. |
| BLER | Block error rate. |
| HARQ | Hybrid automatic repeat request, supporting retransmission/error recovery. |
| E2 | O-RAN interface connecting RAN nodes to the near-real-time RIC. |
| RIC | RAN Intelligent Controller. |
| KPM | Key Performance Measurement E2 service model. |
| RC | RAN Control E2 service model. |

## 9. Demo workflow

```text
Open dashboard
  → choose a supported C-RAN, Cloud-RAN, vC-RAN or O-RAN scenario
  → select OAI/srsRAN (O-RAN: OAI only)
  → Deploy/Switch
  → verify Operational Status
  → inspect Technical Logs
  → inspect Runtime/Recent Events
  → view 5G Core
  → view Layer 2
  → open Grafana if needed
```

Wait for terminal success before declaring a switch complete. Inspect UE registration/PDU evidence separately when demonstrating end-to-end service. Do not use H-CRAN, F-RAN or O-RAN + srsRAN as deployment targets in this release.
