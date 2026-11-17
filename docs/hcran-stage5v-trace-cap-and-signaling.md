# Stage 5V — split real AMF recovery failures from SCTP header sampling

Status: **research / NO DEPLOY / NO-GO for production.**

## Evidence from Stage 5U run 44050626134 (17 November 2026)

All four Stage5U jobs rebuilt the same pinned Stage5T research UE and Stage3-like
CU. Full repository offline validation passed.

- **Trial 1:** cycle 1 PASS; cycle 2 **real small-UE recovery FAIL**. Macro
  recovered 6/6 ICMP, fresh PDU. Small UE retained old PDU IP and failed
  0/6 across 19 ping attempts. Five Service Requests, eventual NAS retry
  exhaustion; no Service Reject or fresh PDU Accept observed.
- **Trial 3:** cycle 1 PASS; cycle 2 **real macro-UE recovery FAIL**. Small
  recovered, macro retained old PDU IP and failed all 19 postfault probes.
  Five Service Requests and retry exhaustion; no Service Reject / new
  PDU observed. Both CU NGSetup and two F1 SCTP peers recovered.
- **Trials 2 and 4:** 3/3 cycles each passed genuine Stage5S/NAS/TUN/ICMP
  and F1-U/NG-U PCAP contracts, *then* Stage5U negative controls did
  not run because its observation gate incorrectly treated normal
  SCTP-header sampling as missing essential UE observations.
- Overall Stage5U FAIL. Never convert two successful sessions into a
  12/12 PASS. The source-candidate Stage5T earlier passed 6/6 in a
  smaller run; it has now demonstrably failed under repetition.

### Diagnostic distinction

The failing UE varies by session: small in trial 1, macro in trial 3.
After the successful postfault NGSetupResponse, the failed UE continued
recording Service Requests but the allowlisted observer never recorded a
Service Reject or new PDU accept. Successful peer UEs did receive the
reject/cause9 and re-established their sessions. **This does not prove
whether the failed request was delivered through CU to AMF.** Dedicated,
bounded NGAP InitialUEMessage/NAS attribution is a future protocol gate.
Neither failed trial re-triggered the old Stage5Q missing-BWP guard.
Do not patch BWP0 a second time without this evidence.

### SCTP header sampling defect

The Stage5R observer always counts all F1-C and N2 SCTP packet headers
but intentionally preserves only the first 100 events per category.
Stage5U set its global “collection incomplete” flag when the F1-C
header category exceeded 100, even though UE/NAS/RRC event categories
never reached their 100-event cap. From the four artifact traces,
F1-C category totals were 2578, 1356, 1642, and 1686 packets, while
the number of persisted events remained 824, 1061, 806, and 1090
(all below 1800).

Stage5V fixes accounting only: **sampling raw SCTP header events does
not set a UE-protocol-incomplete flag**. Actual timeline capacity
exhaustion, missing sources, or truncated critical categories still
fail closed. Stage5U’s nine real-evidence negative controls are
unchanged, with an explicit check of the new wire sampling metadata.

The Stage5V two-session RFsim workflow retains the *identical* pinned
Stage5T CU/UE builder, three real AMF faults per session, NAS cause9,
new PDU addresses, UE process identity, ICMP, and both-DU F1-U/NG-U
wire requirements. It also produces a privacy-safe **per-cycle
post-NGSetup NAS/RRC/CU phase report on success or failure**.
A Stage5V diagnostic PASS does NOT replace a failed Stage5U 4x3
repeatability gate or authorize staging/HP/OSM/production deployment.
