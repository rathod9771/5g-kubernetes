# Layer 3 assurance — 3GPP Release 15–16 acceptance contract

**Scope:** This project calls its closed-loop monitoring/decision/recovery
component "Layer 3". It is **not** the NR RRC/NAS protocol layer, IP L3,
or a claim that the orchestrator implements a 3GPP radio stack. Open5GS,
OAI/srsRAN and OSM keep their protocol and lifecycle responsibilities.

## Release baseline

| Release | Specification | Relevance | Limitation |
|---|---|---|---|
| 15 | [TS 28.530](https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3273), [TS 28.533](https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3416) | Management and orchestration concepts/architecture | Local Flask/OSM APIs are not standardized 3GPP MnS endpoints |
| 15–16 | [TS 28.532](https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3427) | Generic management services | API-level compatibility unverified |
| 15–16 | [TS 28.552 v16.15.0](https://www.etsi.org/deliver/etsi_ts/128500_128599/128552/16.15.00_60/ts_128552v161500p.pdf) | NG-RAN, AMF and SMF performance measurement subjects | Vendor counter semantics need per-version verification |
| 15–16 | [TS 28.554](https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3415) | 5G end-to-end KPI definitions | A generic BLER/latency gauge is not automatically a standardized KPI |
| 16 | [TS 28.535 v16.0.0](https://www.etsi.org/deliver/etsi_ts/128500_128599/128535/16.00.00_60/ts_128535v160000p.pdf), [TS 28.536](https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3701) | Governed closed-loop communication-service assurance | Live coordination, execution and verification remain unvalidated |
| 15–16 | TS 23.501 / 23.502 / 23.503 | Registration, PDU-session and 5QI/QoS semantics | Kubernetes readiness and SCTP connection alone do not prove 5GS service |

**Standards-aligned does not mean certified 3GPP conformance.** Pin a
Release 15/16 version for each acceptance claim. Do not accidentally
attribute features from newer releases to Release 16. The sample
5% failure-rate threshold is a LOCAL candidate policy; 3GPP does not
mandate this general threshold for these service measurements.

## Added code

The new assurance.rel15_16.qualify_counter_windows function provides
an isolated read-only qualification of AMF registration, SMF PDU-session
establishment and NG-RAN RRC setup candidate ratios.

- Exact cluster/NS/NF/source identities and documented semantic-review ID.
- Two or more nonoverlapping, consecutive counter windows; 20 attempts
  minimum **per window** by default.
- Monotonic valid counters, successes no greater than attempts, source
  coverage explicitly verified, target up, no unaccounted reset.
- Source observation age at most 120 seconds. Missing, stale, future,
  conflicting, counter-reset and discontinuous sources fail closed.
- A degraded candidate only after consecutive windows breach the local
  threshold. A candidate is not an incident, a standardized KPI
  implementation or automatic permission to act.

Every return value explicitly disables production execution. The
production_readiness report cannot unlock autonomy. Synthetic unit tests
demonstrate code behavior only. **This new module is not yet connected to
the live ingestion path**; operator-reviewed adapters and metric mappings
are needed before operational use.

## Required production release gates (open)

1. **Validate metrics on the installed versions:** source metric family,
   numerator, denominator, measurement point, reset/restart, 3GPP
   measurement correspondence, raw counter timeline and label provenance.
   Missing BLER or missing UE traffic is UNAVAILABLE, never zero.
2. **Verify resource/protocol identity:** cluster, namespace UID,
   OSM NS ID, operation ID, CU/DU/AMF/SMF/UPF, slice when applicable.
   Independently check F1/NG Setup, RRC, UE registration, PDU sessions
   and measured user-plane traffic. SCTP ESTABLISHED is transport only.
3. **Establish SLS baselines and alert behavior:** per profile/slice
   failure budgets, minimum traffic, persistence/clearing hysteresis,
   severity, correlation, source outage, duplicate suppressions and
   maintenance windows. Explicitly review Prometheus rules and targets.
4. **Harden operations:** read-only collector identity, least-privilege
   Kubernetes and OSM roles, TLS, SSO with individual operator auditing,
   secrets and retention, durable audit, HA, resources and backpressure.
5. **Build scoped recovery runbooks:** preserve OSM-managed Helm ownership,
   reuse shared lifecycle locks and pending-update journal. Demonstrate
   exact NS ownership, no protected core mutation, approvals, stop switch,
   cooldown, prechecks, idempotent action, pending-operation reconciliation,
   protocol verification and real rollback on disposable profiles.
6. **Test on staging:** falsifiable fault injection for CU/DU, AMF/SMF,
   stale metrics, missing sources, duplicate alarms, failed OSM operations,
   node loss and failed rollback. Record UE interruption and PDU continuity;
   do not claim hitless RAN switching without measurement.
7. **Review go/no-go:** obtain independent approval for each allowlisted
   production operation after these gates. Existing OBSERVE_ONLY remains
   the default and LIVE_AUTO plus legacy watcher remain disabled.

## Offline verification

Run from the repository root:

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_assurance_rel15_16.py' -v
    PYTHONPATH=scripts python3 -m unittest discover -s tests -p 'test_assurance*.py'

The first command tests the new module. The second requires the normal
repository Python environment and is not claimed as run here. Neither
proves live protocol conformance, RAN failover or field readiness.
