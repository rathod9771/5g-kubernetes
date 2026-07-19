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



---

## End-to-End Integration

### Connection Diagram (Control + User Plane)
CONTROL PLANE
┌────┐  RRC/NAS  ┌─────┐   NGAP/SCTP    ┌─────┐  SBI/HTTP2   ┌──────────────────┐
│ UE │◄─────────►│ gNB │◄──────────────►│ AMF │◄────────────►│ NRF ◄─► AUSF     │
└────┘  (RLS/    └─────┘  :38412        └──┬──┘   :7777      │  ▲       ▼       │
▲      ZMQ sim)                          │                 │  │      UDM      │
│                                        │ Namf/Nsmf       │  │       ▼       │
│                                        ▼                 │  └────► UDR      │
│                                     ┌─────┐    PFCP      └──────────┬───────┘
│                                     │ SMF │◄──────┐                 ▼
│                                     └─────┘ :8805 │            ┌─────────┐
│                                                   ▼            │ MongoDB │
│                 USER PLANE                    ┌───────┐        └─────────┘
│   GTP-U tunnel (encapsulated IP)              │  UPF  │
└──────────────────────────────────────────────►│ogstun │──► NAT ──► Internet
UE 10.45.0.x ──► gNB pod ──► UDP :2152      │10.45. │    (masquerade
│ 0.1/16│     10.45.0.0/16)
└───────┘
Registration flow: UE → gNB (RRC) → AMF (NGAP) → AUSF → UDM → UDR → MongoDB
(5G-AKA authentication) → back down → Security Mode → Registration Accept →
SMF creates PDU session → UPF programs GTP tunnel → UE gets `uesimtun0` with
an IP from 10.45.0.0/16.

### Network Interface Diagram
UE pod                    gNB pod                  UPF pod
┌──────────────┐          ┌──────────────┐         ┌──────────────────┐
│ eth0 (pod IP)│◄────────►│ eth0 (pod IP)│◄───────►│ eth0 (pod IP)    │
│              │  RLS sim │  binds:      │  GTP-U  │                  │
│ uesimtun0    │  (UDP    │  ngapIp=podIP│  UDP    │ ogstun 10.45.0.1 │──► iptables
│ 10.45.0.x    │  :4997)  │  gtpIp =podIP│  :2152  │ (TUN device)     │    MASQUERADE
└──────────────┘          └──────────────┘         └──────────────────┘    ──► internet
│                          │
│      N2 (NGAP/SCTP :38412) to ClusterIP service:
│      open5gs-amf-ngap ──► AMF pod
└── all UE application traffic enters uesimtun0 and
travels inside GTP-U between gNB and UPF
Key design decision — **no Multus / static IPs anywhere**: every component
binds its own pod IP and reaches peers via ClusterIP service DNS
(`open5gs-amf-ngap`, `srsran-cu`, …). The pod IP is discovered at container
start from `/etc/hosts` and substituted into configs:

```bash
# Pattern used in every RAN chart's init script:
export POD_IP=$(awk 'END{print $1}' /etc/hosts)
export AMF_IP=$(getent hosts open5gs-amf-ngap | awk '{print $1}')
sed -e "s|AMF_IP|${AMF_IP}|g" -e "s|POD_IP|${POD_IP}|g" template.conf > live.conf
```

(The free5gc-era platform used Multus `NetworkAttachmentDefinition`s with
ipvlan on `enp3s0` and static 10.100.50.x addressing — removed because
ipvlan secondary interfaces cannot reach ClusterIP services; kube-proxy's
NAT rules are invisible to them. See tag `free5gc-platform-v1` for that
model.)

### Errors Hit During Integration (and Fixes)

