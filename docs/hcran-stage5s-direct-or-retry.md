# Stage 5S — correctly qualify direct Service Reject or bounded retry (NO DEPLOY)

## Why

Stage 5R run **43816804915**: Trial 2 completed 3/3 sequential
two-UE AMF recovery cycles with new PDU IPv4, 6/6 ICMP per UE and real
bidirectional F1-U/NG-U packet captures.

Trial 1 also had both UEs regain NAS/PDU/user plane in cycle 3, but Stage5I
marked the run FAIL because macro UE received a real NAS Service Reject
on its first Service Request and therefore **needed no retry**.
Both Stage5I runtime wrapper and its independent verifier had incorrectly
made a scheduled retry + second request mandatory.

The failure was an **acceptance-contract false negative**, not an
RFsim segmentation fault or missing postfault traffic in that cycle.
Do not infer the intermittent Stage5Q recovery defect is gone; previous
Stage5P/5Q sessions really failed to restore one UE.

## Exact evidence paths allowed

- **direct_service_reject**: one Service Request, no retry scheduled,
  followed by Service Reject, cause #9, fresh SUCI Registration Request,
  Registration Accept and PDU Session Establishment Accept.
- **bounded_retry**: at least one bounded retry scheduled and a second
  Service Request, followed by the same real cause-9 → SUCI → Registration
  Accept → PDU Accept chain.

The path chosen must match the recorded marker counts. A request with no
reject/response, contradictory retry counts, timer-arm failure or exhausted
retry budget remains **FAIL**. A completed NAS path without postfault
6/6 user-plane ICMP, independently validated bidirectional GTP-U, unchanged
UE process IDs or new PDU IPs also remains **FAIL**.

The per-UE `recovery_path` and runtime `recovery_paths` are recorded.
`bounded_service_request_retry_observed_for_both` is derived, not
unconditionally asserted. This avoids mislabeling a successful direct path
as a retry. The Stage5P independent evaluator still enforces three
consecutive AMF stop/restart cycles within the SAME two UE processes;
two independently hosted RFsim sessions provide run-to-run variance.

## Scope and reproducibility

Changes are limited to isolated Stage5I experiment evidence code, its
independent verifier, new contract tests, docs and the Stage5S workflow.
No production UE binaries, Helm install paths, Kubernetes objects, OSM
catalogs, subscriber records, RIC/E2SM or HP cluster changed.

- Stage5R historical source: `aedc8ebb091631318b1c9f1f861dccf5aea88867`
- Stage5R run: https://github.com/rathod9771/5g-kubernetes/actions/runs/43816804915
- Stage5S runs rebuild pinned OAI W13 CU and W45 Stage5Q UE from source
  on disposable Ubuntu GitHub-hosted runners, no historical artifact fetch.
- Run local negative tests: `python3 -m unittest discover -s tests -p 'test_hcran_stage5s_direct_retry.py' -v`

**Production decision: NO-GO.** Runtime Stage5S results, repeated soak,
RRC/MAC reliability, security, OSM rollback and RF conformance remain
qualification gates. Do not promote source until Stage5S and independent
repeatability testing succeed.
