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
