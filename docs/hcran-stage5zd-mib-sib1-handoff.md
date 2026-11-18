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
