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

1. E2AP setup response; version-matched nearRT-RIC and service models.
   Stage 4G diagnosed a **SIGSEGV** in FlexRIC plugin loading. A candidate
   that crashes before setup is rejected even if the NGAP patch itself works.
2. E2SM-KPM xApp subscription, at least three valid KPM indications, and
   bounded RC control with acknowledgement. Mere E2 Setup does not prove
   control or monitoring behavior.
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
