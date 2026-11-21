# Phase 1B1 — one real OAI NR RFsim UE with C-RAN OAI/Open5GS (research)

Created **21 November 2026**, after successful [Phase 1B0 actual
C-RAN NG Setup/F1 Setup](https://github.com/rathod9771/5g-kubernetes/actions/runs/44871454425).
That run had real CU-side NG Setup/F1 Setup markers and an independently
verified sanitized receipt. **It did not run any UE.**

## What Phase 1B1 adds

A bounded, isolated one-UE 5G SA protocol and data-plane acceptance gate:
- one source-pinned, test-only OAI W13 NGAP-patched CU;
- one OAI W45 DU with `ran-type: cran`, DU ID 100, 5G NR RFsim;
- one source-pinned, test-only OAI W45 NR UE using a previously tested
  Stage5ZG builder (NOT legacy srsUE);
- isolated Open5GS AMF/NRF/SMF/UPF/AUSF/UDM/UDR/PCF/NSSF/SCP and
  MongoDB from the reviewed frozen Stage4K transport harness;
- a strictly **synthetic** example subscriber created only inside the
  runner and never sourced from HP, OSM or production systems.

Actual runtime acceptance requires **all** of:
1. UE/CU/DU/5GC running, NG Setup Response from AMF, F1 Setup Request
   and F1 Setup Response for DU 100;
2. genuine UE NAS Registration Accept and PDU Session Establishment
   Accept from UE runtime logs;
3. exactly one UE TUN `oaitun_ue1` with an actual 10.45.x.x/16 address;
4. 4/4 actual ICMP echo replies from UE TUN to test UPF gateway 10.45.0.1;
5. independent GTPv1-U PCAP decode showing real nonzero-TEID user data
   G-PDUs in **F1-U UL/DL and NG-U UL/DL**;
6. matching unmodified runner events and independent packet decoder
   counters and PCAP SHA256 digest; no missing or repeated milestones.

The actual 3GPP message checks are through tested runtime UE/CU logs,
while F1-U/NG-U G-PDUs use an independent binary packet decoder. This
is a *bounded experimental procedure validation*, **not** exhaustive
3GPP Rel-15/16 conformance or formal protocol decoder conformance.

## Isolation and preservation

- Branch: `experiment/phase1b1-cran-oai-single-ue-20261121`;
  separate from successfully qualified Phase 1B0 source
  `d5b80e33414f619dbd38c4fd110746a2e7de4ebb`.
- Tests run on GitHub-hosted disposable `ubuntu-24.04` Docker bridge.
- `push` triggers **offline negative tests only**, never live Docker.
- Manual `workflow_dispatch` requires exact-source full Offline Project
  Validation **SUCCESS**, same SHA, same branch, same repository and
  explicit confirmation `ISOLATED_CRAN_ONE_UE_NO_DEPLOY`.
- No HP Kubernetes/Longhorn/OSM, kubectl/SSH, actual subscribers, RIC/E2,
  AMF fault injection, production credentials or image registry write.
- No upload of subscriber keys, raw core/UE logs, rendered ConfigMaps,
  packet captures or full temporary runtime state. Only sanitized receipt,
  source refs and actual local Docker image IDs can be retained.
- Test-only image tags are source-built/pinned and image IDs captured;
  this is NOT digest-pinned production hardening. Research status NO-GO
  for production.
- Original H-CRAN Stage5ZG 6/6 recovery evidence and frozen source
  `7fa911ece3b510b09351bf19c722007365ba47bd` remain untouched.
- For clarity, the reviewed historical Stage4K test code calls its one
  DU role `macro` internally. The new subclass verifies that the
  Helm-rendered **CU/DU Deployments and labels are `cran`** and uses
  **only one C-RAN DU**, not the H-CRAN macro/small topology.

The runner reuses the **frozen** Stage4K/Stage4L 5GC/NR-UE/GT-PDU code
at commit `4080110a74bf6ba58804797c5b7216458e2f0a93`, with a
C-RAN-specific Helm profile and isolated Docker network name.
The original source-pinned OAI Stage3 CU and W45 Stage5ZG UE builders
are reproduced (not byte-identical prior archives), using verified
upstream source pins and reviewable patches; no expired GitHub artifact
is used. This is a separate, no-fault **first-attachment** study.

## Gate invocation (after exact-commit full regression PASS)

```bash
gh workflow run phase1b1-cran-oai-single-ue-manual.yml \
  --repo rathod9771/5g-kubernetes \
  --ref experiment/phase1b1-cran-oai-single-ue-20261121 \
  -f offline_run_id='<successful full offline run from latest exact HEAD>' \
  -f confirm_isolated_no_deploy=ISOLATED_CRAN_ONE_UE_NO_DEPLOY
```

Never substitute the previous Phase 1B0 run ID: different branch/SHA.
Manual workflow may be dispatched only after actual same-source offline
PASS, not on queued/in-progress/failed CI. The source build may consume
substantial runner resources; do not start duplicate trials.

## Interpretation

- On `ONE_UE_NAS_PDU_TUN_F1U_NGU_ICMP_RUNTIME_PASS`: record the actual
  supported first-attach/RFsim result with source sha and PCAP digest.
  This is one UE + one DU. Multi-UE, mobility, AMF failure recovery,
  srsRAN interoperability, real USRP SDR, HP performance and formal
  Rel-15/16 conformance remain **NOT_RUN**.
- On any failed source build, test, missing log milestone, missing UE
  TUN IP, packet loss, or absent UL/DL G-PDUs: **FAIL**; preserve only
  bounded non-sensitive diagnostics; no false PASS.
- The legacy standalone **srsUE** is not assumed compatible with 5G
  NR SA virtual-radio reception. srsRAN will require a separately
  proven RFsim/ZMQ interconnect and NR UE candidate in Phase 1B2.

Tracking: [Issue #16](https://github.com/rathod9771/5g-kubernetes/issues/16).
