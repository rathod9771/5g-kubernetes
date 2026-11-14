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
