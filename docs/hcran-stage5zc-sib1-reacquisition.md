# Stage 5ZC — post-idle SIB1/BWP0 reacquisition liveness, NO DEPLOY

**Date:** 2026-11-18. **Scope:** isolated synthetic H-CRAN OAI two-UE/two-DU RFsim and Open5GS. **Production: NO-GO.**

## Verified failure prompting the experiment

At [Stage5ZB run 44264817161](https://github.com/rathod9771/5g-kubernetes/actions/runs/44264817161), offline validation succeeded. Live trial 2 passed all 3/3 genuine AMF-restart cycles; trial 1 passed cycle 1 and failed cycle 2, leaving cycle 3 unattempted.

The independently downloaded, SHA-256 checked archives showed that after NG Setup restored in failed trial 1/cycle 2:
- Macro UE: one Service Request, NAS buffered, MAC RA triggered, **zero** UE Msg3 sent, **zero** RA success, **zero** newly generated RRCSetupComplete, **zero** CU RRCSetupComplete, **zero** AMF InitialUEMessage macro-cell marker and **zero** fresh PDU acceptance.
- Small UE: generated RRCSetupComplete, completed NAS/PDU and reached the AMF successfully.
- Macro UE later exhausted its bounded NAS retries; both UE processes stayed within original acceptance restrictions.
- The macro UE's raw bounded cleanup log shows the existing Stage5T guard **postponed MAC RA because UL/DL BWP0 were absent**. Physical-layer synchronization subsequently succeeded, but **no new SIB1 decode** was observed before the final NAS expiry. By comparison, the successful control trial logged SIB1 decode, PRACH scheduling, Msg3, RA success and RRCSetupComplete.
- One `PRACH job is in the past` DU warning appears in the failing trial **and** in the passing trial; it must **not** be used alone as a root-cause diagnosis.

These are bounded log observations, not packet-level proof of loss or evidence that Stage5T itself caused the stall. The remaining hypothesis concerns post-idle synchronization and SIB1/BWP0 reacquisition in the interval before PRACH scheduling.

## New experimental diagnostics (no recovery-code changes)

The Stage5ZC parser observes only five allowlisted UE/PHY categories:

- `ue_ra_deferred_until_sib1` (optional sanitized `mac_state`, UL and DL BWP counts)
- `ue_phy_sync_started`
- `ue_phy_initial_sync_success`
- `ue_phy_synchronized`
- `ue_prach_scheduled`

The DU `du_prach_job_in_past_warning` marker is independently counted as **nonconclusive**. Existing Stage5R/Stage5ZB already observe `rrc_sib1_decoded`, UE Msg3, RA success and generated RRCSetupComplete, CU RRC receipt, AMF ingress, NAS timeouts, and fresh PDU acceptance. No raw lines, UE identities, RNTIs, NAS contents, PLMN identities or subscriber credentials are exported.

`stage5zc/analyze_sib1_reacquisition.py` reads only the original bounded/redacted Stage5R category timeline; partitions each genuine cycle **after** T34 NGSetupResponse; and reports SIB1/RA transition categories, bounded timestamp deltas, and any missing markers. The diagnostic can only be `DIAGNOSTIC_ONLY` or `INCOMPLETE_OBSERVATION`; it can never transform a failed real Stage5P recovery into a pass or authorize production.

## Qualification

Existing GitHub-hosted isolated workflow: 2 independent RFsim sessions x 3 AMF restarts, unchanged pinned Stage5T UE and Stage3 CU behavior. Original Stage5P genuine NAS/PDU/ICMP/F1-U/NG-U recovery gate, Stage5U nine negative evidence controls, and Stage5Y per-cell AMF-ingress verifier remain fail-closed.

On another macro failure, compare **Stage5T deferral -> PHY sync -> SIB1 decoding -> PRACH schedule -> UE Msg3 -> DU/ CU RRC -> AMF cell ingress** and its timing against the bounded NAS expiry, using the same cycle-aligned observation windows. Do not lengthen retry timers, make fake NAS/RA events, or infer causation from event absence alone. A native UE/DU source change should be considered only after a reproducible evidence-backed defect is identified.

**Hard protection:** no changes to `main`, installer, OSM, HP Kubernetes, production subscribers or Near-RT RIC/E2SM-KPM configuration. Formal 3GPP conformance, staging security, rollback, resource limits and operator authorization remain outstanding.
