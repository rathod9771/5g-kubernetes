# H-CRAN srsRAN — 3GPP-oriented, GitHub Actions-only research qualification

**Research state (22 Nov 2026):** 3GPP *architecture/configuration audit* complete;
**dual-DU runtime NGAP/F1AP NOT VERIFIED; NAS/PDU/UE/Mobility NOT RUN**.
No production deployment, no HP system access, no OSM mutation. OAI H-CRAN is
a separate, frozen success record and is not retested or modified here.

## 1. What “based on 3GPP” means

**H-CRAN (heterogeneous cloud RAN) is a research topology label**, not a
separately specified 3GPP node or proof of a coordinated macro/small RAN.
Its actual protocol baseline is the Release-15/16 **NG-RAN split gNB**:

```text
Synthetic test Open5GS AMF  <-- NG-C/N2: SCTP38412, NGAP -->  one srsCU
Synthetic test Open5GS UPF  <-- NG-U/N3: UDP2152, GTP-U --> CU-UP (NOT TESTED)
                                                        |
                        +-- F1-C SCTP38472 / F1-U UDP2152 -- macro srsDU, DU 100, PCI 101
                        +-- F1-C SCTP38472 / F1-U UDP2152 -- small srsDU, DU 101, PCI 102
```

Standards boundaries, **not a claim of complete Release conformance**:

| Normative basis | Relevant function | Current code/config | Required runtime evidence |
|---|---|---|---|
| 3GPP TS 38.401 Rel-16 | one gNB-CU controls one/multiple gNB-DUs | one CU; max_nof_dus=2; two unique DUs | simultaneous, independently observed F1 connections |
| TS 38.470, 38.472, 38.473 Rel-15/16 | F1-C SCTP and F1AP Setup | F1-C port 38472, both DU IDs and independent IPs | two request+response exchanges, not one reused log line |
| TS 38.474 Rel-16 | F1-U GTP-U | per-DU address, UDP 2152 | per-UE per-DU UL/DL real G-PDUs and TEIDs |
| TS 38.410, 38.413 Rel-16 | N2 NGAP | common PLMN, TAC and slice, N2/SCTP38412 | real NGSetupRequest+Response |
| TS 23.501/23.502, 24.501 Rel-16 | registration, PDU sessions, QoS, NAS | test-core interface only | authentic NAS, security and session outcome per UE |
| TS 38.331, 38.211–214 Rel-16 | RRC and physical radio | distinct PCI and NR-ARFCN/numerology declarations | decoded RRC, compatible RF simulator, repeatability |
| TS 33.501 Rel-16 | authentication/security | core supports 5GS concepts | secure test subscriber and evidence of real NAS security |
| TS 28.552 Rel-16 | management KPIs | infrastructure Prometheus/Grafana | measurements grounded in actual protocol counters |
| Experimental orchestration | OSM/K8s workload lifecycle | retained canonical charts only | staged rollout/rollback; **not a 3GPP conformance test** |

The canonical charts are **not** changed in this stage. Render-only contract
tests plus offline auditing establish whether the declared parameters match
the research target. **No configuration-only test can prove RF, NAS or user-plane behavior.**

## 2. Audited actual repository parameters

All three objects use the **canonical** srsRAN Helm CU/DU implementations,
not the historical monolithic `helm/hcran-srsran` audit charts:

| Field | CU | Macro DU | Small DU |
|---|---|---|---|
| Helm override | `cu/values-hcran.yaml` | `du/values-hcran-macro.yaml` | `du/values-hcran-small.yaml` |
| ranType | hcran | hcran | hcran |
| gNB ID | 3585 | 3585 | 3585 |
| DU ID | — | 100 | 101 |
| PCI | — | 101 | 102 |
| sector_id | — | 1 | 2 |
| center NR-ARFCN | — | 632628, band n78 | 632628, band n78 |
| SCS | — | 30 kHz | 30 kHz |
| channel BW | — | 20 MHz | 10 MHz |
| tx_gain config | — | 75 | 35 |
| PLMN / TAC | 20893 / 1 | 20893 / 1 | 20893 / 1 |
| S-NSSAI | SST=1, SD=0x010203 | through common 5GC | through common 5GC |
| F1-C | binds CU Pod IP | CU IP / own Pod IP | CU IP / own Pod IP |
| F1-U | Pod IP/UDP2152 | own Pod IP/UDP2152 | own Pod IP/UDP2152 |
| Radio transport | — | ZMQ loopback ports 2000/2001 | separate namespace, same loopback ports |

The topology is explicitly **RIC/E2SM-free**: Near-RT RIC, E2AP, E2SM-KPM
and E2SM-RC are neither deployment dependencies nor acceptance requirements.
Prometheus/Grafana is retained for infrastructure monitoring but is not a
substitute for protocol-correctness observations.

**Known research limits:** shared center frequency with two different
bandwidths, nominal gains, separate ZMQ namespaces and distinct PCIs are
only configuration choices; they **do not demonstrate true macro/small RF
coverage, interference management, coordinated scheduling, or handover.**
The 10/20 MHz RU sample rates and a specific compatible NR SA ZMQ UE require
separate runtime validation. An existing LTE srsUE is **not** assumed to be
an NR SA UE; OAI RFsim cannot be connected directly to srsRAN ZMQ.

## 3. Stage H0 — static and negative evidence gates

Push to `experiment/hcran-srsran-3gpp-20261122` launches:

1. Python negative contract tests for independent CU/macro/small identities,
   duplicate IDs, gNB mismatch, PLMN/TAC/S-NSSAI/NR-ARFCN/SCS drift,
   F1-C/F1-U routing, observer safety, RIC/E2 exclusion, source pin and
   fail-closed live-evidence decision.
