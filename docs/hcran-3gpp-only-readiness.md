# H-CRAN — 3GPP-only acceptance and monitoring (no Near-RT RIC)

**Status:** Experimental. **Deployment:** NO-GO. **Scope:** H-CRAN profiles
`hcran-oai` and `hcran-srsran` only. The O-RAN scenario `oran-oai`,
its experiments, and historical artifacts are not changed or deleted.

## Architectural decision (13 November 2026)

- Follow 3GPP Release 15/16 NG-RAN gNB-CU/gNB-DU architecture.
- One CU; macro DU 100 and small DU 101 have distinct cell identities,
  share the same 5GC and communicate over F1-C (SCTP) and F1-U (GTP-U).
- CU uses NG-C/N2 (NGAP/SCTP) toward Open5GS AMF and NG-U/N3
  (GTP-U/UDP) toward Open5GS UPF.
- **Near-RT RIC, FlexRIC, E2AP, E2SM-KPM and E2SM-RC are excluded from
  H-CRAN deployment and H-CRAN acceptance requirements.** They are not
  required by 3GPP TS 38.401 for this topology.
- Existing Prometheus/Grafana remains the **monitoring stack**. It is
  independent of the 3GPP protocols, and is not a substitute for 3GPP
  protocol test evidence.
- The canonical Helm CU templates fail if an H-CRAN chart is rendered with
  `e2Agent.enabled=true`, `ric.enabled=true`, or `e2.enabled=true`.
  Rendered ConfigMaps contain no `e2_agent` section. This is source-level
  protection only: **we have not audited or modified a running HP CU Pod**.

Reference: 3GPP TS 38.401 §6.1.1 (Release 16), TS 38.470–38.474 (F1),
TS 38.410–38.413 (NG), TS 38.331 (RRC), TS 24.501 (NAS),
TS 23.501/23.502 (5GS/PDU), TS 28.552 (measurement definitions).

H-CRAN is a **research deployment label**, not a separately standardized
3GPP network function. Two simultaneous DUs and UEs establish a working
multi-DU topology, but do not prove inter-cell coordination or full Release
15/16 conformance.

## Existing bounded GitHub-hosted acceptance evidence

| Capability | Source | Result and limit |
| --- | --- | --- |
| Dual F1-C | Stage 4D run 43162695243 | Passed isolated F1 setup |
| NGAP AMF retry | Stage 3 run 43157479388 | Passed bounded NGAP retry |
| One UE NAS/PDU | Stage 4K run 43190810958 | Passed simulator NAS/PDU |
| Bidirectional F1-U and NG-U | Stage 4L run 43192172149 | Passed GTP-U/ICMP |
| Dual UEs / dual DUs | Stage 4O run 43194751671 | Two IPs, 6/6 pings per UE |
| Short dual-UE stability | Stage 4T run 43224919656 | ~97-second window |
| AMF fault with auto UE self-recovery | 43219517357 | **FAILED**: no service within 150s |
| Supervisor-assisted synthetic UE restart | Stage 4U run 43367213888 | Passed assisted recovery only |

These are **isolated CI trials**. The report's `PASS` denotes successful
historical GitHub run + matching non-expired artifact, not exhaustive re-review
of every captured packet. Artifact retention is short. Reproduce on a
qualified staging topology before promoting an image.

The prior E2 and KPM GitHub Actions runs are historical research records,
not H-CRAN acceptance prerequisites, and are no longer counted by
`scripts/ci_oai_ngap_readiness.py`.

## Open 3GPP-oriented acceptance work

| ID | Standard/procedure | Required verifiable outcome |
| --- | --- | --- |
| A1 | TS 38.473 / 38.331, intra-gNB inter-DU mobility | UE moves macro→small and small→macro; F1AP context transfer, new cell, retained PDU IP and measured user-plane interruption |
| A2 | TS 38.473 F1AP context | Successful UE Context Setup/Modification/Release, negative causes; timestamps and correlated F1-C captures |
| A3 | TS 38.331 RRC | Reconfiguration, radio link failure and re-establishment behavior, no false automatic recovery claim |
| A4 | TS 24.501 / TS 23.502 | NAS security, registration rejection/retry/deregistration and PDU Modification/Release with expected outcomes |
| A5 | TS 23.501 / 23.502 QoS | 5QI and QFI-to-DRB mapping lifecycle, validated *using normal 5GC/NGAP/RRC/F1*, **not E2SM-RC** |
| A6 | TS 38.211–38.214 | NR radio timing, numerology, RF/link behavior under repeatable channels; simulation is not physical-layer conformance |
| A7 | 3GPP traffic/service resilience | AMF-only and full-core outages with unassisted NAS/PDU restoration across repeated cycles; negative failures reported |
| A8 | Sustained performance | Multiple runs with throughput, RTT, packet loss, PDUs, restarts, CPU/PSI and disk-I/O pressure monitored for fixed observation windows |
| A9 | OSM staging lifecycle (orchestration, not 3GPP conformance) | Actual staging upgrade and rollback with immutable image digest, recovered service, reviewed signoff |

