# 5G Automated Deployment Platform

## Project Overview
An automated platform for deploying various 5G scenarios based on open source tools — inspired by Amrita University research project.

## What This Project Does
Like choosing an OS at boot time, this platform lets you select which RAN (Radio Access Network) to deploy with one click — and automatically deploys it on Kubernetes via GitOps.

## Architecture
## Stack
| Tool | Role |
|------|------|
| kubeadm | Bare metal Kubernetes cluster |
| free5gc | 5G Core (AMF, SMF, UPF, NRF, AUSF, UDM) |
| UERANSIM | Simulated gNB + UE |
| srsRAN | C-RAN deployment |
| OpenAirInterface | O-RAN (CU + DU) deployment |
| Helm | Kubernetes packaging |
| ArgoCD | GitOps CD automation |
| Flannel + Multus | CNI networking |
| gtp5g v0.8.10 | GTP kernel module for UPF |
| Flask | RAN Selector backend API |

## Deployment Scenarios Supported
- **C-RAN** - Centralized RAN using srsRAN (single gNB)
- **O-RAN** - Disaggregated RAN using OpenAirInterface (CU + DU split)

## Results
- free5gc 5G Core: 12 pods running on Kubernetes
- UERANSIM UE registered and connected
- End-to-end ping: 8.8.8.8 successful (12ms)
- RAN switching: one click via web UI

## Repository Structure
## Author
Kumar - Telco Cloud Engineer