2. Helm-only rendering of **all three canonical H-CRAN profiles** with the
   local, nonpullable test image reference. No Kubernetes/OSM calls.
3. An **8-second disposable GitHub-hosted SCTP-capture preflight** to
   establish packet observer readiness. It uses `sudo -n dumpcap -w -`
   piped to a file descriptor **opened by the unprivileged runner**; TShark
   decodes only after capture. A green preflight is not a protocol PASS.
4. Full `Offline Project Validation` exact source SHA; manual runtime will
   refuse anything other than completed `SUCCESS` on the identical
   branch, SHA, workflow and own repository.

## 4. Stage H1 — one CU + TWO real DUs NGAP/F1AP runtime (manual)

`.github/workflows/hcransrs-research-manual.yml` is manual for **costly
runtime** and push-only for lightweight checks. Only the manual job starts
synthetic Open5GS NRF/AMF and source-built srsRAN nodes inside a private
GitHub-hosted Docker network. It builds from reviewed source commit
`11c9bbabb69873752500d676f55e0034f6caa5c5` in
`herlesupreeth/srsRAN_Project`; `ENABLE_ZEROMQ=ON`,
`ENABLE_UHD=OFF`, `ENABLE_DPDK=OFF`, `srscu` + `srsdu`.
The Ubuntu base digest, built binary SHA256, image ID and source pin are
retained. It **does not use the HP image bytes** or the host installer and
does not import to Kubernetes containerd or push images.

Acceptance: independent decoded SCTP wire packets with exact IP directions,
`NGSetupRequest` CU→AMF, `NGSetupResponse` AMF→CU, both
`F1SetupRequest` macro→CU and small→CU and both
`F1SetupResponse` CU→each, in request-before-response order; no decoded
NG or F1 setup failures. At the end, **AMF, NRF, CU, macro and small**
containers must all remain live, with three peer IPs (AMF + two DUs)
present in CU SCTP association state. Captures are local; only sanitized
frame counts, first frame numbers, PCAP SHA256 and source pins may be uploaded.

**One F1 connection is insufficient; one DU crash is failure.**
A passing H1 means `HCRANSRS_NGAP_BOTH_F1AP_WIRE_PASS_UE_NOT_TESTED`,
**not** end-to-end 5G SA or full 3GPP conformance.

Manual launch *only after* the most recent branch exact-SHA full offline
run is successful (replace RUN_ID with that actual run):

```bash
gh workflow run hcransrs-research-manual.yml \
  --repo rathod9771/5g-kubernetes \
  --ref experiment/hcran-srsran-3gpp-20261122 \
  -f offline_run_id=RUN_ID \
  -f confirm_isolated_no_deploy=ISOLATED_HCRAN_SRSCU_TWO_DUS_NO_DEPLOY
```

Do not launch multiple concurrent builds; a failed observer preflight
blocks compilation and must be resolved without bypassing the check.
Job timeout is bounded; no background work is promised.

## 5. Further research gates — distinct from H1

| Gate | Experiment | PASS requires | Present status |
|---|---|---|---|
| H2 | Compatible **5G NR SA** ZMQ UE and independent macro/small RF paths | pinned/buildable reviewed emulator, separated RF endpoints and genuine SIB1/RRC evidence | **BLOCKED until compatible UE demonstrated** |
| H3 | Two synthetic subscribers, one UE per cell | two real NAS Registration Accept, NAS security, PDU Session Establishment Accept; distinct IMSIs and UE PDU IPs | NOT RUN |
| H4 | F1-U and NG-U | independent binary-decoded GTPv1-U G-PDUs UL+DL per DU, nonzero TEIDs, 0-loss bounded ICMP | NOT RUN |
| H5 | UE RRC and inter-DU mobility | source-supported, commanded real serving-cell move macro↔small, PDU IP continuity, interruption measurement and 3GPP F1/RRC evidence | NOT RUN; may be BLOCKED by UE |
| H6 | QoS, rejection, release, failure recovery | F1 UE-context lifecycle, 5QI/QFI, NAS/PDU modify/release, negative paths, multiple bounded AMF/core failure repeats | NOT RUN |
| H7 | Repeatability/performance | n≥3 independent bounded runs, RTT/throughput/loss, load/CPU/memory/disk and evidence provenance | NOT RUN |
| H8 | Staging/rollback & research handover | reproducible installation, immutable image provenance, rollback observation, independent safety review | NOT RUN; separate approval required |

Implementing an acceptance gate is **not** a PASS. Never simulate or fabricate
UE protocol outcomes. If no compatible 5G SA ZMQ UE is available under an
appropriate research license, **stop at a qualified dual-DU signaling result
and disclose the limitation**. There is no scientifically valid shortcut.

## 6. Isolation and preservation

- **GitHub-hosted** `ubuntu-24.04` runners only. No HP/K8s cluster,
  OSM, dashboards, subscribers, physical SDR or real operator access.
- No writes to `main`, frozen OAI H-CRAN Stage5ZG source
  `7fa911ece3b510b09351bf19c722007365ba47bd`, or previous C-RAN
  srsRAN branch. No RIC removal from any running pod.
- No raw subscriber credentials, configs, logs or PCAP in artifacts.
- Image tags here are **research build outputs**, not production pull
  signatures or proof that HP's installed image digest is identical.
- Report unexpected compile/runtime errors as **FAIL/BLOCKED**, not PASS.

This runbook is a staged research plan and explicit evidence boundary.
Current conclusion remains **NO-GO for production, not full 3GPP compliance**.