**Do not infer that A1/A2/A3/A4/A5 are supported just because packets can
be exchanged under F1/NG.** Source capability inspection and disposable
runtime tests are mandatory before stating a pass.

### Proposed next experiment: Stage 4W intra-gNB inter-DU mobility preflight

1. Inspect pinned OAI CU/DU source for supported intra-CU DU-to-DU
   mobility and target-cell selection (handover vs reselection). RRC
   Reconfiguration, F1 UE Context Modification/Setup and DRB tunnel
   migration must be real, not constructed from static logs.
2. Validate two independent cells with compatible PLMN, TAC, S-NSSAI,
   NR-ARFCN, numerology and independent RF paths.
3. Generate one controlled **disposable** UE mobility event and capture
   synchronized CU, both DU, UE, N2, F1-C, F1-U and N3 evidence.
4. Require before/after serving cell identity, preserved PDU service,
   measured interruption, no traffic duplication and full cleanup on
   failure. Repeat in both directions with negative scenarios.
5. If the pinned simulator cannot support physical mobility / controlled
   cell transition, classify Stage 4W **BLOCKED** rather than infer PASS.

## Prometheus and Grafana path

Existing files to reuse (nothing is deployed automatically):

- `monitoring/kube-prometheus-stack-values.yaml` — Prometheus/Grafana;
- `monitoring/open5gs-podmonitor.yaml` — scrape available 5GC metrics;
- `monitoring/ran-exporter` — OAI log-derived radio metrics when configured
  for the matching H-CRAN Pod label/namespace and verified against logs.

Starter PromQL (adapt selectors to observed labels and scrape jobs):

```promql
kube_pod_container_status_ready{pod=~"hcran-oai-(cu|macro|small)-.*"}
rate(container_cpu_usage_seconds_total{pod=~"hcran-oai-(cu|macro|small)-.*"}[5m])
container_memory_working_set_bytes{pod=~"hcran-oai-(cu|macro|small)-.*"}
increase(kube_pod_container_status_restarts_total{pod=~"hcran-oai-(cu|macro|small)-.*"}[1h])
```

These queries cover infrastructure health, **not automatic measurement of
3GPP F1AP, NGAP, UE handover, NAS or PDU session success**. Those require
purpose-built read-only exporters, log parsing and/or packet captures with
correct labels, counters and independent provenance. Do not fabricate radio
KPIs if the source is unavailable.

## Safety and deployment decision

All source changes in this branch are isolated GitHub changes. Do not use
`kubectl delete`, `helm uninstall`, patch a running CU ConfigMap, scale
disposable UEs on the busy HP node, or remove shared FlexRIC resources used
by non-H-CRAN experiments. A live RIC removal (if one exists in the active
H-CRAN configuration) must be a separately approved, reversible change after
live inventory and capacity checks.

**Decision:** NO-GO. Green chart/unit/inventory CI is not permission to
deploy. Runtime recovery, mobility, RF and OSM staging acceptance remain open.

## November 13 production qualification additions — source/CI only

**No H-CRAN image or Kubernetes/OSM workload has been released or deployed.**
The following are *staging candidate controls*, not green conformance tests.

### Stage 4W inter-DU mobility source feasibility

OAI `2026.w13` at
`openair2/RRC/NR/rrc_gNB_mobility.c` implements an F1 mobility path including
`nr_rrc_trigger_f1_ho`, `nr_HO_F1_trigger_telnet`, target-DU selection and
F1 UE Context operations. The pinned upstream
`doc/handover-tutorial.md` requires **UE-as-RFsim-server**, both DUs as
clients, distinct SSB position bitmaps, and a gNB built with
`--build-lib telnetsrv` for manual `ci trigger_f1_ho`. The older patched CU
artifact was built without an explicit telnetsrv module. Do **not** send a
remote telnet control to the running HP cluster.

A SHA-pinned audit is provided by
`scripts/hcran_stage4w_mobility_preflight.py`. It returns
`PINNED_SOURCE_HAS_F1_HANDOVER_PATH` but `runtime_status=BLOCKED`.
The temporary Helm overlays
`helm/cran-oai/du/values-hcran-mobility-macro.yaml` and
`values-hcran-mobility-small.yaml` render SSB bitmaps 1 and 2 respectively
and connect to a test-only `oai-ue-rfsim` server name. These overlays are
for a **disposable, one-UE/two-DU runner**, never the active two-UE deployment.
Actual macro→small and small→macro transitions, packet interruption, UE IP
continuity and correlated F1AP/RRC evidence have **not** passed.

