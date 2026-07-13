# 5G Automated Deployment Platform

## Project Overview
An automated platform for deploying various 5G RAN scenarios on Kubernetes using open source tools.
Inspired by Amrita University research project on "Automated Platform for various 5G Deployment Scenarios based on Open Source."

Like choosing an OS at boot time — this platform lets you select which RAN to deploy with one click,
automatically deploying it on Kubernetes via GitOps.

## Architecture RAN Selector UI (Web Dashboard)

|

v

Flask Backend --> GitHub (active-ran.yaml)

|

v

ArgoCD (GitOps)

|

v

Kubernetes (kubeadm)

/                    
srsRAN (C-RAN)        OpenAirInterface (O-RAN)

Single gNB pod         CU pod + DU pod

Centralized            Disaggregated (F1 interface)

\                    /

free5gc 5G Core

AMF -> SMF -> UPF -> Internet## Deployment Scenarios

### Scenario 1 - C-RAN (Centralized RAN) using srsRAN
- Single gNB pod handles all baseband processing
- Connects to AMF via NGAP/SCTP
- Supports ZMQ simulation + USRP B210 real RF

### Scenario 2 - O-RAN (Disaggregated RAN) using OpenAirInterface
- Two separate pods: CU (Central Unit) + DU (Distributed Unit)
- CU connects to AMF via NGAP
- DU connects to CU via F1 interface (SCTP port 38472)
- True functional split as per 3GPP O-RAN specifications

## Results Achieved
- free5gc 5G Core: 12 pods running on Kubernetes
- UERANSIM UE registered and connected
- End-to-end ping: 8.8.8.8 successful (12ms) through 5G core
- srsRAN C-RAN: gNB connected to AMF via NGAP
- OAI O-RAN: CU connected to AMF + DU connected to CU via F1
- RAN switching: one click via web UI triggers GitOps deployment

## Stack

| Tool | Role |
|------|------|
| kubeadm | Bare metal Kubernetes cluster on Ubuntu 24.04 |
| free5gc v3.3.0 | 5G Core (AMF, SMF, UPF, NRF, AUSF, UDM, PCF) |
| UERANSIM v3.2.6 | Simulated gNB + UE for testing |
| srsRAN | C-RAN deployment (Centralized gNB) |
| OpenAirInterface | O-RAN deployment (CU + DU split) |
| Helm | Kubernetes packaging and templating |
| ArgoCD | GitOps CD - auto deploys on GitHub changes |
| Flannel | Pod networking CNI |
| Multus | Multiple network interfaces (N2, N3, N4, N6, F1) |
| gtp5g v0.8.10 | GTP kernel module for UPF data plane |
| Flask | RAN Selector backend API |
| GitHub | Single source of truth for GitOps |

## Network Interfaces (Multus)

| Interface | Subnet | Used By |
|-----------|--------|---------|
| N2 | 10.100.50.248/29 | AMF (.249), srsRAN (.251), OAI-CU (.252), OAI-DU (.253) |
| N3 | 10.100.50.232/29 | UPF ↔ gNB (GTP-U) |
| N4 | 10.100.50.240/29 | SMF ↔ UPF (PFCP) |
| N6 | 10.100.100.0/24 | UPF ↔ Internet |

## Repository Structure
RAN Selector UI (Web Dashboard)

|

v

Flask Backend --> GitHub (active-ran.yaml)

|

v

ArgoCD (GitOps)

|

v

Kubernetes (kubeadm)

/                    
srsRAN (C-RAN)        OpenAirInterface (O-RAN)

Single gNB pod         CU pod + DU pod

Centralized            Disaggregated (F1 interface)

\                    /

free5gc 5G Core

AMF -> SMF -> UPF -> Internet >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>## Deployment Scenarios

### Scenario 1 - C-RAN (Centralized RAN) using srsRAN
- Single gNB pod handles all baseband processing
- Connects to AMF via NGAP/SCTP
- Supports ZMQ simulation + USRP B210 real RF

### Scenario 2 - O-RAN (Disaggregated RAN) using OpenAirInterface
- Two separate pods: CU (Central Unit) + DU (Distributed Unit)
- CU connects to AMF via NGAP
- DU connects to CU via F1 interface (SCTP port 38472)
- True functional split as per 3GPP O-RAN specifications

## Results Achieved
- free5gc 5G Core: 12 pods running on Kubernetes
- UERANSIM UE registered and connected
- End-to-end ping: 8.8.8.8 successful (12ms) through 5G core
- srsRAN C-RAN: gNB connected to AMF via NGAP
- OAI O-RAN: CU connected to AMF + DU connected to CU via F1
- RAN switching: one click via web UI triggers GitOps deployment

## Stack

