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
- simulated (not live) OSM lifecycle/rollback test suite.

All results above come from **isolated GitHub-hosted runners**. No HP cluster
credentials or production workload was involved.

## Non-negotiable remaining live gates

1. **E2AP Setup now passed** in isolated Stage 4G run 43173532767.
   The earlier SIGSEGV was diagnosed in the legacy FlexRIC plugin loader,
   and the pinned matching KPM/RC agent libraries removed the crash.
   A real E2 Setup Request and Successful Outcome were seen on SCTP.
2. **Still NO-GO:** E2SM-KPM xApp subscription and at least three valid
   KPM indications, plus a bounded E2SM-RC control acknowledgement and
   demonstrated RAN effect. E2 Setup does not prove monitoring/control.
   The pinned OAI 2026.w13 upstream labels the KPM/RC control demo
   as proof-of-concept with no actual RAN control, so a green xApp
   process alone cannot satisfy this gate.
3. In an **isolated staging** environment with a synthetic subscriber:
   UE authentication, NAS Registration Accept and PDU Session Establishment
   Accept. Verify actual assigned UE IP and subscriber redaction.
4. F1-U/NG-U GTP-U data with identifiable DU and UPF counters/TEIDs, both
   directions, not just UDP listener presence.
5. Simultaneous two-DU operation during at least three AMF recovery cycles
   with UE service available. Review CPU, disk pressure, packet loss, memory,
   restarts, and clock sync.
6. Actual OSM-controlled **staging** upgrade and rollback with unchanged
   core/DU image digests, release identities and recorded recovery time.
   The previous 109 OSM tests use mocks and cannot prove live rollback.
7. Immutable production image distribution with exact digest verification
   and authorization before any live HP replacement.

## Environment boundary

The H-CRAN OAI offline regression suite on experimental branch (run 43181622225) passed 1,047 tests with two skipped. This does not substitute for live NAS/PDU/F1-U/NG-U or E2 indications.

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
