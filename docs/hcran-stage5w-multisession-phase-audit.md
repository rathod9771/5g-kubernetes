# Stage 5W — four-session research repeatability after Stage5V diagnostic correction

**NO DEPLOY / NO-GO: independent RFsim evidence is not a production authorization.**

## Verified result: Stage5V

Stage5V [run 44060438838](https://github.com/rathod9771/5g-kubernetes/actions/runs/44060438838)
passed both three-cycle jobs: **6/6 genuine AMF stop/restart dual-UE
recovery cycles**, with new PDU addresses, unchanged UE process identity,
real ICMP, independent NAS/TUN checks and F1-U/NG-U PCAP proof. Both
trials independently rejected nine deliberately corrupted copies of
successful evidence (18/18 negative verifications). Offline project
validation also passed. The Stage5T pinned UE patch and Stage3 CU
binaries were not changed.

Stage5V's own phase report is deliberately diagnostic. In trial 2
cycle 3, the *macro* UE's Service Reject, Registration Accept and PDU
Accept occurred around 244.40-244.61 seconds in the observer clock,
while the harness recorded its NGSetupResponse at about 244.69 seconds.
Filtering the phase timeline to only events AFTER the NGSetupResponse
observation omitted those legitimate earlier UE-local markers despite
the independent 3/3 recovery PASS.

Stage5W fixes the **diagnostic attribution only**: count event markers
after the recorded AMF-restart event, separating those before versus
after NGSetupResponse *observation*. Pre-restart data is excluded;
UE-local marker timing is not evidence of AMF NGAP ingress/delivery.
No packet is forged, no NAS retry changed, no BWP configuration
invented and no real recovery acceptance relaxed.

## Stage5W qualification contract

Four independent disposable GitHub-hosted RFsim jobs each perform
three consecutive controlled AMF restarts (**12 planned cycles**),
rebuilding the **same source-pinned Stage5T UE** and Stage3-like CU.
Each job continues to require:
- two stable UE process identities and independent synthetic subscribers;
- genuine fresh dual-UE NAS registration and PDU session after each fault;
- simultaneous 6/6 per-UE ICMP after recovery;
- new distinct PDU IPv4 addresses per cycle;
- independent per-cycle bidirectional F1-U + NG-U G-PDU captures with
  distinct SHA-256 checksums;
- a complete bounded protocol timeline and nine fail-closed negative
  evidence controls, all of which must reject manipulated copies.

**FAIL-CLOSED:** Any non-recovery or one un-rejected negative evidence
control fails that session, blocks production promotion, and preserves
the real failure diagnosis. Even all 12/12 successes would only satisfy
this restricted RFsim repeatability gate, NOT 3GPP formal conformance,
hardware RF testing, long soak, resource pressure testing, secure
staging rollback or a production SLA.

Prior Stage5U failure [run 44050626134](https://github.com/rathod9771/5g-kubernetes/actions/runs/44050626134)
remains part of the evidence record: intermittent one-UE NAS recovery
was observed twice under repetition. It cannot be erased by a smaller
successful run.
