# H-CRAN OAI Stage 4U — bounded supervised synthetic UE recovery

## Why this experiment exists

The patched OAI CU successfully reconnects NGAP to the AMF. However, real
isolated Stage 4P fault-injection runs `43218236191` and `43219517357`
showed that CU reconnection **does not by itself restore UE NAS/PDU service**.
Both test UE PDU tunnel interfaces disappeared and did not return within the
150-second spontaneous recovery window. A later *explicitly controlled* UE
simulator restart restored service (Stage 4Q run `43220775040`).

**Stage 4U is not a native 3GPP UE re-attach fix.** It tests a bounded
supervisor policy for *disposable synthetic UE simulators only*.

## Runtime policy and acceptance

On a disposable GitHub-hosted Docker bridge:

1. Require two concurrent UE subscriptions, two distinct PDU IP addresses,
   and bidirectional data over macro DU 100 and small DU 101.
2. Kill and restart *only* the isolated AMF daemon, preserve core NF and
   CU/DU processes.
3. Require patched CU NGAP redial, NEW NG Setup Response, and unchanged
   SCTP associations to both DUs.
4. Check PDU tunnel presence twice, three seconds apart. A single failed
   probe must never cause a restart.
5. If PDU service remains absent and AMF plus all three CU SCTP peers are
   healthy, recreate **at most one instance of each synthetic UE simulator**.
6. Require fresh NAS Registration Accept and PDU Session Establishment
   Accept for each recreated UE, unique assigned IPs, and post-fault
   bidirectional F1-U/NG-U GTP-U plus UE-to-UPF ICMP traffic.
7. Preserve sanitized evidence, policy action counts, and elapsed time.
   Reject timeouts, unstable AMF, lost F1 associations or any extra restart.

## Scope and limitations

- This workflow is **isolated**. It does not read or change the production
  HP Kubernetes cluster, OSM catalog, dashboard or its real subscriber DB.
- Only test-local Docker names `oai-ngap-stage4k-ue` and
  `oai-ngap-stage4k-ue-small` may be recreated.
- This mechanism is an **orchestrator-assisted restart of a benchmark NR-UE
  process**, not an autonomous physical handset/NAS recovery mechanism.
- It does **not** prove production-grade UE service continuity, an independent
  failure observer, a multi-cycle failover SLO or actual OSM staging rollback.
- Because upstream `2026.w13` E2SM-RC control implementation is described as
  proof-of-concept, RC actions with demonstrated RAN effect remain out of
  scope. KPM indication delivery was separately verified in Stage 4S,
  run `43223244340` (30 indications).

**Production decision remains NO_GO.** A positive CI result may justify a
staging pilot of a benchmark-UE recovery supervisor only after authorization,
resource gates, and rollback have been independently verified.
