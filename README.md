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

---

## RF Tuning for Over-the-Air Operation (USRP B210)

Once real phones were registering, the remaining work was making the radio link
survive. This section records the RF-layer problems found while driving a USRP
B210 from a bare-metal srsRAN gNB, and how each was diagnosed.

### Antenna constraint drove the band choice

The lab's antennas are Ettus VERT900, rated for 824-960 MHz and 1710-1990 MHz.
Band n78 (3489 MHz) is far outside that range, so the antennas radiated and
received very inefficiently there: uplink SINR sat between -14 and -31 dB and
the link collapsed within about two minutes. The gNB was retuned to band n3
(dl_arfcn 368500, 1842.5 MHz), which falls inside the antenna's upper range.
Uplink SINR immediately improved to positive double digits.

### Errors encountered and how they were resolved

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Uplink SINR -14 to -31 dB, RLF within ~2 min on band n78 | VERT900 antennas are out of band at 3.5 GHz | Retune the cell to band n3 (1842.5 MHz), inside the antenna's 1710-1990 MHz range |
| 2 | gNB log: Real-time failure in RF: underflow | Host not feeding IQ samples to the B210 fast enough over USB | otw_format sc12 (12-bit over-the-wire) plus deeper USB buffers via num_recv_frames and num_send_frames set to 512 |
| 3 | RLF with cause MAC max KOs reached, 100 consecutive HARQ-ACK KOs or undecoded CSIs | Uplink control channel not decoding | Two separate contributors, see rows 4 and 6 |
| 4 | rsrp column reported ovl in the metrics table; SINR stuck at 2-5 dB despite a close handset | Receiver front-end overload: rx_gain was set too high (65), saturating the ADC so samples arrived distorted | Lower rx_gain to 40. SINR rose to 20-26 dB and throughput to roughly 1.6 Mbps down / 774 kbps up |
| 5 | phr column persistently negative (-9 to -32) | Handset already at maximum transmit power, so raising gNB tx_gain cannot improve the uplink | Treat uplink as power-limited; fix reception instead of demanding more from the UE |
| 6 | ta column showing negative timing advance, rsrp falling ~40 dB in one second on a stationary handset | Timing/frequency drift of the B210's free-running internal oscillator. A negative timing advance is physically impossible for a real distance, so it indicates loss of sync rather than propagation loss | Discipline the clock with an external reference (GPSDO). srsRAN's own band-3 B210 reference configuration specifies an external 10 MHz reference for this reason. Open item at time of writing |
| 7 | Raising max_consecutive_kos from 100 to 400 barely extended the session | The link was not failing from a few unlucky errors but degrading until nothing decoded, consistent with drift rather than noise | Config tolerance is not a substitute for clock discipline |
| 8 | Link survived 60-90 s before the gain fix, but only 5-10 s afterwards | With better SINR the scheduler selects a higher MCS (up to 20), and higher-order modulation tolerates far less timing error | Cap max_ue_mcs to trade throughput for resilience while the clock issue is outstanding |
| 9 | gNB froze during radio init, never reaching gNB started | The process was launched piped through tee; when the pipe buffer filled, back-pressure stalled a real-time thread | Never pipe the live gNB. It writes its own logfile; read that separately |
| 10 | uhd_usrp_probe: No devices found for subdev A:B / clock_source external | subdev and clock_source are runtime settings applied after the device is opened, not device-discovery arguments | Do not put them in --args. Let srsRAN set them via its own config keys |
| 11 | Phones stopped attaching entirely, with no new activity in the AMF log | A helm upgrade and AMF pod restart tears down the gNB's NGAP association, and srsRAN does not reconnect on its own | Restart the bare-metal gNB after any AMF restart |

### Working parameters at time of writing

| Parameter | Value | Note |
|---|---|---|
| band | 3 | Chosen to match VERT900 antenna range |
| dl_arfcn | 368500 | 1842.5 MHz downlink, 1747.5 MHz uplink (FDD) |
| channel_bandwidth_MHz | 10 | Lower bandwidth eases USB and CPU load |
| common_scs | 15 | Required for band n3 |
| srate | 15.36 | Must be one of the rates valid for the PRACH configuration |
| tx_gain | 60 | Reduced from 80; excessive transmit gain worsened self-interference |
| rx_gain | 40 | Reduced from 65 to clear receiver overload |
| otw_format | sc12 | Cuts USB bandwidth |
| device_args | num_recv_frames=512, num_send_frames=512 | Deeper USB buffering |

