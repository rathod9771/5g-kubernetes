# Stage 5ZD — source-pinned MIB/SIB1 handoff probe (isolated RFsim)

**Date:** 2026-11-18. **Status:** diagnostic / NO DEPLOY / production **NO-GO**.

## Evidence leading to this experiment

Stage5ZC live [44273113846](https://github.com/rathod9771/5g-kubernetes/actions/runs/44273113846) passed 3/3 AMF recovery cycles in trial 2, and 1/3 in trial 1, with trial 1's second cycle failing. The Stage5ZC archived category timeline and source trace show:

- After the second NGSetup restored: macro UE submitted three Service Requests; each MAC RA request was postponed by the previously pinned Stage5T guard with `mac_state=0, ul_count=0, dl_count=0`.
- No new SIB1 decode, PRACH schedule, Msg3 transmission, RRCSetupComplete generation, CU receipt, AMF cell ingress or fresh macro PDU marker after NGSetup. The small UE completed its corresponding genuine service recovery.
- In the same failed macro cycle, PHY completed initial synchronization with the macro DU **before** NGSetup restoration. No later SIB1 decode was observed. The successful control trial decoded SIB1 and continued with RA.
- SIB1 absence cannot be equated to packet loss, source failure or complete causal attribution; it is a log-observed gap in MIB/SIB1 reacquisition.

## Pinned code contract

UE source **`OPENAIRINTERFACE/openairinterface5g` at exactly
`dfb296ab867050a7bde81b393fa31b757ada2c83`**. Existing Stage5G/5I/5K/5M/5Q/5T research patches remain unchanged. An additional source file patch must match exactly three anchor callsites in `config_ue.c` and adds only `LOG_I` statements:

1. `nr_rrc_mac_config_req_mib`: MIB MAC configuration callback reached; bounded MAC state, MIB pointer-present boolean, SIB1 scheduling flag and BWP counts.
2. `nr_rrc_mac_sched_sib`: SIB1 reception scheduling callback reached; same bounded state, flag and BWP counts.
3. `nr_rrc_mac_config_req_sib1`: SIB1 MAC configuration returned; bounded state, BWP counts and current BWP pointer-present flags.

The patch must fail closed on upstream source drift or reapplication. A **separate copy** of the pinned Stage5T builder builds a disposable Stage5ZD UE without changing the original builder. It preserves exact source pin checks, expected touched-file set, full source diff and SHA-256 publication, and pinned image source. The resulting executable is a new experimental binary with added logging, not a claim of bit-equivalence or zero scheduling perturbation.

The live Stage5R source observer emits only `ue_mib_configured`, `ue_sib1_scheduled`, `ue_sib1_mac_configured`, plus pre-existing and PBCH-decoded categories. The independently executable Stage5ZD analyzer partitions each genuine AMF fault cycle into **before vs after new NGSetupResponse** so pre-restoration sync cannot be mistaken for post-restoration SIB1 recovery.

## Acceptance and interpretation

Two isolated GitHub Actions sessions, three AMF cycles each. Stage5P genuine two-UE NAS/PDU/F1-U/NG-U checks, Stage5U negative controls and Stage5Y live per-cell AMF ingress remain unchanged and fail-closed. All Stage5ZD evidence is diagnostic-only.

If a failure is reproduced:
- PBCH observed, but no MIB callback: investigate PHY-to-RRC MIB dispatch and synchronization state.
- MIB callback observed, but no SIB1 scheduling callback: investigate RRC SIB1 scheduling request/transition.
- SIB1 scheduled, but not decoded: investigate SI-RNTI PDCCH/CORESET0/PHY timing without guessing packet loss.
- SIB1 decoded, but MAC config not applied: investigate RRC-to-MAC SIB1 configuration.
- BWP pointers present, but RA stalled: investigate stage5M/5T MAC state transition, separate from NGAP.

Because logging itself can change timing, any apparent improvement is **not proof the issue is fixed**. No retry budget or state-machine behavior changes are introduced.

**Hard exclusions:** no `main` update, no OSM/HP Kubernetes, no production keys/subscribers, no Near-RT RIC or E2SM-KPM, no staging or production deployment. Formal 3GPP conformance and operator authorization still outstanding.


## Stage 5ZD initial run 44280619980 — corrections and retained FAIL

The initial run built both source-pinned experimental UEs and completed isolated
RFsim, but **neither independent trial qualified**. Trial 1 passed its first
AMF restart and failed the second; trial 2 failed its first. The previously
reported macro-only pattern **did not recur identically**: both failed Stage5ZD
cycles showed the **small UE** missing a fresh post-NGSetup PDU/RA completion,
while the macro UE did recover a fresh PDU. This cross-cell variability does
not support a macro-only root cause or a verified UE source defect.

The initial MIB hook emitted repeated `mib_configured` records while the
UE was already connected (100 retained per UE, at the category ceiling).
Consequently the Stage5R collector deliberately set
`collection_limit_reached=true` in **both** archives, and all derived Stage5ZB,
5ZC, 5ZD diagnostic reports properly returned `INCOMPLETE_OBSERVATION`.
The archive is still useful for bounded counts, but the absence of any
particular marker is **not** evidence that a callback never occurred.

A Stage5ZD correction bounds *source logging only* with
`if (mac->state < UE_CONNECTED)` around the periodic MIB hook. It retains
all original MAC-state assignments and leaves Stage5T, UE timers, protocol
code, genuine acceptance gates, privacy restrictions and source pins intact.
SIB1 schedule and configured handoff markers remain unfiltered. A new
isolated RFsim run must verify that this reduces source volume and clears the
trace-overflow flag; there is **no** automatic promotion of this correction.

The initial offline run failed two compatibility tests:
the original Stage5Y test expected a direct reference to the Stage5T
builder even though Stage5ZD deliberately uses a derived pinned-log-only
builder; the release impact registry lacked an explicit classification of
the new experimental builder. These are now narrowly corrected:
Stage5Y accepts Stage5ZD **only if** its source pin, inherited Stage5T
patch and diagnostic are present, and release impact classifies precisely
that one research-only builder as a non-OSM package input.

**Decision remains NO-GO** until new CI proves the collector is complete,
real two-UE recovery passes independently, and separate staging, formal
3GPP validation and operator approval are obtained.