| # | Symptom (UE side) | Root cause found | Fix |
|---|---|---|---|
| 1 | `Cell selection failure ... [1] barred` forever | gNB never completed NG Setup — SCTP to AMF timing out | gNB had a Multus ipvlan interface; SCTP left via it and couldn't reach ClusterIP/pod IPs. Removed Multus (n2/n3network `enabled: false`) |
| 2 | `SCTP bind failed: Cannot assign requested address` | gNB config still bound old static Multus IP after interface removed | Bind addresses → `0.0.0.0` (UERANSIM) / pod IP (srsRAN, OAI) |
| 3 | `SEMANTICALLY_INCORRECT_MESSAGE` reject, AMF log `HTTP response error [400/500]` on `nausf-auth` discovery, NRF log `No SEPP [...3gppnetwork.org]` | AUSF/UDM/UDR registered in NRF with built-in default PLMN **999/70**; AMF serves 208/93, so NRF treated discovery as *roaming* and tried a (nonexistent) SEPP | `customOpen5gsConfig` with `serving: plmn_id 208/93` on AUSF, UDM, UDR |
| 4 | Random alternating errors (400/503/PAYLOAD_NOT_FORWARDED), NFs losing NRF heartbeat every ~10 s | Open5GS 2.7.0 **NRF segfaulting** repeatedly under NF churn; also Istio Envoy mishandling SBI HTTP/2 (bare `sbi` port name defeats protocol detection) | Chart 2.2.6 / images 2.7.2 (NRF fixed); Istio removed from namespace; `appProtocol: http2` documented for anyone keeping a mesh |
| 5 | `UE_IDENTITY_CANNOT_BE_DERIVED_FROM_NETWORK` | Stale GUTI from failed attempts + NF restarts mid-flow | Transient — cleared once the NF chain was stable |
| 6 | Registration + PDU session OK, but `ping -I uesimtun0` 100% loss (even to 10.45.0.1) | gNB advertised **0.0.0.0** as its GTP-U endpoint (bound 0.0.0.0 → advertised 0.0.0.0); user-plane packets had no valid return address | gNB advertises its real pod IP (downward-API / `/etc/hosts` substitution at startup) |
| 7 | srsRAN NG Setup rejected: `slice-not-supported` | gNB slice list had `sst: 1` only; network slice is SST 1 + SD 0x010203 | Add SD — decimal `66051` for srsRAN, hex `0x010203` for OAI |
| 8 | UE `Authentication Failure due to SQN out of range` (once, then success) | Normal 5G-AKA sequence-number resync on first attach after DB re-provisioning | None needed — resync is part of the protocol |

### End-to-End Verification Commands

Run these in order — each proves one segment of the chain:

```bash
NS=free5gc
AMFPOD=$(kubectl get pods -n $NS -l app.kubernetes.io/name=amf -o jsonpath='{.items[0].metadata.name}')
UEPOD=$(kubectl get pods -n $NS -l component=ue -o jsonpath='{.items[0].metadata.name}')

# ── 1. Core health: every pod 1/1 Running, zero restarts on NRF
kubectl get pods -n $NS

# ── 2. NRF registry sane: AUSF discoverable WITH the right PLMN (must show 208/93)
kubectl exec -n $NS $AMFPOD -- curl -s --http2-prior-knowledge \
  'http://open5gs-nrf-sbi:7777/nnrf-disc/v1/nf-instances?target-nf-type=AUSF&requester-nf-type=AMF' \
  | grep -o '"plmnList":\[[^]]*\]'

# ── 3. gNB ↔ AMF (N2/NGAP): AMF must log the acceptance
kubectl logs $AMFPOD -n $NS | grep -E "gNB-N2 accepted|Number of gNBs"

# ── 4. SCTP associations from the RAN side (state 3 = ESTABLISHED)
#      For split RANs this shows BOTH NGAP (:38412) and F1 (:38472)
CUPOD=$(kubectl get pods -n $NS -l app=srsran-cu -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -n "$CUPOD" ] && kubectl exec -n $NS $CUPOD -- cat /proc/net/sctp/assocs

# ── 5. UE registration + PDU session (the control-plane end-to-end proof)
kubectl logs $UEPOD -n $NS | grep -E "Initial Registration is successful|PDU Session establishment is successful|TUN interface"
# Expect:  Initial Registration is successful
#          PDU Session establishment is successful PSI[1]
#          TUN interface[uesimtun0, 10.45.0.x] is up

# ── 6. User plane inside the network: UE → UPF tunnel endpoint
kubectl exec -n $NS $UEPOD -- ping -I uesimtun0 -c 4 10.45.0.1
# 0% loss proves the GTP-U path UE → gNB → UPF works

# ── 7. User plane to the internet: UE → world through UPF NAT
kubectl exec -n $NS $UEPOD -- ping -I uesimtun0 -c 4 8.8.8.8
kubectl exec -n $NS $UEPOD -- curl --interface uesimtun0 -s -o /dev/null -w "%{http_code}\n" http://www.google.com
# 0% loss + HTTP 200 = complete end-to-end data path

# ── 8. Live latency/status via the dashboard API
curl -s http://localhost:8090/api/ue-status | python3 -m json.tool
curl -s http://localhost:8090/api/latency   | python3 -m json.tool
```

Interpretation guide: step 5 failing with step 3 passing → look at the
AUSF/UDM/UDR chain (error table #3). Step 6 failing with step 5 passing →
GTP-U addressing (error table #6). Step 7 failing with step 6 passing →
UPF NAT/forwarding (`iptables -t nat`, `ip_forward`).

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