### Diagnostic commands

Live metrics table, the fastest way to read link health. Press t inside the
running gNB terminal to toggle it. Columns worth watching: pusch is uplink
SINR, rsrp shows ovl when the receiver is overloaded, phr goes negative when
the handset is out of transmit power, ta should be a stable positive value,
and the nok percentage shows block errors.

```bash
# start the gNB (never pipe this through tee or grep)
sudo ~/srsRAN_Project/build/apps/gnb/gnb -c ~/usrp-gnb/gnb_b210.yml

# uplink SINR history from the logfile
grep csi1 /tmp/gnb_b210.log | grep -oE "sinr=[-0-9.]+dB" | tail -20

# radio link failures and their causes
grep -iE "RLF|KOs|underflow|late" /tmp/gnb_b210.log | tail -20

# confirm the radio is healthy and on USB 3
sudo uhd_usrp_probe | grep -iE "B210|USB"

# how many gNBs the core currently has attached
AMFPOD=$(kubectl get pods -n free5gc -l app.kubernetes.io/name=amf -o jsonpath='{.items[0].metadata.name}')
kubectl logs $AMFPOD -n free5gc | grep -iE "gNB-N2 accepted|Number of gNBs" | tail
```

Note that SINR lines only appear when the gNB log level is set to info; at
warning level the PHY lines are suppressed.

### Interpreting the failure signature

The distinction that mattered most in diagnosis was between a weak link and a
drifting clock. A weak link degrades gradually: SINR sags, block errors climb,
then the connection drops. A drifting clock fails abruptly with SINR still
high, and it leaves a fingerprint in the timing advance column, which turns
negative or jumps erratically. Recognising the second pattern is what
redirected the work from gain and antenna tuning to clock discipline.


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


---

## IMS (Kamailio) on Kubernetes

VoNR requires an IMS alongside the 5G core: Kamailio in three roles (P-CSCF,
I-CSCF, S-CSCF), PyHSS with a MySQL backend for IMS subscriber data, rtpengine
for media, and a DNS server, since IMS routing resolves component names within
the 3gppnetwork.org realm.

### Why a single pod

The components were first run from the upstream docker_open5gs compose file
beside the cluster. That failed on networking: pods on flannel could not reach
the Docker bridge network at all, while the host could. Two container runtimes
writing iptables rules on one host proved to be a recurring fight rather than a
one-off.

The chart in helm/ims therefore runs all seven components as containers in a
single pod. That solves the addressing problem directly: in compose each
component has a static IP baked into the DNS zone and every config, whereas in
Kubernetes pod IPs are dynamic. Sharing one pod means one IP, so every
component's *_IP environment variable is set from status.podIP via fieldRef and
the upstream init scripts configure themselves unchanged. Ports do not collide
(SIP on 4060/5060/6060, Diameter on 3868-3871), and the UE reaches everything
through one Service ClusterIP.

### Core-side prerequisites

The UE opens a second PDU session purely for SIP signalling, so the core needs:

- an `ims` DNN on 10.46.0.0/16, with the UPF creating a second tunnel
  interface (ogstun2) alongside ogstun
- subscribers carrying both DNNs (`open5gs-dbctl update_apn <imsi> ims 0`)
- the SMF advertising the P-CSCF address during session establishment, so the
  handset learns where to send SIP

