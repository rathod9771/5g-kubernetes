# Stage 5T — preserve UE SIB1 acquisition when NAS triggers access after AMF failure

**Stage: research-only source candidate. No HP, OSM, real SIM, or production deployment. NO-GO.**

## Stage 5S reproducible failure

On 17 November 2026, [Stage5S run 44025289740](https://github.com/rathod9771/5g-kubernetes/actions/runs/44025289740)
finished with one 3/3 PASS session and one FAIL at cycle 1. In failed trial 2,
macro UE re-registered, re-addressed to 10.45.0.4 and recovered 6/6 ICMP,
but small UE retained old IP 10.45.0.3 and failed 0/6 for 19 post-fault
probes. No containers crashed.

Saved bounded Stage5R timeline from trial 2 (artifact 14303173410)
isolated the small UE's failure:
- Five NAS Service Requests, five T3517-like timeouts and retry exhaustion;
  **no** NAS Service Reject / cause-9 processing / fresh PDU accept.
- Initial postfault PRACH and RRCSetupComplete did occur. Later attempts
  did **not** restart PRACH as needed.
- Stage5Q BWP-deferral sampled counts 1, 2, 4, ... 262144, all reporting
  \`ul_count=0\`, \`dl_count=0\`, \`mib=1\`, \`configured_ul0=0\` and
  \`configured_dl0=0\`.
- The macro UE logged fresh SIB1 decoding and completed cause-9 re-registration.
- The small UE's post-fault protocol timeline did not include fresh SIB1
  decode once it entered the BWP-starved RA state.

## Pinned source-level race hypothesis

The exact upstream OAI W45 UE source at
\`dfb296ab867050a7bde81b393fa31b757ada2c83\` has:
- \`GO_TO_IDLE\`: \`nr_ue_init_mac\` resets MAC to \`UE_NOT_SYNC\` and
  \`release_mac_configuration\` clears active UL/DL BWP entries; it then
  requests PHY synchronization.
- \`nr_rrc_mac_sched_sib\`: \`UE_NOT_SYNC\` transitions to
  \`UE_RECEIVING_SIB\` once SIB1 scheduling is requested.
- \`nr_rrc_mac_config_req_sib1\`: configures UL/DL BWP0 and transitions
  \`UE_RECEIVING_SIB\` to \`UE_PERFORMING_RA\`.
- \`NAS_INITIAL_UL_TRANSFER_REQ\`: buffers NAS for RRCSetupComplete but
  calls \`nr_rrc_trigger_mac_ra(NR_MAC_RA_START_SETUP)\` immediately.
- \`nr_mac_start_ra\` in original \`config_ue.c\`: unconditionally forces
  \`UE_PERFORMING_RA\`, potentially before SIB1/BWP0 is populated. A
  pending SR can therefore bypass the required SIB1 reception state.

This is a **causal hypothesis**, not proven fix. Stage5M/5Q correctly
prevent a null dereference or a fabricated BWP, but cannot by themselves
restore a BWP array that has been emptied.

## Stage 5T isolated patch behavior

Only \`nr_mac_start_ra\` is changed: if either active UL/DL BWP pointer
is absent during a NAS-initiated RRC setup/T300 RA trigger, **postpone
setting \`UE_PERFORMING_RA\`**, preserving the existing \`UE_NOT_SYNC\`
or \`UE_RECEIVING_SIB\` state and the buffered initial NAS. The normal
MIB/SIB1 configuration path must establish valid BWP0 before RA starts.

Never allocate a synthetic BWP, forge an AMF response, restart the UE,
alter supported runtime installer or override Stage5M/5Q guards.

This intervention must be checked with two independent RFsim sessions,
three consecutive AMF stop/restarts per session, unchanged UE processes,
new PDU addresses, ordered NAS, real simultaneous ICMP, and per-cycle
bidirectional F1-U/NG-U captures. If either session fails, **Stage5T
is NO-GO**. Even a complete pass does not constitute 3GPP commercial
conformance or production authorization.
