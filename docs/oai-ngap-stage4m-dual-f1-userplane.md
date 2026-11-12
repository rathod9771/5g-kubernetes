# H-CRAN OAI Stage 4M — dual F1-C while macro UE carries traffic

## Inputs

Uses exactly the previously successful Stage 4L synthetic UE NAS/PDU/data
path, a disposable MongoDB and Open5GS network, patched OAI 2026.w13 CU,
OAI 2026.w25 macro and small DUs. Macro DU ID 100 at 172.29.76.30 and
small DU ID 101 at 172.29.76.31 connect to the same CU.

## Acceptance

1. Real Open5GS NG Setup succeeds.
2. CU logs an F1 Setup Request and its response for both DU IDs 100 and 101.
3. CU SCTP association listing includes AMF, macro and small DU before
   registration, and again after traffic.
4. One **macro UE only** receives NAS Registration Accept, PDU Session
   Establishment Accept and assigned tunnel IP.
5. UE-to-UPF ICMP and four F1-U/NG-U GTPv1-U G-PDU directions all pass the
   Stage 4L established checks.
6. Both DUs and CU remain alive; logs/evidence preserved and no live
   Kubernetes/OSM operations run.

This test does **NOT** claim small DU user plane, macro/small simultaneous
UE load, FlexRIC indications, RC control effects, throughput targets,
OSM upgrade/rollback or production readiness.

Safety: Only a disposable GitHub Actions runner, no HP SSH/Kubernetes or
production subscriber access, synthetic in-memory subscriber and automatic
test-container teardown. Existing production deployments are unchanged.

Production readiness remains **NO_GO** pending:
* real small-cell UE registration/PDU/data-path, dual-user contention;
* KPM indications under attached UE traffic and supported RC behavior;
* repeated AMF/DU/UPF faults with UE service and SLOs;
* separately authorized OSM staging rollout and rollback with digest proof.