### Errors encountered and how they were resolved

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Compose containers all reported Up, but CSCFs and PyHSS never started; connections to MySQL timed out with error 110 | Kubernetes sets the iptables FORWARD policy to DROP, which silently discards Docker inter-container traffic. Error 110 (timeout) rather than 111 (refused) is the signature: packets vanish rather than being rejected | `iptables -P FORWARD ACCEPT`, or a targeted rule on the Docker bridge. Neither survives a reboot, and kube-proxy may reset it |
| 2 | Container reported Up while the service inside was not running | These images run an init script that polls MySQL and only then starts the real process. Docker and Kubernetes both report the wrapper, not the service | Check for the actual process (`kamailio -f`, `python3 apiService.py`) rather than trusting container status |
| 3 | Pods on flannel could not reach the compose network at all, though the host could reach it in 0.067 ms | Traffic between the CNI bridge and the Docker bridge was dropped before reaching any rule that would accept it; Docker 29 replaced the familiar isolation chains, and a Tailscale forward chain also sits in the path | Not resolved at the compose layer. Superseded by moving IMS into the cluster |
| 4 | MySQL container ran but mysqld never started | A hostPath volume was mounted over /var/lib/mysql. Docker named volumes copy the image's existing content in on first use; Kubernetes hostPath mounts do not, so the empty directory shadowed the database baked into the image | Do not mount over /var/lib/mysql. Persistence would need an init step that seeds the directory first |
| 5 | PyHSS API did not answer on 8080; apiService.py appeared as a zombie | The init script starts three PyHSS services in parallel and does not supervise them. Two race to create the schema, and the loser dies with `Table 'auc' already exists` rather than retrying | Restart apiService inside the container. Kubernetes restarts of the whole container also resolve it once the schema exists. A retry wrapper is the proper fix |
| 6 | P-CSCF logged repeated failures registering itself as an NF | Its 5G-mode config points at an SCP address from the compose network, which does not exist in this deployment | Point SCP_IP and NRF_IP at the cluster NRF |
| 7 | Test REGISTER returned 504 Server Time-Out, with the Contact header accumulating alias parameters on each pass until Kamailio reported a buffer overrun | The Request-URI was set to the P-CSCF's own IP. A UE sends REGISTER with the home domain as Request-URI, which the P-CSCF resolves onward to the I-CSCF on 4060; given its own address it forwards to itself in a loop | Request-URI must be the home realm, not the proxy address |
| 8 | ClusterIP did not answer ping, suggesting the Service was unreachable | kube-proxy forwards only the ports and protocols a Service declares; ICMP is never forwarded | Test a ClusterIP with TCP to a declared port, not with ping |

### Verified

The UE reaches the IMS through the `ims` PDU session: a request from inside the
UE pod via the ims tunnel to the PyHSS API returns 200, and the P-CSCF's SIP
port is open. The P-CSCF receives and parses SIP REGISTER correctly (source,
Contact and expiry all logged), DNS resolves the CSCF names to the pod with SRV
records pointing at 4060, and all three Kamailio instances are listening.

The authentication round-trip remains untested with a generic SIP client:
sipsak forces an SRV lookup for the realm that the UE pod's resolver cannot
satisfy. A handset constructs a correct IMS REGISTER natively, including the
IPsec security negotiation the P-CSCF expects, so that is the next test.

### Useful commands

```bash
# IMS pod and service
kubectl get pods -n free5gc -l app=ims
kubectl get svc -n free5gc ims

# check the services inside actually started
IMSPOD=$(kubectl get pods -n free5gc -l app=ims -o jsonpath='{.items[0].metadata.name}')
for c in icscf scscf pcscf; do
  kubectl exec -n free5gc $IMSPOD -c $c -- ps aux | grep -c "kamailio -f"
done

# PyHSS API through the Service
curl -s -o /dev/null -w "%{http_code}\n" http://10.99.99.99:8080/docs/

# if the API does not answer, restart it inside the container
kubectl exec -n free5gc $IMSPOD -c pyhss -- \
  sh -c "cd /pyhss/services; nohup python3 apiService.py >/tmp/api.log 2>&1 &"

# watch SIP arrive at the P-CSCF
kubectl logs -n free5gc $IMSPOD -c pcscf -f | grep -iE "REGISTER|INVITE|401|200 OK"

# UE side: both PDU sessions
UEPOD=$(kubectl get pods -n free5gc -l component=ue -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n free5gc $UEPOD -- ip -br addr show | grep uesimtun

# reachability from the UE over the ims tunnel (TCP, not ping)
kubectl exec -n free5gc $UEPOD -- curl -s -o /dev/null -w "%{http_code}\n" \
  --interface uesimtun1 http://10.99.99.99:8080/docs/
```


## Performance

Latency and throughput measured end-to-end through each RAN scenario, using
the UE's own PDU session — traffic genuinely traverses the full path from
the UE pod through the RAN (DU/gNB → CU, where applicable) into the core and
out via the UPF.

**Method**