| Tool | Role |
|------|------|
| kubeadm | Bare metal Kubernetes cluster on Ubuntu 24.04 |
| free5gc v3.3.0 | 5G Core (AMF, SMF, UPF, NRF, AUSF, UDM, PCF) |
| UERANSIM v3.2.6 | Simulated gNB + UE for testing |
| srsRAN | C-RAN deployment (Centralized gNB) |
| OpenAirInterface | O-RAN deployment (CU + DU split) |
| Helm | Kubernetes packaging and templating |
| ArgoCD | GitOps CD - auto deploys on GitHub changes |
| Flannel | Pod networking CNI |
| Multus | Multiple network interfaces (N2, N3, N4, N6, F1) |
| gtp5g v0.8.10 | GTP kernel module for UPF data plane |
| Flask | RAN Selector backend API |
| GitHub | Single source of truth for GitOps |

## Network Interfaces (Multus)

| Interface | Subnet | Used By |
|-----------|--------|---------|
| N2 | 10.100.50.248/29 | AMF (.249), srsRAN (.251), OAI-CU (.252), OAI-DU (.253) |
| N3 | 10.100.50.232/29 | UPF ↔ gNB (GTP-U) |
| N4 | 10.100.50.240/29 | SMF ↔ UPF (PFCP) |
| N6 | 10.100.100.0/24 | UPF ↔ Internet |

## Repository Structure
## Deployment Scenarios

### Scenario 1 - C-RAN (Centralized RAN) using srsRAN
- Single gNB pod handles all baseband processing
- Connects to AMF via NGAP/SCTP
- Supports ZMQ simulation + USRP B210 real RF

### Scenario 2 - O-RAN (Disaggregated RAN) using OpenAirInterface
- Two separate pods: CU (Central Unit) + DU (Distributed Unit)
- CU connects to AMF via NGAP
- DU connects to CU via F1 interface (SCTP port 38472)
- True functional split as per 3GPP O-RAN specifications

## Results Achieved
- free5gc 5G Core: 12 pods running on Kubernetes
- UERANSIM UE registered and connected
- End-to-end ping: 8.8.8.8 successful (12ms) through 5G core
- srsRAN C-RAN: gNB connected to AMF via NGAP
- OAI O-RAN: CU connected to AMF + DU connected to CU via F1
- RAN switching: one click via web UI triggers GitOps deployment

## Stack

| Tool | Role |
|------|------|
| kubeadm | Bare metal Kubernetes cluster on Ubuntu 24.04 |
| free5gc v3.3.0 | 5G Core (AMF, SMF, UPF, NRF, AUSF, UDM, PCF) |
| UERANSIM v3.2.6 | Simulated gNB + UE for testing |
| srsRAN | C-RAN deployment (Centralized gNB) |
| OpenAirInterface | O-RAN deployment (CU + DU split) |
| Helm | Kubernetes packaging and templating |
| ArgoCD | GitOps CD - auto deploys on GitHub changes |
| Flannel | Pod networking CNI |
| Multus | Multiple network interfaces (N2, N3, N4, N6, F1) |
| gtp5g v0.8.10 | GTP kernel module for UPF data plane |
| Flask | RAN Selector backend API |
| GitHub | Single source of truth for GitOps |

## Network Interfaces (Multus)

| Interface | Subnet | Used By |
|-----------|--------|---------|
| N2 | 10.100.50.248/29 | AMF (.249), srsRAN (.251), OAI-CU (.252), OAI-DU (.253) |
| N3 | 10.100.50.232/29 | UPF ↔ gNB (GTP-U) |
| N4 | 10.100.50.240/29 | SMF ↔ UPF (PFCP) |
| N6 | 10.100.100.0/24 | UPF ↔ Internet |

## Repository Structure
5g-kubernetes/

├── helm/

│   ├── open5gs/          # Custom Open5GS Helm charts

│   ├── srsran/           # C-RAN Helm chart (srsRAN gNB)

│   └── oai/

│       ├── cu/           # O-RAN CU Helm chart

│       └── du/           # O-RAN DU Helm chart

├── ran-selector/

│   ├── index.html        # RAN Selector Web UI

│   ├── backend.py        # Flask API - updates GitHub + deploys

│   ├── select.sh         # CLI selector script

│   └── active-ran.yaml   # GitOps config (ArgoCD watches this)

└── README.md
## How GitOps Works
User clicks "Deploy srsRAN" or "Deploy OAI O-RAN" in Web UI
Flask backend updates active-ran.yaml on disk
Flask runs git push to GitHub
ArgoCD detects change in GitHub (polls every 3 min)
ArgoCD runs helm install/upgrade on Kubernetes
New RAN pods start and connect to free5gc core
UI shows green "Running" status
## Challenges Solved
1. Minikube doesnt support gtp5g kernel module - switched to kubeadm
2. Network interface mismatch (eth0 vs enp3s0) - patched all NADs
3. gtp5g version mismatch - downgraded to v0.8.10
4. UPF internet routing - policy routing with iptables MARK
5. OAI F1 port conflict (2152 used by both GTP-U and F1) - used 38472 for F1
6. OAI config format - used official .conf format with full RF parameters

## Author
Kumar - Telco Cloud Engineer
Amrita Vishwa Vidyapeetham - ECE Department


