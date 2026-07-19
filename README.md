# 5G Network Deployment Platform on Kubernetes

**Automated Platform for 5G Deployment Scenarios based on Open Source**

A research platform (Amrita University) that deploys a complete, working 5G
network — core, RAN, and simulated UE — on a single-node Kubernetes cluster,
with a web dashboard for switching between RAN architectures at the click of
a button.

**Current state: fully operational end-to-end.** A simulated UE registers
through 5G-AKA authentication, establishes a PDU session, and reaches the
internet through the GTP-U user plane (verified: 0% loss, ~10-20 ms RTT
through `uesimtun0`).

---

## Stack

| Layer | Component | Version |
|---|---|---|
| 5G Core | Open5GS (Gradiant Helm chart 2.2.6) | 2.7.2 |
| RAN | srsRAN Project + OpenAirInterface | latest / 2026.w13 |
| UE/gNB simulator | UERANSIM (towards5gs chart) | v3.2.6 |
| Database | MongoDB (custom StatefulSet) | 6.0 |
| Orchestration | kubeadm Kubernetes + Flannel + Multus | v1.29 |
| Dashboard | Flask + vanilla JS | — |

Previous free5gc-based platform (including Istio service-mesh integration,
Rancher, and OSM onboarding) is preserved at tag **`free5gc-platform-v1`**.

---

## RAN Architecture Matrix

Six deployable RAN variants — three architectures × two software stacks:

| | srsRAN | OAI |
|---|---|---|
| **C-RAN** (centralized, monolithic gNB) | `helm/srsran` | `helm/oai-cran` |
| **O-RAN** (CU/DU split over F1) | `helm/srsran-oran/{cu,du}` | `helm/oai/{cu,du}` |
| **Cloud-RAN** (resource-profiled cloud workload) | `helm/cloud-ran-srsran` | `helm/cloud-ran-oai` |

### Terminology

These terms carry ambiguity in industry literature, so we state our working
definitions explicitly:

- **C-RAN (Centralized RAN):** all gNB functions (CU + DU + PHY) centralized
  in a single monolithic deployment — one pod running the full gNB stack.
- **O-RAN (Open / Disaggregated RAN):** gNB disaggregated into CU and DU as
  independent network functions communicating over the standardized F1
  interface (F1-C on SCTP 38472), discovered via Kubernetes Services.
- **Cloud-RAN:** RAN functions as cloud-native workloads under Kubernetes
  elastic resource management (explicit requests/limits: 1 CPU / 1 Gi
  requested, 4 CPU / 4 Gi limit).

*Historical note:* "C-RAN" originated as Centralized RAN (China Mobile, 2010 —
pooled BBUs with remote radio heads over fronthaul) and was later also read
as "Cloud RAN" by parts of the industry. "O-RAN" strictly refers to the O-RAN
Alliance interface specifications (E2/O1/RIC); we use it in the common looser
sense of an open CU/DU functional split.

---

## Network Configuration

| Parameter | Value |
|---|---|
| PLMN | 208 / 93 |
| TAC | 1 |
| Slice | SST 1, SD 0x010203 |
| Test subscriber | IMSI 208930000000003 (auto-provisioned via Helm hook) |
| UE subnet | 10.45.0.0/16 (UPF `ogstun`, NAT to internet) |

---

## Quick Start

```bash
# 1. Core (namespace kept as 'free5gc' for historical continuity)
helm install open5gs ./helm/open5gs -n free5gc

# 2. UE + simulated gNB
helm install ueransim ./helm/ueransim -n free5gc

# 3. Any RAN variant, e.g. O-RAN + srsRAN (CU first, then DU)
helm install srsran-cu ./helm/srsran-oran/cu -n free5gc
sleep 30
helm install srsran-du ./helm/srsran-oran/du -n free5gc

# 4. Dashboard
cd ran-selector && python3 backend.py   # http://<host>:8090

# Validate end-to-end
UEPOD=$(kubectl get pods -n free5gc -l component=ue -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n free5gc $UEPOD -- ping -I uesimtun0 -c 4 8.8.8.8
```