### Security hardening, not yet qualified at runtime

The legacy OAI H-CRAN CU config advertises `nea0` and allows `nia0`;
the CU and DU Pods are also privileged. We added opt-in, isolated
compatibility profiles:

- `helm/cran-oai/cu/values-hcran-secure-staging.yaml`:
  NEA2 and NIA2 only, static Helm validation and negative tests;
- `helm/cran-oai/{cu,du}/values-hcran-pod-security-staging.yaml`:
  `privileged: false`, `allowPrivilegeEscalation: false`,
  `capabilities.drop: [ALL]`, `seccompProfile.type: RuntimeDefault`.
  This policy must undergo complete SCTP, RFsim, NGAP and GTP-U runtime
  compatibility testing. `readOnlyRootFilesystem` remains false pending
  verified OAI temporary-file handling.

The baseline configuration has been preserved, so none of this evidence
means the live HP pods are secure. UP integrity is a separate optional
3GPP capability/policy decision; the overlay does **not** claim that
`drb_integrity=no` has been reviewed or altered.

The isolated Stage 4X GitHub workflow
`.github/workflows/hcran-nea2-dual-ue-isolated.yml` attempts two real
synthetic UEs and checks NAS/PDU and F1-U/NG-U through the NEA2/NIA2
candidate. A successful run alone does not decode or establish the negotiated
security mode and is not a staging-cluster signoff. Never count an incomplete
or failed run as a pass.

### Read-only staging inventory and CPU/IO-pressure gate

A bounded evaluator at `scripts/hcran_staging_inventory.py` reads
Deployments, Pods, Services, and the public CU ConfigMap through **GET
only**, never Secrets or UE subscriber data. It requires a separate per-node
pressure snapshot, exact 1/1 CU/macro/small pod availability, zero current
restarts, immutable image digest in Deployment specs, no E2 agent or E2
service port, NEA2/NIA2 policy and a nonprivileged container security
context.

A synthetic healthy fixture can return
`READY_FOR_ISOLATED_STAGING_TEST_ONLY`. Even that output includes
`production_decision=NO_GO` and never authorizes rollout. An incomplete or
unhealthy real snapshot returns `BLOCKED`.

Operator command example (safe read-only **after establishing an isolated
staging context and on-node pressure evidence**):

```bash
# Run this on each actual RAN node. Use the Kubernetes nodeName, not an alias.
python3 scripts/hcran_node_pressure.py \
  --node <kubernetes-node-name> --output /tmp/hcran-node-pressure.json

# For a single-node staging test, the JSON above is the pressure input.
# For multi-node staging, combine all node entries in a reviewed JSON file.
python3 scripts/hcran_staging_inventory.py \
  --live-read-only \
  --context <explicit-staging-context> \
  --namespace <isolated-staging-namespace> \
  --pressure /tmp/hcran-node-pressure.json \
  --report /tmp/hcran-staging-inventory.json
```

This makes no mutations, and will likely **block** the existing high-load HP
node. CPU PSI `some avg10 <25`, IO PSI `some avg10 <10` and 1-minute
load/logical-CPU ratio `<1.5` are conservative **project staging safety
thresholds**, not 3GPP values. They are adjustable only by documented review.
Prometheus/Grafana scrape success, protocol evidence, and long-duration SLO
qualification are separate checks.

### What actually permits a production release

The current `scripts/ci_oai_ngap_readiness.py` report is an **inventory,
not a production approval authority**. It remains NO-GO until all recorded
3GPP-oriented service, safety, security, mobility, stability and OSM staging
gates have direct evidence. Each result needs an exact image digest,
test topology, run time, independent observer, packet or event samples,
negative test, and reproducible pass/fail criteria.

A reviewed staging release must separately demonstrate: NAS/PDU session
creation and modification/release; F1 mobility in both directions with
preserved user-plane traffic; appropriate RRC failure handling; AMF/core
outage recovery; sustained throughput/packet loss under bounded node
pressure; active Prometheus/Grafana data; and *actual* OSM-managed upgrade
plus rollback with restored state/digests. Security should be qualified
with authorized test SIMs/keys and no credential leakage.

**No automatic deploy, merge into main, OSM catalog replacement or HP
Kubernetes mutation may be triggered by passing these offline checks.**
Operator authorization and a reversible change window remain required.