---

## 🎛️ Management Layer

| Tool | Port | Purpose |
|---|---|---|
| **Rancher** | 8443 | Kubernetes cluster management — visual pod inspection, logs, shell access |
| **Custom Dashboard** | 8090 | RAN selector + live NF logs + deploy via GitOps |
| **Kiali** | 20001 | Istio service mesh observability — traffic graph, success rates |
| **free5gc WebUI** | 30500 | Subscriber management |

### Rancher Setup

```bash
# Install cert-manager (required by Rancher)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.3/cert-manager.yaml

# Install Rancher
helm repo add rancher-latest https://releases.rancher.com/server-charts/latest
helm install rancher rancher-latest/rancher \
  --namespace cattle-system \
  --set hostname=rancher.<HOST_IP>.sslip.io \
  --set bootstrapPassword=<PASSWORD> \
  --set replicas=1

# Access via port-forward
kubectl port-forward -n cattle-system svc/rancher 8443:443 --address 0.0.0.0
# Open: https://<HOST_IP>:8443
```

### Istio Service Mesh

All 8 control plane NFs (AMF, SMF, NRF, AUSF, UDM, UDR, PCF, NSSF) run with Envoy sidecars (2/2).
UPF is intentionally excluded — it handles GTP-U data plane traffic which the mesh does not proxy.

Key integration steps:
1. Installed Istio with **CNI plugin** (`components.cni.enabled=true`) — avoids init container breakage with Multus
2. Labeled namespace: `kubectl label namespace free5gc istio-injection=enabled`
3. Excluded NRF and MongoDB ClusterIPs from interception via `traffic.sidecar.istio.io/excludeOutboundIPRanges` so init containers (wait-nrf, wait-mongo) can reach dependencies before Envoy starts
4. Result: 100% SBI success rate, ~12ms p50 latency in Kiali

### Architecture (matches Amrita reference poster)
## 🗺️ Roadmap

- [x] free5gc core on Kubernetes — all 12 pods
- [x] UERANSIM end-to-end — UE pings 8.8.8.8 through 5G core
- [x] srsRAN (C-RAN) connected to AMF via NGAP
- [x] OAI O-RAN CU+DU deployed with F1 interface
- [x] RAN Selector dashboard with GitOps deploy + live kubectl logs
- [x] Istio service mesh — 8 NFs with Envoy sidecars
- [x] Rancher cluster management
- [x] OSM (Open Source MANO) - ETSI NFV orchestration - 14/14 pods running
- [ ] O-RAN Near-RT RIC — activate OAI E2 interface
- [ ] USRP B210 — real RF with physical SIM


---

## 🏗️ OSM (Open Source MANO) — ETSI NFV Orchestration

Deployed OSM 14 onto the existing kubeadm cluster (not a separate installer-managed cluster) by using its Helm chart directly instead of the full installer script.

### Why not the standard installer?
`install_osm.sh` assumes a blank machine and tries to run its own `kubeadm init` — which collides with an already-running cluster (port 6443, existing etcd, manifest files). Solution: extract the Helm chart the installer ships (`/usr/share/osm-devops/installers/helm/osm`) and deploy it directly into an `osm` namespace on the existing cluster.

### Challenges & fixes

| Problem | Root Cause | Fix |
|---|---|---|
| 5 Services failed on `helm install` | Chart hardcodes ports outside the NodePort range (80, 3000, 9091, 9998, 9999) | Overrode via `--set` for most; patched `grafana-service.yaml` directly (its port was hardcoded, not templated) |
| `ngui` CrashLoopBackOff | nginx couldn't resolve `nbi` upstream | Resolved once NBI came up (see below) |
| `nbi`, `ro`, `mon`, `lcm` stuck in `Init` | MongoDB was never deployed — OSM's chart expects it as a **separate** release | `helm install mongodb-k8s bitnami/mongodb -f mongodb-values.yaml` |
| `lcm` `FailedMount: secret "lcm-client-cert" not found` | cert-manager `Certificate` → `ClusterIssuer(ca-issuer)` → needs `osm-ca` secret, but cert-manager reads CA secrets from **its own namespace**, while the chart created it in `osm` | Copied `osm-ca` secret from `osm` → `cert-manager` namespace, then restarted the cert-manager controller pod to force re-evaluation |
| `lcm` init container `alpine:latest` never pulled | No internet image pull path for that tag in containerd cache | `docker pull` → `docker save` → `ctr images import` (same pattern used earlier for free5gc/UPF images) |

### Result
All 14 OSM pods Running: NBI, LCM, RO, MON, Keystone, NG-UI, Kafka, Zookeeper, MongoDB (+arbiter), MySQL, Prometheus, Grafana, Webhook-translator.

Access:
```bash
# OSM UI
http://<HOST_IP>:30080   # admin / admin

# OSM NBI (API)
http://<HOST_IP>:30999
```

### Architecture — matches Amrita reference poster in full
