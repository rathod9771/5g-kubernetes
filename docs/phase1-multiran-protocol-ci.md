# Phase 1 — multi-RAN 5G protocol validation, baseline CI gate

**21 November 2026.** This branch delivers the first **parallel, GitHub-hosted,
offline** interface/configuration audit. It does **not** claim all architectures
can already complete 5G SA NAS registration, PDU session or user-plane traffic.

## Objective and scope

Evaluate actual supported procedures across the existing OAI/srsRAN C-RAN,
O-RAN, H-CRAN, vC-RAN, cloud-RAN and F-RAN scenario profiles. Compare:
- NGAP N2 SCTP / AMF connectivity and NG Setup;
- F1AP F1-C SCTP / F1 Setup between CU and DU;
- real UE NAS Registration Request/Accept/Complete;
- PDU Session Establishment Accept and actual UE address;
- traffic in F1-U and NG-U, with packet counters and end-to-end reachability.

An interface declaration in a Helm ConfigMap or Service is **not** proof of
a functioning protocol. This distinction is mandatory in reports.

## Phase 1A delivered: safe, parallel *static* protocol configuration audit

[Workflow](../.github/workflows/phase1-multiran-protocol-static.yml) uses
`scripts/experiments/phase1_protocol_matrix.py` to read the authoritative
`config/scenarios.json` registry, render the existing registered CU and DU
Helm charts with a synthetic **non-pullable** image reference, and inspect:

- CU N2/NGAP SCTP/38412 config+Service declaration;
- CU and DU F1AP SCTP/38472 config+Service declarations;
- optional declared F1-U DU and NG-U CU Service UDP ports;
- RFsim/ZMQ backend declaration;
- H-CRAN two distinct DU identifiers and no RIC/E2 agent in the rendered CU.

The matrix covers **13 registered cases**. Eleven have the registry's
`generation_status=ready` and `validation_policy.split_ran=true`. The
`oran-srsran` case is deliberately reported `REGISTRY_BLOCKED` because
the registry documents unresolved stable-AMF CU discovery; its historical
chart also uses a floating `:master` image and must **not** be promoted.
The vendor-independent `fran` is an edge-only chart, not a CU/DU split:
it is reported `NOT_SPLIT_RAN`. The `none` lifecycle case is omitted.

F-RAN OAI and srsRAN have CU/DU plus an additional fog component. This
Phase 1A audit only inspects their registered CU/DU pairs and explicitly
marks the fog component `FOG_NOT_PROTOCOL_TESTED`; fog/MEC testing is a
separate test plan.

The pipeline runs a 13-case job matrix with `max-parallel: 4`, plus offline
negative tests. It never pulls an image, starts Open5GS, contacts Kubernetes,
uses HP, performs SSH or changes a cluster. Only bounded JSON summaries are
stored, never ConfigMaps or credentials. Each job's result must be read as
`STATIC_CONFIG_PRESENT_RUNTIME_NOT_TESTED`, `CONFIG_BLOCKED`,
`REGISTRY_BLOCKED`, or `NOT_SPLIT_RAN`.
If an eligible CU/DU loses mandatory static NGAP or F1AP binding, its job
fails. Other static fields such as absent F1-U/NG-U Service declarations
are visible as gaps, not masqueraded as runtime passes.

## Phase 1B: real protocol runtime validation (NOT EXECUTED by this workflow)

Use manual, separately authorized disposable GitHub-hosted integration
environments only after exact-source offline acceptance and artifact pinning.
Initially prioritize one OAI C-RAN and one srsRAN C-RAN pair, followed by
O-RAN OAI, H-CRAN srsRAN and other profiles as their support is established.

The runtime harness must:
1. Pin exact image digests/source SHAs (including AMF/SMF/UPF, DB, gNB and UE)
   and create isolated, **synthetic** subscribers only.
2. Run a genuine 5G NR virtual-radio UE with a demonstrated compatible RF
   transport; a chart's ZMQ driver alone is not a complete virtual UE.
   **srsUE from legacy srsRAN 4G is not assumed to be a compatible NR SA UE.**
3. Capture NGAP/F1AP setup **from both peers**, successful NAS Registration,
   PDU Session Establishment, actual UE-assigned IP, and at least one
   bidirectional traffic sample with F1-U/NG-U evidence.
4. Fail on absent actual packet/procedure evidence, UE exit, protocol decode
   ambiguity, missing subscriber/invalid identity, or zero traffic.
5. Record independent, bounded `PASS`/`FAIL` with timestamps and pinned
   provenance. Do **not** conflate successful Docker builds, Helm renders or
   SCTP listener readiness with completed 3GPP procedures.
6. Only after initial end-to-end success, add controlled fault injection in
   research Phase 2. Do not restart AMF on the HP machine as part of Phase 1.

**Existing experimental evidence:** isolated H-CRAN OAI Stage5ZG
[successful run #44457384676](https://github.com/rathod9771/5g-kubernetes/actions/runs/44457384676)
qualified a *specific* frozen test-only OAI UE/CU/DU/5GC RFsim build:
2 independent sessions × 3 AMF stop/restart cycles each = **6/6
successful cycles** (and 18/18 negative-control rejections). Its exact
runtime-tested source commit is
`7fa911ece3b510b09351bf19c722007365ba47bd`. This is historical
bounded **H-CRAN OAI evidence only**, not a current multi-RAN PASS and not
production conformance. Do not auto-trigger that expensive live workflow.

## Safeguards and promotion

- Branch: `experiment/phase1-multiran-protocol-matrix-20261121`.
- Source parent: Stage5ZJ fully validated experimental-staging commit
  `b23a33f8623901c5e24d71ed6f3b9df10f0b1ff8`.
- Do **not** merge `main`, alter frozen Stage5ZG source, or edit existing
  deployment/recovery workflows for this static gate.
- HP is a **research machine**, not production; it is nonetheless suffering
  rotational-HDD/Longhorn IO pressure and is deliberately **not used** by
  hosted CI.
- Near-RT RIC/E2SM-KPM/RC are **not part of H-CRAN**; Prometheus and Grafana
  remain its monitoring path. O-RAN E2 is independently optional and is not a
  prerequisite for Release 15/16 NGAP/F1AP testing.
- **NO production GO** and **NO claim of runtime 3GPP conformance**.