```bash
# server on the host
iperf3 -s -D

# find the host bridge IP pods use to reach it (usually 10.244.0.1)
ip addr show cni0 | grep "inet "

# client inside the UE pod, source-bound to its tunnel so traffic
# actually goes through the RAN/core rather than any shortcut
UEPOD=$(kubectl get pods -n free5gc -l component=ue -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n free5gc $UEPOD -- iperf3 -c <host-cni0-ip> -B <ue-tunnel-ip> -t 10

# latency over the same tunnel
kubectl exec -n free5gc $UEPOD -- ping -I uesimtun0 -c 20 8.8.8.8

# stop the server when done
pkill iperf3
```

| Scenario | Throughput | Latency (RTT) | Notes |
|---|---|---|---|
| O-RAN + OAI | 320 Mbit/s sustained (10s) | ~9.7-10.1 ms to 8.8.8.8 | 473 TCP retransmits over the run, likely attributable to the ZMQ-simulated radio interface rather than the core or CU/DU split itself |
| v-CRAN + OAI | 339 Mbit/s sustained (10s) | ~9.8-12.6 ms to 8.8.8.8 | 441 TCP retransmits, same profile as O-RAN. HPA (target 70% CPU on the CU) watched live during the burst and stayed flat at 3-4% — the CU handles RRC/PDCP signalling only, so bulk UE throughput does not load it; scaling this CU needs concurrent registrations/handovers, not more data volume from one UE |
| H-CRAN + OAI (macro+small) | 292 Mbit/s sustained (10s) | ~9.8-11.9 ms to 8.8.8.8 (mean 10.2) | 415 TCP retransmits. Both cells attached simultaneously (confirmed via AMF gNB count), but the UE only ever uses the one it registered on — a second cell being present has no measurable effect on the active session

### F-RAN: edge vs. internet latency

F-RAN'''s value proposition isn'''t throughput — it'''s how much faster a
local edge service responds versus one reached over the normal path.
Measured from the same UE, back to back, on O-RAN+OAI as the base RAN
with F-RAN'''s edge DNN and MEC app layered on top (F-RAN is additive,
not a RAN choice of its own).

Ping couldn'''t be used for the edge target — it'''s a Kubernetes
ClusterIP, and ClusterIPs never answer ICMP (kube-proxy only forwards
real TCP/UDP traffic to declared ports). Used curl'''s TCP connect time
instead, which is the fairer comparison for "how fast can a client
start talking to this service" anyway.

| Path | TCP connect time (10 runs) |
|---|---|
| Edge DNN — local breakout to the MEC app, never leaves the node | ~0.6-2.0 ms, median ~0.8 ms |
| Internet DNN — same UE, normal path out | ~5.0 ms steady-state (first sample excluded: DNS + cold-connect overhead) |

Roughly a 6x latency advantage for the edge path, even on a single-node
testbed where the physical distance advantage a real deployment would
have doesn'''t exist. The gap here comes entirely from the edge session
never leaving the cluster'''s internal network, versus the internet
session'''s extra hop out through NAT.

### Capacity testing: where does load actually land?

The single-stream test above raised an obvious follow-up: since the CU
never scales under bulk UE traffic, what *does* limit throughput as load
increases — the DU's CPU, or something else? Repeated the v-CRAN+OAI test
with a heavier, longer burst (60s, 4 parallel TCP streams) while watching
both pods resource use live.

| Test | Throughput | Retransmits | DU CPU | CU CPU |
|---|---|---|---|---|
| 1 stream, 10s | 339 Mbit/s | 441 | not measured | 3-4% (idle) |
| 4 streams, 60s | 312 Mbit/s | 17,289 | 216m (~2.7% of 8 cores) | 18m (idle) |

Adding parallel streams made things *worse*, not better — throughput
dropped and retransmits rose by roughly 39x. Neither pod was anywhere
near its CPU ceiling (the DU has no resource limit set at all, and used
under 3% of the node regardless). That rules out Kubernetes resource
constraints as the bottleneck.

The constraint is the **ZMQ-simulated radio link** standing in for real
RF hardware — it does not handle concurrent high-throughput streams
gracefully, and the retransmit explosion is consistent with packets
queuing and dropping there rather than anywhere in the CU/DU/core path.
Worth stating plainly: this is a limit of the simulated radio interface
used for this testbed, not a limit of the cloud-native RAN/core design
itself.


