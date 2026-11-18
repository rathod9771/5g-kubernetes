# Stage 5Y-A — Failed dual-UE AMF recovery: independent evidence audit (2026-11-18)

**Scope:** isolated GitHub-hosted two-UE/two-DU OAI RFsim. **NO DEPLOY; production NO-GO.**
**Investigated commit:** `a51b9bcd8203d989bfb98d41ebb3b0829e8479b9`.
**Live run:** https://github.com/rathod9771/5g-kubernetes/actions/runs/44234327537
**Offline run:** https://github.com/rathod9771/5g-kubernetes/actions/runs/44234327398

This report summarizes **retained redacted experimental evidence**, not a production deployment or a completed six-cycle acceptance. The two failed archived trials are `hcran-stage5y-repeatability-trial-1` (artifact `14397617960`) and `hcran-stage5y-repeatability-trial-2` (artifact `14397656909`). They contain only one attempted recovery cycle each; neither produced a passing Stage5P cycle.

## Verifiable findings

| Evidence | Trial 1 | Trial 2 |
| --- | --- | --- |
| Initial two-UE NAS/PDU/ICMP and bidirectional F1-U/NG-U | PASS | PASS |
| AMF stopped and restarted | PASS | PASS |
| New CU NGSetupResponse and F1-C peer checks | PASS | PASS |
| Macro UE ICMP after AMF restart | 0/6 replies, each of 19 probes | 0/6 replies, each of 19 probes |
| Small UE ICMP after AMF restart | 6/6 replies, each of 19 probes | 6/6 replies, each of 19 probes |
| Macro UE PDU IPv4 | unchanged `10.45.0.2` | unchanged `10.45.0.2` |
| Small UE fresh PDU IPv4 | `10.45.0.4` | `10.45.0.4` |
| Macro Service Request indicators (Stage5X per-cycle) | 3 | 3 |
| Macro Service Reject / fresh PDU / AMF cell ingress indicators | 0 / 0 / 0 | 0 / 0 / 0 |
| Macro CU RRCSetupComplete / UL NAS indicators (Stage5X) | 0 / 0 | 1 / 0 |
| Macro NAS bounded retries exhausted (Stage5I) | 1 | 1 |
| Small Service Reject / cause #9 / fresh PDU | present | present |
| Small CU RRCSetupComplete / UL NAS / AMF cell-ingress markers | 3 / 4 / 2 | 4 / 4 / 2 |
| CU, DUs, AMF and both UE containers alive at cleanup | YES | YES |
| Stage5P first cycle | FAIL | FAIL |

Counts are Stage5X/Stage5I **allowlisted markers**, not raw NGAP packet counts or subscriber-bound cryptographic proof. The absence of a marker is an evidence gap; it does not prove a particular interface dropped a message.

**Failure boundary:** both macro UEs generated Service Requests after AMF restart, entered or attempted RRC Setup, failed to show a fresh NAS rejection/registration/PDU chain, and ultimately exhausted bounded NAS retries. The small-cell UE recovered successfully in both independent sessions. The AMF process-count correction removed the previous transient `/proc` parse exception from the observed failure path; it did not solve macro recovery.

### Offline regression correction

The independent offline run executed **1,126 tests, with one ERROR and three skipped**. The sole error occurred in the newly added `test_disappearing_amf_proc_entry_counter_stays_numeric_and_fail_closed`: its synthetic stdout test fixture used a literal `\\n` instead of an actual newline. This is a fixture error, not proof that the process-count helper fails with real shell output. The test fixture is corrected in the next experiment-only commit and must pass offline CI before any further live run.

## Interpretation and next safe qualification step

The evidence localizes the outstanding problem **between macro-UE RRC/NAS re-establishment and independently observable AMF cell ingress**. It does **not** establish whether the missing progress is at the UE radio/MAC-RLC, F1-C, CU RRC/NGAP, SCTP, or AMF boundary. Some macro RRCSetupComplete events appear at the CU during portions of the trace, but no post-fault macro per-cell AMF InitialUEMessage event is qualified.

**Do not bypass the gate:** never count stale `10.45.0.2` ICMP failure as a successful recovery; do not extend Stage5I retry budgets, fake a NAS response, suppress AMF cell evidence, or reuse the successful Stage5X archive as Stage5Y evidence.

**Next controlled investigation (no automatic production writes):** collect a time-bounded, privacy-reduced, cell-correlated trace for macro and small cell covering (1) UE Service Request and buffered dedicated NAS, (2) DU F1-C UL RRC container transfer, (3) CU RRCSetupComplete and NGAP InitialUEMessage production, (4) CU-to-AMF NGAP SCTP transport, and (5) AMF InitialUEMessage categorization. Inspect in memory and save only timestamps, cell labels, message categories, counts and explicit gaps; do not persist subscriber IDs, NAS payloads, or raw packet captures. A controlled macro-only vs dual-UE comparison should test whether the failure requires concurrent small-cell activity, without changing the pinned UE/CU recovery code.

A fresh **two independent sessions × three AMF faults** qualification is required after resolving the observed macro failure. Both sessions must pass the genuine NAS/PDU/ICMP/F1-U/NG-U recovery gate, the nine negative controls, and the separate per-cycle AMF-cell observer gate. Until then production remains **NO-GO**.

Main, the installer, OSM, and the HP cluster remain out of scope.
