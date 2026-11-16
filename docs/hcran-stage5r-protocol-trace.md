# Stage 5R — bounded protocol-level localization, NO DEPLOY

## Motivation (Stage 5Q run 43803131061)
The full offline Stage 5Q tests succeeded, and both pinned source builds
completed. The live three-cycle experiment failed on cycle 1.

The macro UE re-registered, refreshed PDU IPv4 10.45.0.2 -> 10.45.0.4,
and restored 6/6 ICMP. The small UE stayed running without a restart,
attempted NAS Service Request five times, never received NAS Service Reject,
exhausted the experimental Stage5I retry budget, retained 10.45.0.3 and
returned 0/6 ICMP through all postfault probes. The AMF reconnected via NGAP.

Stage 5Q's bounded BWP0 cached rebind is therefore NOT a verified fix.
Archived logs lacked the full post-fault small-UE MAC/RRC timeline needed
to determine whether it was waiting for SIB1/BWP, blocked in random access
or unable to deliver RRCSetupComplete / NAS to the CU/AMF.

## New scope
This branch makes **no UE, CU, DU, NAS, OSM, Kubernetes or installer source
behavior change** relative to experimental Stage5Q. Instead, it attaches
five docker logs --follow --tail 0 --timestamps readers to both UE,
CU and both DU containers during the genuine Stage5P multi-cycle AMF fault
procedure. A sixth reader uses header-only tcpdump for SCTP signaling on
38412 (NGAP N2) and 38472 (F1-C). It does not preserve the SCTP payload.

Event data are reduced to an explicit allowlist of:
- UE: SIB1, BWP0 restoration/deferrals and numeric state counters, PRACH/RAR,
  RRC setup/complete/idle/release, NAS Service Request/Reject, cause 9,
  T3517 retry, registration/PDU Accept, TUN refresh.
- CU/DUs: F1 setup, RRC setup/complete, UL/DL information transfer, NGAP
  association, SCTP shutdown.
- AMF: InitialUEMessage, unknown 5G-S-TMSI, UE context release and N2 accept.
- Harness: exact AMF stop/restart, NGAP reconnect, bounded ICMP probes and
  Stage5P cycle boundaries.

The new stage5r-protocol-timeline.json contains only timestamps,
event categories, whitelisted numeric BWP metadata, and counters.
It excludes raw NAS payload, IMSI, key, SUCI, or NGAP/F1 packet bytes.
The historical Stage5P acceptance harness still archives its own
synthetic-test log/ICMP/GTP-U evidence separately; these should be handled
as test-only evidence and never commingled with real subscriber data.
The observer enforces 1800 total timeline events and 100 emitted
events per category/role; complete aggregate category counts survive caps.

## Test plan
Two independent, disposable Ubuntu 24.04 GitHub-hosted sessions rebuild
pinned experimental Stage3 NGAP CU and Stage5Q UE. Each executes **three
consecutive AMF stops/restarts against the same two UE processes**.
Genuine Stage5I/Stage5K and Stage5P evidence verifiers still decide PASS;
a diagnostic trace cannot turn a failed fault experiment green.

Offline test command:
python3 -m unittest discover -s tests -p 'test_hcran_stage5r_contract.py' -v

The runtime workflow is .github/workflows/hcran-stage5r-protocol-trace.yml.

If either session fails, use the first incomplete UE's ordered trace plus
CU/AMF and N2/F1-C packet categories to narrow the failure boundary.
No recovery code changes until the trace supports one hypothesis.
No source promotion, HP cluster change, OSM catalog update, subscriber
mutation or production authorization is permitted. Decision: **NO-GO**.
