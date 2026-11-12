# Stage 4M: real attached UE plus E2SM-KPM indications (isolated CI)

This experiment combines two previously successful isolated stacks:

- Stage 4L real OAI NGAP-patched CU + H-CRAN macro DU 100 + synthetic OAI UE,
  Open5GS NAS/PDU and bidirectional user-plane.
- Stage 4I pinned nearRT-RIC + matching OAI E2-enabled CU plugins, and
  FlexRIC E2SM-KPM RAN Function 2 xApp subscription.

The workflow rebuilds the pinned ASN.1 compiler and xApp; it starts a fresh
disposable MongoDB-backed Open5GS 5GC, attaches one synthetic UE, confirms
PDU, and then subscribes the KPM xApp while the UE sends internal ping
traffic to its UPF gateway. It captures E2AP SCTP Request/Response.

**Acceptance:** a real E2 Setup Response, real UE Registration Accept,
real PDU Session Establishment Accept, successful KPM Subscription Response,
real UE connectivity and **at least three genuine KPM indication events**.
No indications or a crash fails the job; no silent pass from mere xApp exit.

**Scope limitations:** one DU and one UE; no concurrent macro/small with
two active UEs. RC monitoring subscriptions are already proven but this
does not establish operational RC control actions or RAN effects. No
production Kubernetes/OSM or rollback is touched, and this is not a
long-duration production SLO benchmark.

No user/private SIM credentials are involved. GitHub runner artifacts
contain only synthetic data, xApp logs and wire captures. Temporary
subscriber configuration is deleted before upload.
