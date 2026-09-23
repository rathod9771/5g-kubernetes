# Reproducibility Checklist

What's actually confirmed working, what isn't yet, and what's a known,
permanent limitation. Every item under **A** was verified live against
this project's own reference machine during this session — see the git
history for the exact commands and their output. Nothing under **A** is
assumed; if it isn't listed there, it hasn't been proven.

---

## A. Verified on the current (reference) machine

- [x] Ubuntu 24.04, 8 cores, 31 GB RAM, 742 GB free disk — `./scripts/preflight.sh` passes
- [x] Required commands present: `kubectl`, `helm`, `git`, `curl`, `python3`, `jq`, `containerd`
- [x] Kernel modules present: `overlay`, `br_netfilter`, `sctp`
- [x] IPv6 disabled system-wide
- [x] Existing Kubernetes cluster (kubeadm v1.29.15) detected and reused by `install.sh`, not reinstalled
- [x] Longhorn detected and reused
- [x] cert-manager detected and reused
- [x] OSM 19 detected and reused; 24 packages onboarded (11 RAN scenarios × KNF+NS, plus open5gs/IMS/full_stack)
- [x] Open5GS core: AMF/SMF/UPF/NRF/AUSF/UDM/UDR/PCF/BSF/NSSF all `1/1 Running`, real SCTP association to a real gNB (`ss -a` shows `ESTAB` on :38412)
- [x] Helm charts underlying all 11 RAN scenarios exist and are the packages OSM actually deploys from (not separate/duplicate implementations)
- [x] kube-prometheus-stack detected and reused; Prometheus/Grafana both reachable
- [x] Custom RAN exporter (`monitoring/ran-exporter/`) running, real BLER/HARQ/SNR/RSRP metrics confirmed flowing
- [x] Custom latency probe (`monitoring/latency-probe/`) running, real TCP round-trip data confirmed flowing
- [x] `ran-selector.service` (dashboard) detected and reused; `/api/scenarios` returns real data
- [x] `layer3-watcher.service` detected and reused; live BLER evaluation confirmed in its own logs every 10s
- [x] `config/global.env.example` parameterizes every machine-specific value found in the audit (Phase 4 below)
- [x] OAI standalone UE (`deploy/oai-nr-ue/`): init container correctly discovers the currently-active gNB pod via the Kubernetes API (no hardcoded IP), prefers the `cell-tier=macro` pod when more than one gNB exists — confirmed after a real scenario switch mid-session
- [x] OAI standalone UE registration: `Received Registration Accept`, confirmed via `./scripts/validate.sh` reporting `UE Registration: READY`
- [x] `./deploy.sh --list` — correct real scenario keys
- [x] `./deploy.sh <scenario>` — two real OSM terminate+instantiate operations performed live, both returned real `ns_instance_id` values
- [x] `./orchestrator.sh` — walked interactively end to end, correctly built the scenario key, correctly handed off, resulting deployment succeeded
- [x] `./status.sh` — correct quick-glance output, reusing `validate.sh`'s detection rather than duplicating it
- [x] Istio: control plane (istio-base + istiod) installed via `install.sh`, isolated in istio-system, zero disruption to the 5G core
- [x] Istio sidecar injection on the 5G core: staged live test (appProtocol: http2 fix first, then one low-risk NF, then remaining NFs, AMF/SMF last) -- 8/9 core NFs (all but UPF, deliberately excluded) carrying a genuine istio-proxy sidecar, confirmed via the dashboard's real /api/istio/status. The previously-documented SBI HTTP/2 mishandling did NOT recur. The real, working PDU session survived every injection stage -- confirmed via ./scripts/validate.sh after each one

- [x] `./scripts/validate.sh --verbose` — every check does real functional verification (log greps for actual protocol events, live SCTP socket state, HTTP calls), not just "pod is Running"
- [x] `./install.sh` — every single check (Kubernetes through Layer 3 watcher) correctly detected existing state and skipped, zero disruption to the already-running platform
- [x] `./uninstall.sh --help` and a real, declined `--monitoring` confirmation prompt — the safety gate genuinely works, nothing was removed
- [x] Layer 3 watcher's full loop — forced-threshold test, real detection → real `POST /api/deploy` → real OSM deployment → real structured JSON logged to `actions.log`
- [x] Dashboard's Layer 2 and Layer 3 panels — live data confirmed rendering in the browser
- [x] `git`, syntax checks (`bash -n`) on every script before and after each change in this session

## B. NOT yet verified on a genuinely clean machine

- [ ] `git clone` on a machine with nothing pre-installed, through `./scripts/preflight.sh` reporting a clean pass with no pre-existing infrastructure
- [ ] `./install.sh` actually bootstrapping Kubernetes from scratch (kubeadm init, Flannel) rather than detecting an existing cluster
- [ ] `./install.sh` actually installing Longhorn, cert-manager, and the monitoring stack from scratch rather than detecting existing releases
- [ ] `./install.sh` actually creating the dashboard's Python venv and installing `ran-selector/requirements.txt` from scratch, and installing/starting the two systemd services for the first time (both were reused, not created, on the reference machine)
- [ ] OSM's own install (`docs/OSM19_INSTALL.md`) as a from-scratch procedure on a machine that's never had it — the reference machine's OSM install predates this reproducibility effort and was never re-run
- [ ] Any RAN scenario's *first-ever* instantiation on a machine with an empty OSM catalog (all 24 packages on the reference machine were already onboarded)
- [ ] Multus — referenced in `docs/architecture.md`-style discussion of the RAN charts' networking, but the current model (see README's "Network Interface Diagram") explicitly uses no Multus / static IPs; not exercised as part of this reproducibility work
- [ ] The full benchmark suite (`bench-all-ran.sh`) has not been re-run this session; its numbers in the README predate this work and are not re-verified here
- [ ] A genuine fresh `helm install`/OSM redeploy of the open5gs charts with the new Istio appProtocol/podLabels changes baked in has not been re-run -- the live cluster only ever received these as ad-hoc kubectl patches, then had the equivalent changes added to the chart source afterward. The two are believed equivalent but not re-proven end to end.

## C. Known limitations (not gaps to close — permanent, documented facts)

- **OAI standalone UE's PDU session does not work.** UPF logs
  `cannot handle PFCP message type[50]`, a real bug, root cause identified
  but not fixed. Registration is genuinely verified; the data plane through
  this specific UE is not. The long-verified UERANSIM flow remains the
  platform's actual proven end-to-end path. See `docs/troubleshooting.md`.
- **`uninstall.sh --ran` / `--core`** don't call a direct OSM terminate-only
  endpoint — the dashboard's `/api/deploy` has no terminate-only mode, so
  these two currently point at the dashboard/OSM GUI instead of acting
  directly.
- **USRP B210 hardware path is permanently separate** from the containerized
  RFsim flow this reproducibility work covers — it has its own
  hardware-dependent setup (`usrp-gnb/`, real antennas, a real B210) that no
  script here installs or configures.
- **IMS/VoNR call flow is not complete** — registration through the IMS PDU
  session is verified; a full authenticated call has not been tested with a
  real handset.

---

## How to extend this checklist

When something moves from B to A, it should be because it was actually run
and its real output checked — not because it "should work." When adding a
new item, match the existing entries' standard: a specific, falsifiable
claim (a command, a log line, a returned value), not a vague "works."
