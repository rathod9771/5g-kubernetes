# Stage 4I — H-CRAN OAI KPM/RC xApp smoke, no deployment

The pinned OAI 2026.w13 NGAP-V2/E2-enabled CU and pinned FlexRIC v2.03
from OAI's own flexric submodule are rebuilt/loaded on a **disposable
GitHub-hosted runner**. This test builds `xapp_kpm_moni` and `xapp_rc_moni`
and runs both against a real FlexRIC E2 Setup.

Success means the E2 peer Setup was observed on the wire and the selected
monitoring xApps ran without errors. **This is not KPM/RC production acceptance.**

UE-level KPM measurements may only be available when an authorized test UE
is attached to the correct slice and has an established PDU session. There is
no UE or subscriber in this test; zero indications are NOT interpreted as
positive evidence.

`xapp_rc_moni` is an RC *monitor*, not an RC control test. OAI 2026.w13
documents the xapp_kpm_rc control path as proof-of-concept with no actual
control carried out in the RAN. We must not mark that path as verified.

All logs, xApp exit codes, metadata and bounded PCAP are uploaded. No
production image is pushed, no OSM or Kubernetes resource touched, no HP
SSH or subscriber secret read.

Promotion: **NO_GO**. Next gates: a dedicated synthetic-subscriber staging
environment for real KPM indications, UE NAS registration, PDU/data plane,
and controlled OSM rollback; plus substantive RC control if it is supported.
