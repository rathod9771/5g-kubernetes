# Stage 5Q — bounded BWP0 recovery research candidate (NO DEPLOY)

## Why Stage 5P did not qualify

Run 43799425467 (16 Nov 2026) had two independent GitHub-hosted
three-cycle sessions using the same archived Stage5M UE binary:
- Session 1: **3/3** successive AMF failures restored both UEs' NAS/PDU,
  new PDU IPv4, F1-U/NG-U packet captures and six ICMP replies per UE.
- Session 2: **FAIL cycle 1**. Macro UE stayed Running/zero restarts but
  0/6 ICMP in 19 attempts; small UE completed new PDU setup and 6/6.
  CU NGAP re-established, but macro was sent Service Requests repeatedly
  without Service Reject and exhausted Stage5I's five attempts.
  The macro UE then repeatedly emitted Stage5M BWP-deferral warnings.
  No new PDU IP or proven user plane for macro.
- The initial issue here is not another Docker exit 139; the failure
  is an intermittent RRC/MAC access liveness stall.

Pinned OAI W45 source paths inspected:
- \`main_ue_nr.c\` \`release_mac_configuration()\` sets
  \`current_UL_BWP/current_DL_BWP = NULL\` on full GO_TO_IDLE.
- \`config_ue.c\` \`nr_rrc_mac_config_req_sib1()\` normally reconfigures
  BWP0 and restores both active BWP pointers.
- Stage5M guard avoids null dereference in \`init_RA()\` but can defer
  indefinitely if the SIB1/configuration path does not finish.

## Candidate behavior

The test-only \`patch_mac_bwp0_liveness.py\` runs strictly after Stage5M.
When active UL/DL BWP pointers are missing, it looks up (but never allocates)
MAC-owned BWP0. The pointers are reused only when both BWP0s and the MIB
exist, the UL BWP has an existing \`rach_ConfigCommon\`, and both BWPs
have nonzero allocations. Otherwise, random access remains blocked.
Rate-limited diagnostic messages at powers-of-two deferrals record the
observed MAC state and BWP0 list counts instead of logging every slot.

This **might** resolve the non-recovery if cached BWP0 is still configured.
If the arrays are empty because SIB1 acquisition was lost, it will still
fail (correctly), but evidence will identify that distinct lifecycle defect.
Do not claim the theory is proven before a rebuilt binary passes real
two-UE, three-cycle AMF fault experiments. An untested cached-BWP0 rebind
is not a specification-conformant recovery procedure by itself.

## Acceptance and release policy

- Frozen upstream source commit and fail-closed Stage5G/5I/5K/5M patch
  order; build a fresh candidate image using only an ephemeral runner.
- Each of three consecutive real AMF stop/restart cycles must keep both
  original UE processes, prove fresh NAS/PDU, two distinct new TUN
  addresses, dual-user-plane ICMP, bidirectional F1-U/NG-U with separate
  packet captures, and pass independent Stage5I/5K validators.
- Any cycle failure is a **NO-GO**, not a partial acceptance.
- This diagnostic research branch makes no installer, main, RIC, live
  subscriber, Kubernetes or OSM changes. Staging rollback, negative
  protocol cases, heavy-load soak and long-duration metrics remain open.

## 16 Nov artifact transport blocker and reproducible CU rebuild

The first Stage5Q workflow attempts failed **before compilation** at
`actions/download-artifact@v4` with GitHub's `Bad credentials` response
while fetching historical Stage3 CU. The updated workflow removes that
artifact dependency and compiles a source-equivalent test-only CU from OAI
2026.w13 commit `4941f44043e6dcec81b2041e83dbae4bee6f737d`, checking
NGAP source blob `da4108df125218f3470940389fdd4cfa496bd81e` and applying
`experiments/oai-ngap-2026w13/ngap-retry-v2.patch` before building.
This rebuilt CU is not guaranteed bit-identical to the historical archived
Stage3 CU; acceptance applies to the pinned *rebuilt* variant only.
