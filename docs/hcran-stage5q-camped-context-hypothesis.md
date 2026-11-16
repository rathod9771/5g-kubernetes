# Stage 5Q — bounded camped-cell retention hypothesis (TEST ONLY)

**Production: NO-GO. No HP host, OSM, installer, Kubernetes or real subscribers.**

Stage 5P (GitHub run \`43799425467\`) reused the SAME two synthetic OAI UE
processes through three consecutive AMF-only restart cycles in each of two
disposable independent RFsim sessions. Session 1 **PASSED 3/3 cycles**,
with separate per-cycle postfault F1-U and NG-U packet captures,
fresh PDU IPv4 reassignment, 6/6 ICMP per UE and zero UE process restarts.

Session 2 **FAILED cycle 1**. The small UE recovered PDU IP
\`10.45.0.3 → 10.45.0.4\` and achieved 6/6 ICMP, while macro UE
retained \`10.45.0.2\`, had 0/6 ICMP even after 19 probes, never received
NAS cause-9 Service Reject/Registration Accept, and exhausted its
five bounded Service Request retries. Macro UE never exited and was not
OOM-killed. UE log tail contains sustained \`Stage5M: defer random access
until UL/DL BWP configuration is ready\` — the guard prevented the
SIGSEGV but did not restore BWP configuration.

Pinned upstream OAI source \`dfb296ab867050a7bde81b393fa31b757ada2c83\`
(\`rrc_UE.c\` / \`config_ue.c\`) reveals a plausible mechanism:

* Stage 5I local forced-IDLE calls \`nr_rrc_going_to_IDLE(rrc, OTHER, NULL)\`.
* With no RRCRelease, the default branch uses \`GO_TO_IDLE\`, which resets
  MAC/PHY, releases common BWP and requires reacquisition of cell/SIB1.
* On a same-cell normal RRCRelease with no redirect, pinned OAI instead
  uses \`GO_TO_IDLE_KEEP_CAMPED\`, retaining common BWP0/paging context.
* Stage 5Q tests retaining existing camped SIB1 via that existing
  reset path **only** on a Stage5I timer-forced idle **and only if**
  the RRC SIB1 pointer and validity flag were already set. Otherwise
  original full cell selection remains. Never forge RRCRelease messages.
* This is an **experimental hypothesis**, not proven safe under actual
  radio link loss, movement, T304/RLF or any physical cell change.

Stage 5Q adds one pinned source patch after Stages 5G/5I/5K/5M,
builds a disposable OAI W45 research UE, and executes the **unchanged
Stage5P three-cycle** real AMF fault and data-plane harness. It never
upgrades the stable installer or live RAN. A compilation PASS is
not a valid 3-cycle recovery PASS.

Promote only after at least two independent three-cycle tests pass
with the same candidate image, plus negative tests ensuring stale
SIB1/cell reselection cannot use the keep-camped shortcut.
