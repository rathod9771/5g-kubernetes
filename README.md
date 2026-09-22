# 5G Kubernetes Orchestrator

**Automated Platform for Various 5G Deployment Scenarios Based on Open Source**

A research platform (Amrita School of Engineering) that deploys a complete,
working 5G network — core, RAN, and UE — on a single-node Kubernetes cluster,
orchestrated end-to-end through Open Source MANO (OSM), with a web dashboard
and a CLI orchestrator for switching between RAN architectures.

The platform is built around three layers:

1. **Automated Deployment** — six RAN architectures × two RAN stacks,
   packaged as real OSM VNF/NS packages, deployed via one command or one
   click.
2. **Real-Time Network Intelligence** — live Prometheus/Grafana telemetry
   across the core and RAN: UE state, throughput, packet loss/retransmission,
   resource utilization, QoS, and latency.
3. **Autonomous Optimization** — a watcher that detects SLA degradation from
   Layer 2's own metrics and automatically triggers a RAN failover, through
   the same deployment path a human uses.

All three layers are implemented and live on the reference machine today.

---

## Reproducibility status

**Read this before anything else.**

This repository is being actively converted from a manually-operated
research platform into a reproducible one. Where it currently stands:

- **Verified, live, on the reference machine:** every script below
  (`scripts/preflight.sh`, `install.sh`, `orchestrator.sh`, `deploy.sh`, `status.sh`,
  `scripts/validate.sh`, `uninstall.sh`), the dashboard, the Layer 3 watcher,
  and real OSM deployments triggered through all of the above.
- **NOT yet verified:** a genuine clean-machine install, from `git clone`
  through a fully working deployment, has not been performed. Everything
  above has only been proven against this project's own long-running
  machine, which already has every dependency installed. See
  [`docs/reproducibility-checklist.md`](docs/reproducibility-checklist.md)
  for exactly what is and isn't confirmed.
- **Known, open limitation:** the standalone OAI UE (`deploy/oai-nr-ue/`)
  registers successfully against the real core (confirmed via
  `scripts/validate.sh`), but **PDU session establishment does not work** —
  a UPF-side PFCP bug, root cause identified but not fixed (see
  [`docs/troubleshooting.md`](docs/troubleshooting.md)). Do not read this
  platform as having a fully working 5G user plane via that UE. The
  long-verified UERANSIM flow (`helm/ueransim`) is the platform's actual
  proven end-to-end data-plane path.

---

## Architecture overview
                USER (dashboard click, or CLI)
                          |
             ┌────────────┴────────────┐
             |                          |
    RAN Selector Dashboard      orchestrator.sh / deploy.sh
    (ran-selector/, Flask)      (same underlying deploy path)
             └────────────┬────────────┘
                          ↓
                OSM (Open Source MANO)
          NBI terminate + instantiate calls
                          ↓
          ┌───────────────┴───────────────┐
          |                                |
     5G Core (open5gs)              RAN (1 of 11 packaged
     AMF/SMF/UPF/NRF/...            scenarios: 6 architectures
                                      × up to 2 stacks each)
          └───────────────┬───────────────┘
                          ↓
                Kubernetes (kubeadm)
                          ↓
    Prometheus + Grafana (Layer 2)   Layer 3 autonomous watcher
    native open5gs metrics +         reads Prometheus, calls the
    custom RAN log exporter +        same OSM deploy path above
    latency probe                    when SLA degrades

CI (GitHub Actions, self-hosted runner) keeps OSM's package catalog current
automatically on every push to `osm-packages/**`. Actually instantiating a
scenario is a separate, explicit step — a dashboard click, a CLI command, or
Layer 3's own automated trigger.

---

## Supported RAN implementations

