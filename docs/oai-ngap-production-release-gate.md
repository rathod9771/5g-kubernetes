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
3. **Stage 4K PASSED**: a separate disposable Open5GS core with synthetic
   subscriber authenticated an OAI NR UE through the patched CU and macro
   DU, sent Registration Accept and PDU Session Establishment Accept and
   assigned UE IPv4 10.45.0.2. Stage 4K is an isolated single-run proof;
   multi-cycle and concurrent macro/small active-UE service remain untested.
4. **Stage 4L PASSED**: pcap shows F1-U G-PDUs (12 UL/8 DL) and NG-U
   G-PDUs (12 UL/8 DL), with 4/4 responses from the isolated UPF gateway.
   These are short observations, not production throughput/loss testing.
5. Simultaneous two-DU operation during at least three AMF recovery cycles
   with UE service available. Review CPU, disk pressure, packet loss, memory,
   restarts, and clock sync.
6. Actual OSM-controlled **staging** upgrade and rollback with unchanged
   core/DU image digests, release identities and recorded recovery time.
   The previous 109 OSM tests use mocks and cannot prove live rollback.
7. Immutable production image distribution with exact digest verification
   and authorization before any live HP replacement.

## Environment boundary

The H-CRAN OAI offline regression suite on experimental branch (run 43181622225) passed 1,047 tests with two skipped. This does not substitute for the later real one-DU UE/PDU/F1-U/NG-U proof or for E2 indications, dual-DU active traffic and staging rollback.

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