---

## Dashboard

`ran-selector/` — Flask backend (port 8090) + single-page UI:

- **Two-step selector:** architecture (C-RAN / O-RAN / Cloud-RAN) → stack
  (srsRAN / OAI), with switch-confirmation and already-deployed guards
- **Live status bar:** UE registration state, PDU session, real RTT and
  packet loss measured through the 5G user plane (`/api/latency`,
  `/api/ue-status`)
- **NF inspection panels:** live logs and status for every Open5GS NF
- **State recovery:** on page load the UI restores the actually-deployed
  combo from cluster state (`/api/verify-clean`) — refresh-proof
- **Clean-switch enforcement:** every release uninstalled individually;
  `verify-clean` endpoint proves exactly one combo is active

---

## Engineering Notes (hard-won)

Documented for anyone reproducing this — each cost real debugging time:

1. **Bitnami image tags:** Bitnami moved Docker Hub to SHA-only "Secure
   Images" tagging; the chart's mongodb dependency references tags that no
   longer exist. Replaced with a minimal official-image MongoDB StatefulSet
   and per-NF `dbURI` overrides. The webui chart additionally hardcodes a
   Bitnami mongo init image *in its template* (not values-controlled) and
   needs `mongo:5.0` — 6.0+ dropped the legacy `mongo` shell its script calls.
2. **Open5GS 2.7.0 NRF segfaults** under NF churn (fixed by 2.7.2). Chart
   and image versions must move together — 2.7.5 images broke config-schema
   compatibility with the 2.2.0-era chart templates.
3. **Serving PLMN:** AUSF/UDM/UDR register with built-in default PLMN 999/70
   unless a `serving:` PLMN is set. NRF then treats same-network discovery
   as roaming, attempts SEPP lookup, and returns 500 — surfacing at the UE
   as `SEMANTICALLY_INCORRECT_MESSAGE`. Fixed via `customOpen5gsConfig`.
4. **SCP removed:** direct-NRF SBI mode on all NFs (matches reference
   architectures; also sidesteps SCP-triggered NRF instability).
5. **SMF freeDiameter:** `smf.config.pcrf.enabled` defaults true independent
   of the top-level `pcrf.enabled=false` → Gx init crash. Disable both.
6. **gNB bind addresses:** a gNB that binds 0.0.0.0 advertises 0.0.0.0 as its
   GTP-U endpoint — control plane works, user plane silently dead. Every RAN
   chart substitutes the pod's real IP at startup (downward-API / /etc/hosts)
   and resolves the AMF from the `open5gs-amf-ngap` service DNS.
7. **Slice SD is mandatory** in gNB configs here: srsRAN takes decimal
   (`sd: 66051`), OAI takes hex (`sd = 0x010203`). Omitting it → NG Setup
   rejected with `slice-not-supported`.
8. **`helm uninstall a b c` aborts at the first missing release** — cleanup
   lists must uninstall per-release or previous combos survive switches.
9. **ipvlan (Multus) secondary interfaces can't reach ClusterIP services** —
   kube-proxy NAT isn't visible from them. The Open5GS model needs no Multus
   on RAN pods at all.

---

## Roadmap

- [x] Open5GS core on Kubernetes, full UE registration + internet
- [x] 6-variant RAN matrix ported and verified
- [x] Dashboard v2 with live metrics
- [ ] USRP B210 bare-metal srsRAN + real smartphone (mentor demo)
- [ ] Kamailio IMS for VoNR voice calls
- [ ] Containerized USRP RAN profile

## References

- Open5GS — https://open5gs.org
- Gradiant 5G charts — https://github.com/Gradiant/5g-charts
- srsRAN Project — https://www.srsran.com
- OpenAirInterface — https://openairinterface.org
- UERANSIM — https://github.com/aligungr/UERANSIM
- towards5gs-helm (Orange) — https://github.com/Orange-OpenSource/towards5gs-helm
