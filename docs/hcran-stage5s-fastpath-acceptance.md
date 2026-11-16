# Stage 5S: Fail-closed NAS fast-path acceptance (research-only)

**16 November 2026 — experimental; no live deployment; production NO-GO.**

Stage 5R run [43816804915](https://github.com/rathod9771/5g-kubernetes/actions/runs/43816804915) produced one fully successful three-cycle trial and one 2/3 trial.

Inspection of the failed session's third-cycle evidence showed **both** UEs really recovered: new macro 10.45.0.8 and small 10.45.0.9 addresses, two independent 6/6 ICMP replies and bidirectional F1-U/NG-U G-PDUs. Neither UE was restarted. Failure was caused by Stage 5I's mandatory *retry* marker: macro received NAS Service Reject cause 9 on its first Service Request, so **a retry was not necessary**. The old verifier required the exact sequence [Service Request, retry schedule, Service Request, Reject, cause9, SUCI, registration, PDU] even when a shorter legitimate chain occurred.

Stage 5S runs an *independently named*, experimental fork of the existing Stage 5I/5P harness (preserving the historical code and published proofs). The NAS acceptance rule supports exactly two documented paths:

1. **Immediate cause-9 rejection:** Service Request → Service Reject cause 9 → stale identity/security invalidation → SUCI Registration Request → Registration Accept → PDU Session Establishment Accept.
2. **Bounded retry:** Service Request → observed retry schedule → subsequent Service Request → same verified cause-9 and NAS/PDU chain.

The stage5s marker manifest explicitly states which path and how many retries actually occurred for each UE; the verifier checks the flags against the marker counts. A retry that exhausted its budget or a failed timer is still rejected. Irrespective of which path occurred, the original real UE identity, AMF-only fault, new PDU address, UE TUN refresh, real bidirectional F1-U and NG-U, separate packet captures per cycle, independent TEIDs and final 6/6 ICMP are mandatory. Missing or malformed evidence is a FAIL, not an inferred pass.

Stage5S reuses the Stage5Q pinned UE BWP0 candidate and source-built Stage3 NGAP CU; only test acceptance code changes. The workflow runs **two independent sessions**, with three consecutive AMF failures per session, on disposable GitHub-hosted runners. No HP, Kubernetes, OSM, RIC, or real subscriber access.

This fixes the demonstrated **false-negative acceptance rule** only. It does not establish that Stage5Q eliminates genuine intermittent NAS/RRC failures. Publication, production deployment and rolling promotion remain NO-GO until both sessions pass and additional staging/rollback gates are satisfied.