| Stack | Status |
|---|---|
| **OpenAirInterface (OAI)** | Implemented and verified across all 6 architectures |
| **srsRAN Project** | Implemented and verified across all 6 architectures (plus a separate, bare-metal USRP B210 path — see [RF Tuning](#rf-tuning-for-over-the-air-operation-usrp-b210) below) |

## Supported deployment architectures

Six architectures, packaged as real OSM VNF/NS packages wrapping Helm charts —
11 total deployment scenarios (one architecture, F-RAN, is additive and has
no separate RAN-stack choice):

| Architecture | srsRAN | OAI |
|---|---|---|
| **Centralized RAN (C-RAN)** — monolithic gNB | `helm/srsran` | `helm/oai-cran` |
| **O-RAN** — CU/DU split over F1 | `helm/srsran-oran/{cu,du}` | `helm/oai/{cu,du}` |
| **Cloud RAN (C-RAN, cloud-native)** | `helm/cloud-ran-srsran` | `helm/cloud-ran-oai` |
| **Virtualized Cloud RAN (v-C-RAN)** — autoscaling CU | `helm/vcran-srsran` | `helm/vcran-oai` |
| **Heterogeneous Cloud RAN (H-CRAN)** — macro + small cell | `helm/hcran-srsran` | `helm/hcran-oai` |
| **Fog RAN (F-RAN)** — edge breakout, additive | `helm/fran-edge` (no stack choice) | |

Every scenario is a real, onboarded `osm-packages/<scenario>_knf` +
`<scenario>_ns` pair — see `./deploy.sh --list` for the exact scenario keys
the orchestrator and dashboard both use.

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

## Prerequisites

Checked automatically by `./scripts/preflight.sh` — run it before anything
else. What it verifies:

- Ubuntu 24.04 (built and verified on this; other distros/versions untested)
- 8+ CPU cores, 24+ GB RAM, 60+ GB free disk recommended (this stack has
  triggered `systemd-oomd` under combined memory pressure below that)
- `kubectl`, `helm`, `git`, `curl`, `python3`, `jq`, a container runtime
  (containerd or docker)
- Kernel modules: `overlay`, `br_netfilter`, `sctp` (the last is easy to
  miss and breaks NGAP/PFCP silently if absent)
- IPv6 disabled system-wide is recommended — enabled IPv6 has silently
  broken `raw.githubusercontent.com` downloads during OSM's install before
- Passwordless or interactive `sudo` available

---

## Repository structure

````
5g-kubernetes/
├── install.sh, deploy.sh, orchestrator.sh, status.sh, uninstall.sh   top-level entry points
├── config/global.env.example, config/global.env (gitignored)         parameterized machine values
├── scripts/preflight.sh, scripts/common.sh, scripts/validate.sh      read-only checks + shared helpers
├── osm-packages/                  24 OSM VNF/NS packages (11 scenarios + core + IMS)
├── helm/                          the underlying Helm charts each package wraps
├── deploy/ran-selector.service, rancher-portforward.service, oai-nr-ue/
├── ran-selector/                  dashboard: Flask backend + single-page UI
├── monitoring/                    Layer 2: Prometheus stack values, RAN exporter, latency probe
├── layer3-autonomous/             Layer 3: the SLA watcher + its systemd unit
├── docs/                          troubleshooting, OSM install notes, reproducibility checklist
└── usrp-gnb/                      bare-metal srsRAN for real USRP B210 hardware
```


---

## Configuration

Everything machine-specific lives in `config/global.env`, never hardcoded
and never committed:

```bash
cp config/global.env.example config/global.env
# edit config/global.env: it auto-detects HOST_IP and HOST_INTERFACE if left
# blank, and documents every other value (OSM IDs, PLMN, NodePorts, ...)
```

Every script (`scripts/common.sh`'s `load_config`) loads this file if
present, and falls back to `config/global.env.example`'s defaults (with a
warning) if it doesn't exist yet — so every script still runs on a fresh
clone, just without your machine's specific overrides.

---

## Installation

```bash
./scripts/preflight.sh    # checks this machine can run the platform; read-only
./install.sh              # idempotent: installs what's missing, reuses what's already there
```

`install.sh` checks, in order, and only installs what's actually missing:
Kubernetes (kubeadm), Longhorn, cert-manager, the monitoring stack
(kube-prometheus-stack + exporters), the dashboard (Python venv + systemd
service), and the Layer 3 watcher (systemd service).

**OSM itself is the one deliberate exception.** This project's OSM install
is a multi-step process with environment-specific patches (see
[`docs/OSM19_INSTALL.md`](docs/OSM19_INSTALL.md)) that hasn't been
re-verified as a one-shot script on a fresh machine. Rather than automate
something unproven, `install.sh` checks for OSM and, if it's missing, stops
with clear instructions pointing at that doc — "user action required," not
silently skipped.

Safe to run more than once: every step detects existing state first. Run
against this project's own already-fully-installed machine, every single
check correctly detects and reuses what's there — nothing gets reinstalled
or restarted unnecessarily.

---

## Deployment

**Interactive:**

```bash
./orchestrator.sh
```

Walks through architecture → RAN stack (skipped for F-RAN) → confirmation,
then hands off to `deploy.sh`.

**Non-interactive:**

```bash
./deploy.sh --list              # every valid scenario key
./deploy.sh cloudran-oai        # deploy a specific scenario
./deploy.sh hcran-srsran
```

Both paths call the exact same thing underneath: the RAN Selector
dashboard's own `POST /api/deploy` — the same endpoint a human clicking the
dashboard uses, and the same one Layer 3's watcher calls automatically.
One orchestration/deployment layer, not several different ones that could
drift apart.

Switching a running RAN is a real OSM terminate-and-instantiate, not a hot
swap — typically 1-3 minutes.

---

## Status and validation

```bash
./status.sh                        # quick glance
./scripts/validate.sh --verbose    # full breakdown with the actual evidence behind each line
```

`validate.sh` does real functional checks, never just "the pod is Running":
it greps actual gNB/UE logs for genuine NGAP/NAS events, checks live SCTP
association state for the RAN's AMF connection (immune to log rotation on
long-running pods — see `docs/troubleshooting.md` for why that matters), and
curls OSM/Prometheus/Grafana directly. It will honestly report the OAI UE's
PDU session as down — it does not paper over that.

---

## Uninstall

```bash
./uninstall.sh --help
./uninstall.sh --monitoring     # or --ran / --core / --platform / --all
```

Nothing runs by default — every flag requires typing `yes` at a
confirmation prompt naming exactly what will be removed. Normal use of this
repository never calls this script or does anything destructive.

---

## Dashboard

`ran-selector/` — Flask backend (port 8090, `ran-selector.service`) + a
single-page UI:

- **RAN Selector:** pick an architecture and stack, click Deploy — calls
  OSM's NBI directly (the same path `deploy.sh` uses)
- **Layer 2 panel:** live BLER, control-plane latency, HARQ retransmissions,
  MAC TX/RX bytes, AMF registrations — refreshing every 8s from Prometheus,
  with a link out to full Grafana dashboards (Grafana can't be embedded —
  it sends `X-Frame-Options`, the same wall Rancher hit)
- **Layer 3 panel:** live feed of autonomous actions the watcher has taken,
  with real OSM instance IDs, refreshing every 10s
- **NF inspection panels:** live logs and status for every open5gs NF
- **State recovery:** both the active RAN and the Layer 2/3 panel choice
  persist across page reloads — no dashboard-hopping
- **OSM panel:** OSM's own GUI embedded directly (no `X-Frame-Options` on
  OSM's side, so this one genuinely embeds)

### Documentation viewer

The dashboard's **Documentation** panel renders the RAN architectures deck as
slide images on the dashboard's own dark theme, with arrow-key navigation, a
thumbnail filmstrip, fullscreen, and links to the PDF and original `.pptx`.
Rendered assets are gitignored and regenerate from the deck:

```bash
cd ran-selector
./make-docs.sh                       # pptx -> pdf -> docs/slides/*.jpg
sudo systemctl restart ran-selector
```

---

## OSM integration

OSM 19, FluxCD-based installer, on the same cluster. 24 packages onboarded
(11 RAN scenarios × KNF+NS pairs, plus open5gs, IMS, and a composing
`full_stack_ns`). CI (`scripts/osm-onboard.sh`, a self-hosted GitHub Actions
runner) automatically re-onboards every package on a push to
`osm-packages/**`.

Self-signed certificate — accept the browser exception once before the
dashboard's embedded OSM panel will load.

Cross-VNF AMF discovery uses a stable alias Service (`amf-ngap-stable`,
label-selected so it survives any release-name change on core redeploy).
See [`docs/OSM19_INSTALL.md`](docs/OSM19_INSTALL.md) for the full install
notes and upstream quirks worth knowing.

---

## Kubernetes integration

kubeadm-bootstrapped, single control-plane node doubling as worker. Flannel
for pod networking. `install.sh` bootstraps this from scratch if no cluster
is reachable, including raising the kubelet's default max-pods limit
(`KUBELET_MAX_PODS` in `config/global.env`, default 200 — this stack's pod
count across core + RAN + monitoring can exceed the default 110 with more
than one scenario active).

---

## Monitoring / Prometheus / Grafana (Layer 2)

`install.sh` deploys `kube-prometheus-stack` (Prometheus + Grafana +
Alertmanager) with values tuned for this workload (`monitoring/kube-prometheus-stack-values.yaml`
— includes fixes for Grafana persistence and a Prometheus 3.x content-type
strictness issue, documented inline).

Three real telemetry sources feed it:

- **open5gs core NFs** (AMF/SMF/UPF/PCF) — native Prometheus metrics,
  3GPP TS 28.552-style, enabled via a real Helm chart fix (they ship the
  capability but it's off by default)
- **Custom RAN exporter** (`monitoring/ran-exporter/`) — OAI's gNB has no
  native Prometheus support, so this tails its log via the Kubernetes API
  and parses BLER/HARQ/SNR/RSRP
- **Custom latency probe** (`monitoring/latency-probe/`) — real TCP
  round-trip timing to the AMF's control-plane (SBI) service

---

## Layer 3 — Autonomous Optimization

`layer3-autonomous/watcher.py`, running as `layer3-watcher.service`,
continuously reads Layer 2's real BLER metric from Prometheus. Sustained
BLER above `LAYER3_BLER_THRESHOLD` (default 5%) is treated as SLA
degradation protecting a critical application, and triggers an automatic
failover to a more resilient scenario (`LAYER3_FAILOVER_SCENARIO`, default
`hcran-oai` — dual macro+small cell) by calling the exact same
`/api/deploy` path a human uses. A cooldown window
(`LAYER3_COOLDOWN_SECONDS`) prevents thrashing OSM with repeated redeploys.

Verified end-to-end with a forced-threshold test: real detection, real
`POST /api/deploy`, real OSM terminate+instantiate, real
`{"status":"success", "ns_instance_id": "..."}` response, logged as
structured JSON to `layer3-autonomous/actions.log` for the dashboard's
Layer 3 panel.

---

## Troubleshooting

[`docs/troubleshooting.md`](docs/troubleshooting.md) — real issues hit
building and operating this platform: the open PDU-session PFCP bug, the
gNB/AMF reconnect issue and its fix, two distinct log-reliability bugs found
while building `validate.sh`, and two historical infrastructure issues
(`k3s`/6443 port conflict, IPv6 breaking OSM's install) that
`scripts/preflight.sh` checks for.

---

## Known limitations

- **OAI standalone UE PDU session does not work.** Registration is real and
  verified; PDU session establishment fails on a UPF-side PFCP bug not yet
  root-caused to a fix. See Reproducibility status above and
  `docs/troubleshooting.md`.
- **Fresh-machine reproduction is not yet verified.** Every script and
  component has been proven live against this project's own long-running
  machine, which already has every dependency installed — not against a
  genuinely clean system. See `docs/reproducibility-checklist.md`.
- **`uninstall.sh --ran` and `--core`** don't yet call a direct OSM
  terminate-only endpoint (the dashboard's `/api/deploy` always
  terminates-and-replaces, it has no terminate-only mode) — they currently
  point you at the dashboard/OSM GUI instead.
- **USRP B210 hardware path** (`usrp-gnb/`) is separate from the
  containerized RFsim flow and has its own hardware-dependent setup not
  covered by any script here — see the RF Tuning section below.
- **F-RAN, v-C-RAN autoscaling, and IMS/VoNR** are real and demonstrated,
  but each has its own caveats documented in the relevant section below.

---

## Roadmap

- [x] Open5GS core on Kubernetes, full UE registration + internet (UERANSIM path)
- [x] 6-architecture × 2-stack RAN matrix, packaged as OSM packages
- [x] OSM 19 fully integrated, CI-driven package onboarding
- [x] Dashboard wired to OSM's NBI (not just a UI)
- [x] Layer 2: real-time network intelligence (Prometheus/Grafana + custom exporters)
- [x] Layer 3: autonomous SLA-triggered failover
- [x] Reproducibility scripts (preflight/install/deploy/orchestrator/status/validate/uninstall)
- [x] USRP B210 bare-metal srsRAN + real smartphone
- [ ] Fresh-machine reproduction test
- [ ] OAI standalone UE PDU session fix
- [ ] Kamailio IMS: full VoNR call flow (registration verified; call flow not yet)

---

## References

- Open5GS — https://open5gs.org
- Gradiant 5G charts — https://github.com/Gradiant/5g-charts
- srsRAN Project — https://www.srsran.com
- OpenAirInterface — https://openairinterface.org
- UERANSIM — https://github.com/aligungr/UERANSIM
- towards5gs-helm (Orange) — https://github.com/Orange-OpenSource/towards5gs-helm
- Open Source MANO — https://osm.etsi.org

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
10. **k3s and kubeadm both default to port 6443.** Whichever bound first won,
    and `kube-apiserver` crash-looped on the other — 125 restarts over 95 days.
    It surfaced as `bind: address already in use` in the apiserver logs, and
    from the outside as intermittent `kubectl` failures: connection refused,
    or `x509: certificate signed by unknown authority` (the cert error is
    misleading — it just means nothing valid was listening). Fixed with
    `sudo systemctl stop k3s && sudo systemctl disable k3s`. `./scripts/preflight.sh`
    checks for exactly this and only warns (not fails) when 6443 is in use by
    something it can't identify as the legitimate apiserver.
11. **A Helm templating bug silently broke NRF discovery.** `customOpen5gsConfig`
    was rendered with plain `toYaml`, which never evaluates template
    expressions — a hardcoded NRF hostname (missing the release-name prefix)
    silently pointed nowhere across 7 network functions. Fixed by wrapping the
    render in `tpl()`.
12. **`open5gs-dbctl`'s IMSI storage bug.** The community `open5gs-dbctl`
    tool stores subscriber IMSIs with the `imsi-` prefix baked into the
    database field itself, which UDR's own read queries never match against
    — invisible until checking UDR's logs directly for "Cannot find IMSI in
    DB" despite the record existing. Add subscribers with the bare numeric
    IMSI, not the `imsi-` prefixed form.
13. **`echo "$VAR" | grep -q PATTERN` under `pipefail`** can silently report
    no match on a large piped variable — `grep -q` exits the instant it
    finds a match, which can SIGPIPE the producing `echo` before it
    finishes writing, and `pipefail` then reports that non-zero SIGPIPE
    exit as the whole pipeline's status. Use a here-string
    (`grep -q PATTERN <<< "$VAR"`) instead.

---

## RAN Architecture Matrix — Network Configuration

| Parameter | Value |
|---|---|
| PLMN | 208 / 93 |
| TAC | 1 |
| Slice | SST 1, SD 0x010203 |
| Test subscriber | IMSI 208930000000003 |
| UE subnet | 10.45.0.0/16 (UPF `ogstun`, NAT to internet) |

---

## End-to-End Integration

### Connection Diagram (Control + User Plane)

Registration flow: UE → gNB (RRC) → AMF (NGAP) → AUSF → UDM → UDR → MongoDB
(5G-AKA authentication) → back down → Security Mode → Registration Accept →
SMF creates PDU session → UPF programs GTP tunnel → UE gets `uesimtun0` with
an IP from 10.45.0.0/16. (This full chain, through PDU session, is the
UERANSIM path — see Known limitations above for the OAI UE's PDU session
status.)

### Network Interface Diagram

Key design decision — **no Multus / static IPs anywhere**: every component
binds its own pod IP and reaches peers via ClusterIP service DNS
(`open5gs-amf-ngap`, `srsran-cu`, …), or, for the standalone OAI UE, via
dynamic pod discovery through the Kubernetes API (`deploy/oai-nr-ue/discover_gnb.py`)
since a plain Service can't safely front H-CRAN's two independent gNB pods.

```bash
# Pattern used in every RAN chart's init script:
export POD_IP=$(awk 'END{print $1}' /etc/hosts)
export AMF_IP=$(getent hosts open5gs-amf-ngap | awk '{print $1}')
sed -e "s|AMF_IP|${AMF_IP}|g" -e "s|POD_IP|${POD_IP}|g" template.conf > live.conf
```

(The free5gc-era platform used Multus `NetworkAttachmentDefinition`s with
ipvlan and static addressing — removed because ipvlan secondary interfaces
cannot reach ClusterIP services. See tag `free5gc-platform-v1` for that
model.)

### Errors Hit During Integration (and Fixes)

| # | Symptom (UE side) | Root cause found | Fix |
|---|---|---|---|
| 1 | `Cell selection failure ... [1] barred` forever | gNB never completed NG Setup — SCTP to AMF timing out | gNB had a Multus ipvlan interface; SCTP left via it and couldn't reach ClusterIP/pod IPs. Removed Multus |
| 2 | `SCTP bind failed: Cannot assign requested address` | gNB config still bound old static Multus IP after interface removed | Bind addresses → `0.0.0.0` (UERANSIM) / pod IP (srsRAN, OAI) |
| 3 | `SEMANTICALLY_INCORRECT_MESSAGE` reject, AMF log `HTTP response error [400/500]` on `nausf-auth` discovery | AUSF/UDM/UDR registered in NRF with built-in default PLMN 999/70; AMF serves 208/93, NRF treated discovery as roaming | `customOpen5gsConfig` with `serving: plmn_id 208/93` on AUSF, UDM, UDR |
| 4 | Random alternating errors, NFs losing NRF heartbeat every ~10s | Open5GS 2.7.0 NRF segfaulting under NF churn | Chart 2.2.6 / images 2.7.2 |
| 5 | `UE_IDENTITY_CANNOT_BE_DERIVED_FROM_NETWORK` | Stale GUTI from failed attempts + NF restarts mid-flow | Transient — cleared once the NF chain was stable |
| 6 | Registration + PDU session OK, but `ping -I uesimtun0` 100% loss | gNB advertised 0.0.0.0 as its GTP-U endpoint | gNB advertises its real pod IP at startup |
| 7 | srsRAN NG Setup rejected: `slice-not-supported` | Missing slice SD | Add SD — decimal `66051` for srsRAN, hex `0x010203` for OAI |
| 8 | UE `Authentication Failure due to SQN out of range` (once, then success) | Normal 5G-AKA sequence-number resync on first attach | None needed — resync is part of the protocol |

### End-to-End Verification Commands

For the UERANSIM path (this project's actual verified end-to-end data plane):

```bash
NS=<your OSM project namespace>  # or use OSM_PROJECT_NAMESPACE from config/global.env
AMFPOD=$(kubectl get pods -n $NS -l app.kubernetes.io/name=amf -o jsonpath='{.items[0].metadata.name}')
UEPOD=$(kubectl get pods -n $NS -l component=ue -o jsonpath='{.items[0].metadata.name}')

# ── 1. Core health
kubectl get pods -n $NS

# ── 2. NRF registry sane: AUSF discoverable with the right PLMN (must show 208/93)
kubectl exec -n $NS $AMFPOD -- curl -s --http2-prior-knowledge \
  'http://open5gs-nrf-sbi:7777/nnrf-disc/v1/nf-instances?target-nf-type=AUSF&requester-nf-type=AMF' \
  | grep -o '"plmnList":\[[^]]*\]'

# ── 3. gNB ↔ AMF (N2/NGAP)
kubectl logs $AMFPOD -n $NS | grep -E "gNB-N2 accepted|Number of gNBs"

# ── 4. UE registration + PDU session
kubectl logs $UEPOD -n $NS | grep -E "Initial Registration is successful|PDU Session establishment is successful|TUN interface"

# ── 5. User plane to the internet
kubectl exec -n $NS $UEPOD -- ping -I uesimtun0 -c 4 8.8.8.8
kubectl exec -n $NS $UEPOD -- curl --interface uesimtun0 -s -o /dev/null -w "%{http_code}\n" http://www.google.com
```

For the automated, real-status equivalent across the whole platform (both
UE stacks), use `./scripts/validate.sh --verbose` instead of running these
by hand.

---

## RF Tuning for Over-the-Air Operation (USRP B210)

Once real phones were registering, the remaining work was making the radio
link survive. This section records the RF-layer problems found while
driving a USRP B210 from a bare-metal srsRAN gNB (`usrp-gnb/`), separate
from the containerized RFsim flow above.

### Antenna constraint drove the band choice

The lab's antennas are Ettus VERT900, rated for 824-960 MHz and 1710-1990
MHz. Band n78 (3489 MHz) is far outside that range, so uplink SINR sat
between -14 and -31 dB and the link collapsed within about two minutes. The
gNB was retuned to band n3 (dl_arfcn 368500, 1842.5 MHz), inside the
antenna's upper range. Uplink SINR immediately improved to positive double
digits.

### Errors encountered and how they were resolved

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Uplink SINR -14 to -31 dB, RLF within ~2 min on band n78 | VERT900 antennas out of band at 3.5 GHz | Retune to band n3 (1842.5 MHz) |
| 2 | gNB log: Real-time failure in RF: underflow | Host not feeding IQ samples fast enough over USB | `otw_format sc12` plus deeper USB buffers (`num_recv_frames`/`num_send_frames`=512) |
| 3 | RLF, cause `MAC max KOs reached` | Uplink control channel not decoding | Two contributors, see rows 4 and 6 |
| 4 | `rsrp` reported `ovl`; SINR stuck at 2-5 dB | Receiver front-end overload: `rx_gain` too high (65) | Lower to 40; SINR rose to 20-26 dB |
| 5 | `phr` persistently negative | Handset already at max transmit power | Treat uplink as power-limited; fix reception instead |
| 6 | Negative timing advance, `rsrp` falling ~40 dB in one second (stationary handset) | B210 free-running oscillator drift | Discipline the clock with an external GPSDO reference. Open item |
| 7 | Raising `max_consecutive_kos` 100→400 barely helped | Link was degrading, not failing from a few unlucky errors — consistent with drift | Config tolerance isn't a substitute for clock discipline |
| 8 | Link survived 60-90s before the gain fix, only 5-10s after | Better SINR → higher MCS → less timing-error tolerance | Cap `max_ue_mcs` to trade throughput for resilience |
| 9 | gNB froze during radio init | Piped through `tee`; pipe buffer filled, stalled a real-time thread | Never pipe the live gNB — it writes its own logfile |
| 10 | `uhd_usrp_probe`: no devices found for `subdev`/`clock_source` | Those are runtime settings, not device-discovery args | Don't put them in `--args`; let srsRAN set them via its own config |
| 11 | Phones stopped attaching, no new AMF log activity | Helm upgrade/AMF restart tears down the gNB's NGAP association; srsRAN doesn't reconnect on its own | Restart the bare-metal gNB after any AMF restart |

### Working parameters at time of writing

| Parameter | Value | Note |
|---|---|---|
| band | 3 | Matches VERT900 antenna range |
| dl_arfcn | 368500 | 1842.5 MHz downlink, 1747.5 MHz uplink (FDD) |
| channel_bandwidth_MHz | 10 | Eases USB and CPU load |
| common_scs | 15 | Required for band n3 |
| srate | 15.36 | Must be valid for the PRACH configuration |
| tx_gain | 60 | Reduced from 80 |
| rx_gain | 40 | Reduced from 65 to clear receiver overload |
| otw_format | sc12 | Cuts USB bandwidth |
| device_args | num_recv_frames=512, num_send_frames=512 | Deeper USB buffering |

### Diagnostic commands

```bash
sudo ~/srsRAN_Project/build/apps/gnb/gnb -c ~/usrp-gnb/gnb_b210.yml
grep csi1 /tmp/gnb_b210.log | grep -oE "sinr=[-0-9.]+dB" | tail -20
grep -iE "RLF|KOs|underflow|late" /tmp/gnb_b210.log | tail -20
sudo uhd_usrp_probe | grep -iE "B210|USB"
```

### Interpreting the failure signature

A weak link degrades gradually: SINR sags, block errors climb, then the
connection drops. A drifting clock fails abruptly with SINR still high, and
leaves a fingerprint in the timing advance column (negative or erratic).
Recognizing the second pattern is what redirected the work from gain and
antenna tuning to clock discipline.

---

## IMS (Kamailio) — VoNR

VoNR requires an IMS alongside the 5G core: Kamailio in three roles
(P-CSCF, I-CSCF, S-CSCF), PyHSS with a MySQL backend, rtpengine for media,
and a DNS server. `helm/ims` runs all seven components in a single pod —
the compose-based version failed on flannel-to-Docker-bridge networking, and
one shared pod IP (via `status.podIP`) sidesteps the addressing problem
Kubernetes' dynamic pod IPs create for the upstream init scripts.

### Verified

The UE reaches the IMS through a dedicated `ims` PDU session: the PyHSS API
returns 200 through it, and the P-CSCF's SIP port correctly receives and
parses SIP REGISTER (source, Contact, expiry all logged). The
authentication round-trip with a real handset (native IMS REGISTER,
including IPsec negotiation) is the next test — a generic SIP client
(`sipsak`) can't complete it because it forces an SRV lookup the UE pod's
resolver can't satisfy.

### Errors encountered and how they were resolved

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Compose containers Up, but CSCFs/PyHSS never started; MySQL timeouts (error 110) | Kubernetes sets iptables FORWARD to DROP, silently discarding Docker inter-container traffic | Not resolved at the compose layer — superseded by moving IMS into the cluster |
| 2 | Container "Up" but the service inside wasn't running | Init scripts poll MySQL before starting the real process; container status reflects the wrapper | Check for the actual process, not container status |
| 3 | Flannel pods couldn't reach the compose network at all | Traffic dropped between the CNI and Docker bridges before any accepting rule | Superseded by moving IMS into the cluster |
| 4 | MySQL container ran but `mysqld` never started | hostPath volume shadowed the image's baked-in `/var/lib/mysql` content | Don't mount over `/var/lib/mysql` without seeding it first |
| 5 | PyHSS API didn't answer; `apiService.py` appeared as a zombie | Init script starts 3 PyHSS services in parallel unsupervised; one loses a schema-creation race and dies | Restart `apiService` inside the container; a retry wrapper is the proper fix |
| 6 | P-CSCF logged repeated NF registration failures | Its config pointed at a compose-network SCP address that doesn't exist here | Point `SCP_IP`/`NRF_IP` at the cluster NRF |
| 7 | Test REGISTER returned 504, Contact header accumulating aliases until Kamailio buffer-overran | Request-URI was set to the P-CSCF's own IP, creating a forwarding loop to itself | Request-URI must be the home realm, not the proxy address |
| 8 | ClusterIP didn't answer ping | kube-proxy only forwards declared ports/protocols; ICMP is never forwarded | Test a ClusterIP with TCP to a declared port, never ping |

---

## Performance

Latency and throughput measured end-to-end through each RAN scenario, using
the UE's own PDU session — traffic genuinely traverses the full path from
UE through RAN into the core and out via the UPF.

**Method**

```bash
iperf3 -s -D   # on the host
ip addr show cni0 | grep "inet "   # find the bridge IP pods use to reach it

UEPOD=$(kubectl get pods -n <ns> -l component=ue -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n <ns> $UEPOD -- iperf3 -c <host-cni0-ip> -B <ue-tunnel-ip> -t 10
kubectl exec -n <ns> $UEPOD -- ping -I uesimtun0 -c 20 8.8.8.8
```

### All ten combinations

Five architectures across both RAN stacks, one identical procedure per run.

| Architecture | srsRAN Mbit/s | srsRAN RTT | srsRAN retr | OAI Mbit/s | OAI RTT | OAI retr |
|---|---|---|---|---|---|---|
| C-RAN | 205 \* | 8.84 ms | 339 | 287 | 9.04 ms | 463 |
| O-RAN (CU/DU over F1) | 215 | 8.23 ms | 313 | 302 | 8.17 ms | 419 |
| Cloud-RAN | 199 | 8.69 ms | 253 | 295 | 9.63 ms | 400 |
| H-CRAN (macro + small) | 147 | 8.35 ms | 195 | 304 | 8.34 ms | 402 |
| v-CRAN (autoscaling CU) | 222 † | 9.95 ms | 337 | 343 † | 8.53 ms | 500 |

\* C-RAN + srsRAN drives a real USRP B210 rather than the ZMQ simulator, so
it isn't directly comparable to the other nine.
† Re-measured after the sweep — all ten ran back-to-back in one session
without tearing down each scenario, so later runs were measured on a
progressively busier machine. **Read these as indicative, not definitive.**

Across every architecture OAI outperformed srsRAN by roughly 1.3-2x on this
testbed — consistent enough to note, though the same load caveat applies.

### Benchmarking

```bash
./bench-all-ran.sh            # all ten, roughly 90-110 minutes
./bench-all-ran.sh oran-oai   # or a subset
```

A single RAN scenario takes this node from ~4.5 load to 30+; the script
tears down the last scenario when it finishes to avoid an unbounded CPU
leak.

### F-RAN: edge vs. internet latency

| Path | TCP connect time (10 runs) |
|---|---|
| Edge DNN — local breakout, never leaves the node | ~0.6-2.0 ms, median ~0.8 ms |
| Internet DNN — same UE, normal path out | ~5.0 ms steady-state |

Roughly a 6x latency advantage for the edge path, entirely from never
leaving the cluster's internal network — even without the physical-distance
advantage a real deployment would add.

### Capacity testing

| Test | Throughput | Retransmits | DU CPU | CU CPU |
|---|---|---|---|---|
| 1 stream, 10s | 339 Mbit/s | 441 | not measured | 3-4% (idle) |
| 4 streams, 60s | 312 Mbit/s | 17,289 | ~2.7% of 8 cores | idle |

Neither pod was anywhere near its CPU ceiling. The constraint is the
ZMQ-simulated radio link, not Kubernetes resource limits — worth stating
plainly as a limit of this testbed's simulated radio, not of the
cloud-native design itself.
