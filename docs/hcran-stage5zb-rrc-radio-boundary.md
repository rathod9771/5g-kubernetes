# Stage 5ZB — isolated UE / DU Random Access and RRCSetupComplete boundary

**Status:** EXPERIMENTAL, observer-only, synthetic RFsim, production **NO-GO**.

## Motivation

The Stage 5ZA observer-on/off A/B workflow
[44253212423](https://github.com/rathod9771/5g-kubernetes/actions/runs/44253212423)
passed all three consecutive AMF recovery cycles in both observer-off sessions
and one observer-on session. The second observer-on session failed its
**first** recovery cycle, with the macro UE receiving zero UPF ICMP replies
throughout nineteen six-packet probes, while the small UE recovered.

The archived Stage5Z phase report for the failed session shows that after
the CU's new NGSetupResponse, the macro UE logged two Service Request
categories but the CU logged **zero macro RRCSetupComplete, zero macro uplink
NAS and zero macro InitialUEMessage cell categories**. The small cell showed
all three categories and a fresh PDU. In the failed macro UE's archived log,
some later RRCSetupComplete references were merely **buffering or MAC RA
triggers**, not unambiguous `Generating RRCSetupComplete` messages.
The previous generic Stage5R `rrc_setup_complete` rule matches both and
must not be used as proof of a transmitted RRC message.

The failure may involve RFsim UE/DU MAC Random Access or RRC/F1-C signaling.
It is **not** proved to be caused by the Stage5Z CU-selection observer.
The A/B session counts (off 2/2, on 1/2) are too small for a causal claim.

## Passive, bounded observation change

The Stage5ZB classifier adds explicit allowlisted categories from live
`docker logs` streams already read by the baseline Stage5R collector:

| Log role | New category |
| --- | --- |
| UE macro / small | `ue_rrc_nas_buffered`, `ue_rrc_mac_ra_triggered` |
| UE macro / small | `ue_rrc_nas_pending_used`, `ue_rrc_setup_complete_generated` |
| UE macro / small | `ue_mac_msg3_transmitted`, `ue_mac_ra_succeeded` |
| DU macro / small | `du_mac_msg3_scheduled`, `du_mac_msg4_ack` |

Each category is based on a bounded (4096-character maximum) single-line
pattern. Only role, category and original Stage5R timing are retained.
There is no raw-line, RNTI, SUPI, NAS payload, identity, PLMN or subscriber
credential output.

`stage5zb/analyze_rrc_radio_boundary.py` partitions every genuine recovery
cycle after its new NGSetupResponse, compares both UE/DU marker chains to
cell-level CU RRC and AMF ingress, and emits **diagnostic categories only**.
Missing traces or sampling overflow remain incomplete. A generated UE
RRCSetupComplete marker does not prove its F1-C delivery, and an absent
marker does not prove packet loss.

The analyzer can *never* certify production readiness or replace the
existing Stage5P three-cycle, Stage5U negative-control or Stage5Y AMF
cell-ingress gates. A failure stays a failure.

## Next qualification and decision

Two fresh isolated GitHub-hosted RFsim sessions using the existing frozen
Stage5T UE / Stage3 CU sources. If macro recovery fails again, compare:

1. After NGSetup: MAC RA **triggered vs completed** at macro UE.
2. UE Msg3 / RA success and **generated** RRCSetupComplete.
3. DU Msg3 scheduling / Msg4 ACK and existing DU context categories.
4. CU cell 3804001 RRCSetupComplete / uplink NAS.
5. Open5GS AMF cell 0xe0101 InitialUEMessage and fresh PDU.

The instrumentation cannot fully isolate F1-C packet delivery. A future
packet-level, privacy-controlled F1AP experiment requires separate approval
and a defined capture contract.

No change to UE/CU protocol implementation, production main, reproducible
installer, OSM, HP Kubernetes, subscribers, or RIC/E2SM-KPM configuration.
