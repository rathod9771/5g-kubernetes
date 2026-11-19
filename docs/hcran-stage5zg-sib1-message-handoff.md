# H-CRAN Stage 5ZG — RRC-to-MAC SIB1 scheduling handoff (NO DEPLOY)

**Date:** 19 November 2026. **Scope:** only isolated GitHub-hosted OAI RFsim with synthetic subscribers and frozen Stage5T UE protocol/recovery patches; production **NO-GO**.

## Confirmed evidence from Stage 5ZF

GitHub Actions offline [44451915472](https://github.com/rathod9771/5g-kubernetes/actions/runs/44451915472) failed **one legacy offline test contract**, which asserted that the Stage5ZD experiment builder name must still be present in the workflow. The new workflow had switched to the Stage5ZF builder without retaining that syntax check. The other 1,145 tests were not failures (three reported skipped). This is a separate test issue, not a protocol fix.

Live [44451915014](https://github.com/rathod9771/5g-kubernetes/actions/runs/44451915014) failed overall: one independent two-UE session completed **3/3 actual AMF restarts** with fresh PDU and GTP-U/ICMP evidence, and the other passed only cycle 1, failing cycle 2 (cycle 3 not attempted). Both official Stage5P recovery gates remained fail-closed.

The **failed small-cell UE** reached 0/6 ping replies in **19 consecutive probes** while the macro UE recovered. The independently downloaded Stage5ZF category timeline for that cycle (stage5r-protocol-timeline.json) shows:
- At **155.652 seconds** elapsed: RRC SI decision `sib1_valid=0, sib_pending=0, get_sib=1, rrc_state=0`, so **RRC explicitly requests SIB1**.
- 155.653 seconds: MAC RA deferral (`mac_state=0`, `UL BWP0=0`, `DL BWP0=0`).
- 155.660 seconds: new PHY synchronization begins; 156.011 seconds: synchronization marker.
- From **156.040 seconds onward**, MAC MIB samples repeatedly report `get_sib1=0, UL BWP=0, DL BWP=0`.
- **No post-155.652-second MAC SIB scheduling callback, new SIB1 decode or fresh PDU** is observed.
- At **285.803 seconds**, RRC sampled `sib1_valid=0, sib_pending=1, get_sib=0`, consistent with a prior SIB request remaining pending, but not proof it was lost.

**Important correction:** the previous Stage5ZF analyzer classified both valid SI-cache and missing-BWP samples across an entire cycle as a candidate causal mismatch. That was not temporally bound and was misleading here. The failed UE *did request SIB1*. Stage5ZG relabels this older cycle-level co-occurrence as **INCONCLUSIVE**.

## Narrow next hypothesis

The message `NR_MAC_RRC_SCHED_SIB` may be scheduled by RRC but fail to reach or be processed by MAC after the UE returns to RRC_IDLE. We do not yet know whether:
- RRC queued the message;
- MAC received and dispatched it;
- `nr_rrc_mac_sched_sib()` changed `get_sib1` / MAC reception state;
- the trace missed short-lived or racing events.

Stage5ZG adds **two log-only** markers to the exact OAI W45 upstream source commit
`dfb296ab867050a7bde81b393fa31b757ada2c83`:

1. Immediately before `nr_rrc_send_msg_to_mac(rrc, &sib_msg)` in `rrc_UE.c`: `Stage5ZG: sib_schedule_sent get_sib=...`.
2. Immediately before `nr_rrc_mac_sched_sib(instance_id, ...)` in `L2_interface_ue.c`: `Stage5ZG: sib_schedule_dispatched get_sib=...`.

The original Stage5ZF sampled SI decision and Stage5ZD MAC SIB scheduling marker remain. Only primitive bounded SIB selector/pending flags are retained in Stage5R JSON. **No raw log, UE identity, RNTI, SUPI/SUCI, cell identifying key, NAS or packet payload** enters the diagnostic-only report.

The source patch validates both exact anchors, applies after frozen Stage5G/5I/5K/5M/5Q/5T and Stage5ZD/5ZF observation patches, and aborts on source drift. The isolated disposable UE is rebuilt, with a nine-file expected diff including `L2_interface_ue.c`; it is *not bit-identical* to older UE builds because of logging.

Stage5ZG separately reports per-cycle post-restored-NGSetup RRC request/send, MAC dispatch, MAC schedule, SIB1 decode and BWP availability. It can flag **missing observed marker after a request**, not prove lost internal messages or causality. No recovery-pass semantics are changed.

## Acceptance and boundaries

The GitHub workflow retains two independent hosted RFsim sessions × three genuine AMF stop/restarts, same two UE processes, NAS/registration/PDU/ICMP/F1-U/NG-U recovery gate, nine Stage5U negative controls, and Stage5Y independent per-cell AMF ingress. The stale Stage5ZD regression is fixed by retaining its builder syntax check while invoking Stage5ZG's builder.

**No** production deployment, HP node activity, OSM/reproducible installer edits, `main` changes, subscriber modifications, protocol retries, state machine changes, Near-RT RIC or E2SM-KPM. Formal 3GPP Rel-15/16 conformance, staging security, rollback and operator approval remain outstanding.
