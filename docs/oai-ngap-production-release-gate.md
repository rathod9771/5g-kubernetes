# NGAP V2 production release gate — current decision: NO-GO

The CI inventory workflow `.github/workflows/oai-ngap-production-evidence.yml`
queries read-only GitHub evidence and produces `report.json`. A successfully
executed **inventory** workflow does not indicate a production-ready image.

## Demonstrated, bounded experimental checks

- NGAP patch compilation and source C regression;
- real NGAP retry with Open5GS, including AMF restart;
- H-CRAN source/chart identity and transport rendering;
- one macro and one small DU F1 Setup; both F1-C SCTP associations simultaneously;
- three AMF stop/restart cycles while both DUs remain connected;
- E2-enabled CU build with E2 agent flag and CPU-portable options;
- real FlexRIC E2 Setup Request/Response and KPM/RC monitor subscriptions;
- Stage 4K: actual OAI UE NAS Registration Accept, PDU Session
  Establishment Accept and UE IP 10.45.0.2 on macro DU 100;
- Stage 4L: **actual bidirectional F1-U and NG-U GTP-U G-PDUs** with
  TEID evidence; 4/4 internal UE-to-UPF ICMP replies, zero packet loss;
- Stage 4M and 4N: both DUs were F1-C attached during successful UE data
  traffic through macro DU 100 or small DU 101, respectively;
- Stage 4O ([run 43194751671](https://github.com/rathod9771/5g-kubernetes/actions/runs/43194751671)):
  **two separate synthetic subscribers and two UEs simultaneously**;
  assigned 10.45.0.2 and 10.45.0.3; each had 6/6 ICMP replies.
  Macro and small F1-U each recorded 14 UL/12 DL G-PDUs, NG-U
  28 UL/24 DL packets, with two distinct NG-U TEIDs; both F1-C peers stayed up.
- Stage 4P ([run 43208820608](https://github.com/rathod9771/5g-kubernetes/actions/runs/43208820608)):
  **FAIL**: after the entire disposable Open5GS core restarted, NGAP
  recovered in 36.19 seconds, both DU SCTP associations persisted,
  but the CU issued RRC Release, removed both DRBs/GTP-U tunnels,
  and neither UE's data service recovered without intervention.
  This is NOT an AMF-only restart test.
- Stage 4Q ([run 43209876014](https://github.com/rathod9771/5g-kubernetes/actions/runs/43209876014)):
  **PASS only for assisted recovery**: after the same full core outage and
  36.08-second NGAP reconnection, both simulated UE processes were explicitly
  restarted. Both obtained new Registration Accept and PDU Session
  Establishment Accept, had distinct PDU IPs, exchanged 6/6 loss-free
  ICMP replies, and restored bidirectional F1-U/NG-U G-PDUs with two TEIDs.
  This does **not** reverse the failed automatic-recovery result in 4P.
- Stage 4R: separate experimental GitHub test of real E2SM-KPM indication
  callbacks under two actively registered H-CRAN UEs; no PASS assumed
  until runtime evidence exists.
- simulated (not live) OSM lifecycle/rollback test suite.

All results above come from **isolated GitHub-hosted runners**. No HP cluster
credentials or production workload was involved.

## Non-negotiable remaining live gates

1. **E2AP Setup now passed** in isolated Stage 4G run 43173532767.
   The earlier SIGSEGV was diagnosed in the legacy FlexRIC plugin loader,
   and the pinned matching KPM/RC agent libraries removed the crash.
   A real E2 Setup Request and Successful Outcome were seen on SCTP.
2. **Still NO-GO:** KPM/RC monitoring xApps subscribed in Stage 4I.
   At least three **real KPM indications with an attached UE** are pending
   Stage 4M. A bounded E2SM-RC control acknowledgement and demonstrated
   RAN effect are also missing. OAI 2026.w13's example RC control path is
   proof-of-concept, not a validated live RAN action.
3. **Stages 4K–4O PASSED**: a disposable Open5GS core authenticated
   two independent synthetic subscribers on separate H-CRAN DUs, established
   two PDU sessions, and carried concurrent F1-U and NG-U user-plane
   traffic. These are bounded hosted-runner trials, not a capacity/SLO test.
4. **Stage 4L PASSED**: pcap shows F1-U G-PDUs (12 UL/8 DL) and NG-U
   G-PDUs (12 UL/8 DL), with 4/4 responses from the isolated UPF gateway.
   These are short observations, not production throughput/loss testing.
5. **Stage 4P failed automatic dual-UE data recovery after a full 5GC restart**.
   RRC and bearer contexts were released. A fresh UE registration/PDU
   recovery is required; distinguish an *assisted UE restart* from autonomous
   resumption. AMF-only outage tests with active UE service and >=3 cycles
   are still required, plus CPU, disk pressure, packet loss, memory, restarts,
   and independent clock/observer evidence.
6. Actual OSM-controlled **staging** upgrade and rollback with unchanged
   core/DU image digests, release identities and recorded recovery time.
   The previous 109 OSM tests use mocks and cannot prove live rollback.
7. Immutable production image distribution with exact digest verification
   and authorization before any live HP replacement.

## Environment boundary

The H-CRAN OAI offline regression suite on experimental branch (run 43181622225) passed 1,047 tests with two skipped. It cannot substitute for E2 indications, demonstrable RC control, automatic session recovery under failures, sustained SLOs, or staging rollback.

The current HP system is a busy live Kubernetes/OSM/Longhorn/Open5GS lab;
no private kubeconfig, subscriber secret, staging namespace, OSM lifecycle
credentials or change window has been supplied through this connected GitHub
environment. **Do not redirect the CI container to this live cluster.**

Current experimental images are short-retention GitHub artifacts and are
not validated as a controlled immutable production image. Keep the existing
CU/DU pods running unchanged.

**Result:** `NO_GO`, until the remaining gates are genuinely passed and
reviewed. Do not merge an automated production/deployment trigger in place
of that evidence.
