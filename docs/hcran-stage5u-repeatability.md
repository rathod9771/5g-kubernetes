# Stage 5U — Stage 5T repeatability and fail-closed negative controls

**Research gate only; NO DEPLOY and production authorization remains NO-GO.**

## Independent evidence audit for Stage 5T (17 November 2026)

Stage5T commit: \`1e7f1dbb1068894a9a07783c5f8025ac54c2daaa\`.
Original Stage5T Actions [run 44032035400](https://github.com/rathod9771/5g-kubernetes/actions/runs/44032035400):
both independent jobs completed successfully. Full offline regression passed in
[run 44032035274](https://github.com/rathod9771/5g-kubernetes/actions/runs/44032035274).

The two Stage5T protocol artifact IDs are \`14307450981\` and
\`14306716819\` (research artifact retention 7 days). The detailed
per-cycle evidence was independently inspected on 17 November 2026.

All 6/6 AMF stop/restart cycles achieved:
- identical synthetic macro and small UE process fingerprints across
  each three-cycle session, no UE restart;
- fresh separate PDU IPv4 addresses for both UEs each cycle;
- 6/6 ICMP after recovery by **each** UE;
- ordered NAS cause-9 -> SUCI registration -> Registration Accept -> new
  PDU session, either after one Service Request or a bounded retry;
- separate distinct SHA-256 packet captures per cycle with real uplink
  and downlink F1-U (both DUs) and NG-U G-PDUs;
- no Stage5M BWP guard deferral counted in any cycle;
- zero Stage5R timeline collection errors.

Individual recorded full fault-cycle durations were 75.07–80.32 s. These
envelopes include the controlled AMF-down interval and must **not** be
interpreted as pure UE service outage or standard MTTR without
separately calibrated event timestamps.

## Stage 5U acceptance scope

A new dedicated four-job GitHub-hosted \`ubuntu-24.04\` matrix re-runs
exactly the **same Stage5T source-pinned UE patch and Stage3-like NGAP
CU**. Each independent session performs three consecutive real AMF
fault/recovery cycles against the same two synthetic UE processes.
Stage5S and Stage5K independent validators continue to require actual
NAS, fresh PDU/TUN, UE process identity, both DU user planes, and
distinct F1-U/NG-U captures. Stage5R captures allowlisted live
protocol-state event categories.

After each successful genuine three-cycle run, Stage5U independently:
1. re-verifies the complete three-cycle proof;
2. requires bounded protocol tracing to be complete and contain >=3
   SIB1, NAS cause9/SUCI, new registration/PDU, TUN refresh markers
   for **both UEs** and no BWP guard or NAS retry exhaustion;
3. creates isolated disposable copies of the validated evidence and
   confirms that nine deliberate evidence corruptions are each
   **rejected**: missing capture, tampered capture SHA, stale PDU IP,
   mismatched UE identity, missing cycle, missing Service Reject,
   absent small-DU F1-U DL packets, missing real PASS event,
   and missing protocol trace.

This is a repeatability and *verifier sensitivity* gate, **not**
a field/3GPP conformance certificate, RF hardware qualification,
negative network-fault injection campaign, long soak, OSM rollback
test, or authorization to modify main/reproducible-installer/HP lab.

**Go/no-go:** any failed real cycle, trace incompleteness, or negative
control not rejected fails the entire Stage5U workflow. Even all 12/12
cycles passing maintains production NO-GO pending independent staging
security, capacity/soak, rollback and regulatory/3GPP test obligations.
