# OAI 2026.w13 NGAP V2 — Acceptance evidence matrix

**Scope:** GitHub-hosted experimental test environment only.
**Last updated:** 2026-11-12.
**Production promotion decision: NO-GO.**
No experiment in this series changed the live HP Kubernetes node, Open5GS,
OSM catalog/NS, H-CRAN deployments or production dashboard.

| Gate | Observation | Classification / evidence |
| --- | --- | --- |
| Patch syntax and source-derived C regression | 2/2 source tests, full OAI compile passed | PASS — experimental source/build only |
| Real NGAP connection with Open5GS | First NG Setup and recovery after AMF restart; kernel SCTP peer | PASS — [Stage 3 run 43157479388](https://github.com/rathod9771/5g-kubernetes/actions/runs/43157479388) |
| Canonical H-CRAN CU+DU manifests | 5 offline Helm/image configuration checks | PASS static — [Stage 4A run 43160947977](https://github.com/rathod9771/5g-kubernetes/actions/runs/43160947977) |
| Macro F1-C | F1 Setup Request/Response from DU 100 | PASS isolated — [Stage 4B run 43161900489](https://github.com/rathod9771/5g-kubernetes/actions/runs/43161900489) |
| Small F1-C | F1 Setup Request/Response from DU 101 | PASS isolated — [Stage 4C run 43162173607](https://github.com/rathod9771/5g-kubernetes/actions/runs/43162173607) |
| Dual F1-C + NGAP | Both DUs and AMF simultaneously in CU's SCTP peers; 15 s retention | PASS isolated — [Stage 4D run 43162695243](https://github.com/rathod9771/5g-kubernetes/actions/runs/43162695243) |
| Dual-DU NGAP fault recovery | 3 intentional AMF stop/restart cycles, 4 NG Setup Responses total including initial; 5 redials; no repeated F1 Setup for either DU | PASS isolated — [Stage 4E run 43163998295](https://github.com/rathod9771/5g-kubernetes/actions/runs/43163998295) |
| OSM/lifecycle offline guard tests | 109 tests passed using synthetic IDs and mocks, no real deployment | PASS offline simulation only — [Stage 4H run 43165822749](https://github.com/rathod9771/5g-kubernetes/actions/runs/43165822749) |
| E2-enabled CU | Patched `2026.w13` with `--build-e2 --noavx512 --noavx2`, build and image packaging passed | **PASS compile/package only** — [Stage 4F run 43165182823](https://github.com/rathod9771/5g-kubernetes/actions/runs/43165182823) |
| Real E2 Setup / FlexRIC | First pinned FlexRIC build failed on an unrelated monitoring example requiring custom ASN.1 compiler; focused RIC + KPM/RC build retry started | **NOT VERIFIED** — [Stage 4G retry 43167220273](https://github.com/rathod9771/5g-kubernetes/actions/runs/43167220273) |
| Live KPM indications / RC control | No confirmed xApp subscription, indications, or E2SM-RC action acknowledgement | NOT VERIFIED |
| UE NAS registration and PDU establishment | No UE and no subscriber in any dual-DU isolated trial | NOT VERIFIED |
| F1-U / NG-U traffic and throughput | F1-U UDP ports checked statically, no actual DRB/TEID data exchange | NOT VERIFIED |
| Staging upgrade/rollback, OSM NS/KNF delivery | No staging credentials or approved change window; no OSM live operation | NOT VERIFIED |

## Interpretation

The NGAP V2 patch has substantive experimental evidence for reconnection and
for preserving two F1-C SCTP associations during three AMF failures. This is
not an end-to-end UE service or a Rel-15/16 protocol conformance certificate.
A passing offline OSM simulation does **not** authorize a real catalog or NS
mutation.

The E2-enabled CU build now passes; the next gating work is actual E2 Setup
and KPM/RC function checks with version-matched FlexRIC, followed by genuine UE registration/PDU/F1-U tests on
an **isolated** appropriately resourced full 5GC staging environment, then
documented OSM-controlled deployment and rollback. Only after those results
and explicit operator approval should an image be considered for HP integration.

Experimental image artifacts currently have short GitHub Actions retention.
Do not treat an expiring runner artifact as a production-distributed immutable
container image.
