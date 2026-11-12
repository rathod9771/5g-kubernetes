# H-CRAN OAI Stage 4P — E2SM-KPM indications under dual-UE traffic

## Evidence required

Run the same disposable Open5GS, H-CRAN CU, macro DU 100 and small DU
101 used by the successful Stage 4O dual-UE experiment, but replace the
CU with the **E2-enabled** patched 2026.w13 image. Build and install the
version-matched FlexRIC E2SM-KPM/RC plugins into both the RIC and CU, and
run the pinned KPM monitoring xApp while two independent synthetic UEs
transmit GTP-U traffic.

The gate requires:
- Real E2 Setup Request and Response on SCTP between CU and nearRT-RIC.
- E2 RAN Function 2 (KPM) accepted, and KPM xApp subscription accepted.
- Macro and small DU F1-C both connected to the same E2-enabled CU.
- Two distinct UEs complete NAS Registration and PDU Accept; both
  exchange ICMP over their own UE TUN, with separate F1-U G-PDUs and
  two NG-U TEIDs per direction.
- At least **three actual RICindication** message envelopes from CU to RIC
  and at least three recognized KPM indication callback log records
  from the xApp. E2 Setup or subscription alone cannot pass.

Logs and E2AP SCTP packets come from the GitHub runner and are retained;
test-only SIM credentials and private temporary configuration are purged
before upload. A pass does not imply 3GPP conformance or sustained service.

## Limits and release decision

Only synthetic RF simulator data is used. OAI 2026.w13 upstream
describes the experimental E2SM-RC control path as proof of concept:
RC monitoring is **not** evidence of an effective RAN control change.

Production decision **NO_GO** pending real RC control-effect validation
if supported, repeated AMF/DU/UPF fault recovery while two UEs have
PDU traffic, sustained CPU/disk pressure and latency measurements,
and an explicitly approved OSM staging upgrade and rollback with
immutable image digest and service restoration evidence. No GitHub job
may touch HP Kubernetes, OSM or real subscriber secrets.
