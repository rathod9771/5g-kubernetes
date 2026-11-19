# Stage 5ZF — RRC SIB1 cache decision vs missing MAC BWP0 (NO DEPLOY)

**Date:** 19 November 2026. **Scope:** isolated GitHub-hosted OAI RFsim; pinned
OAI W45 UE, Stage5T research behavior and Stage3 CU unchanged. Production NO-GO.

## Why Stage 5ZF exists

Stage5ZE [live run 44294377078](https://github.com/rathod9771/5g-kubernetes/actions/runs/44294377078)
passed 3/3 AMF restarts in independent trial 2. Trial 1 passed the first cycle
and failed the second: macro UPF ICMP 0/6 over 19 probes while small UE
recovered. [Offline run 44294377019](https://github.com/rathod9771/5g-kubernetes/actions/runs/44294377019)
passed. The Stage5ZE failed macro timeline was also incomplete as a
**diagnostic**, due to more than 100 observed MIB callback categories, not
because the Stage5P recovery acceptance check was bypassed.

The retained Stage5ZE archive had 92 macro MAC MIB callbacks in the failing
cycle's pre-NGSetup observation interval, with `state=0`,
`get_sib1=0`, and both BWP counts zero; it recorded no SIB1 scheduling,
SIB1 decoding or generated RRCSetupComplete after that AMF restart.

Review of the **exact upstream-pinned** `rrc_UE.c` source reveals a
specific, testable mismatch. A normal RRCRelease may preserve
`SI_info->sib1_validity` under `preserve_camped_context` while the
MAC reset leaves current UL/DL BWP0 unconfigured. Upon subsequent MIB,
`check_si_status(SI_info)` is consulted only if
`!SI_info->sib_pending` and other guards are met; it will request SIB1
if the RRC SI validity flag is false. If the RRC considers SIB1 valid,
there may be no new SIB1 scheduling request even when MAC lacks BWP0.
This remains a **hypothesis**, not an established cause, until both
sides' state decisions are observed in a real failed recovery.

## Probe design

Keep the **same** pinned upstream commit
`dfb296ab867050a7bde81b393fa31b757ada2c83`, Stage5G/5I/5K/
5M/5Q/5T recovery patches, and Stage5ZD source-pinned transition markers.
A separate Stage5ZF builder runs one additional fail-closed source patch
**after** Stage5ZD:

1. At `nr_rrc_ue_decode_NR_BCCH_BCH_Message`, add **log-only** sampled
   RRC SI-decision records: `sib1_validity`, `sib_pending`,
   requested SIB type, RRC state, barred flag, and sample index.
2. Bound repeated MAC MIB configuration logs by a power-of-two count.
   For RRC decisions, log power-of-two samples plus **all nonzero**
   scheduling requests. This leaves original MIB/RA behavior unchanged.
3. In the Stage5R collector, retain only allowlisted event categories,
   bounded scalar state and timing. No SUPI/IMSI/SUCI/RNTI, NAS payload,
   PLMN, credentials, packet payloads or raw log lines enter the timeline.
4. Produce `stage5zf-si-cache-bwp0-gap.json` diagnostic-only by genuine
   AMF cycle, with separate pre-/post-NGSetup samples, original recovery
   PASS/FAIL and an explicit production NO-GO. A cached-validity /
   BWP-absent co-occurrence is *not* packet-level or causal proof.

## Acceptance and boundaries

Two independent GitHub-hosted 2UE/2DU isolated RFsim trials, each with
three actual AMF stop/restart cycles. No attempt to alter recovery logic,
reset cached SI, shorten/lengthen timers, forge SIB1, or skip negative
controls. Retain original Stage5P genuine NAS/PDU/F1-U/NG-U acceptance,
Stage5U nine negative controls, and Stage5Y independent cell-level
AMF-ingress gate. The source-pinned patch must abort on anchor drift
or reapplication. Offline tests cover changed source anchors, safe
sampling, failure preservation, malformed input and original CI gates.

If reproduced with `sib1_valid=1`, `sib_pending=0`,
`get_sib=0`, and empty MAC BWP0 in the same failed episode, investigate
whether explicit SI-cache invalidation or safe BWP restoration is
appropriate under 3GPP Rel-15/16. Such a behavior change requires a
**separate** reviewed experimental patch and full repeatability tests.

No changes to `main`, installer, OSM, HP Kubernetes, production
subscribers or Near-RT RIC/E2SM-KPM. No production promotion.
