# Stage 4P — H-CRAN dual-UE traffic with FlexRIC KPM indication gate

This *experimental, isolated* GitHub Actions test combines two previously
independent successful proof points:

- Stage 4O: two real OAI RFsim UEs attach to macro DU 100 and small DU 101
  through a patched OAI CU and a disposable full Mongo/Open5GS 5GC. Both
  register, establish separate PDU sessions, receive distinct UE IPv4
  addresses, pass concurrent UE-to-UPF ICMP, and exchange real F1-U/NG-U
  GTPv1-U T-PDU traffic.
- Stage 4I: pinned OAI `2026.w13` E2-agent-capable CU, FlexRIC near-RT RIC,
  compatible KPM/RC service-model libraries and KPM monitor xApp compile,
  set up E2AP and accept a KPM subscription with no UE.

Stage 4P repeats the Stage 4O topology using the pinned *E2-enabled* CU
rather than the non-E2 CU, and starts the KPM xApp **while two UEs have
established PDU sessions**. It sends additional simultaneous ICMP through
both UE tunnel interfaces to ensure live user-plane traffic during monitoring.

## Acceptance checks

The Stage 4P job MUST fail unless all the following are observed:

1. RIC launched, remains running, and receives the CU's E2AP Setup Request.
2. E2AP Setup Successful Outcome returns on actual SCTP packets
   (not assumed from a process starting or a log string alone).
3. NGAP and both DU F1-C setup and SCTP associations pass.
4. Both distinct synthetic UEs successfully register and establish separate
   PDU sessions, with real F1-U and NG-U G-PDUs and zero-loss concurrent ICMP.
5. E2SM-KPM xApp accepts subscription while both UE sessions exist.
6. At least **three** actual KPM indication callback lines occur during the
   bounded collection window. An E2 Setup Response or RIC Subscription
   Response without indications is explicitly insufficient.

## Limitations

The workflow verifies a *small, isolated functional experiment*, not
3GPP Release 15/16 conformance or sustained SLA performance. In particular:

- This run does **not** perform an actual E2SM-RC control action, validate
  control effects on the RAN, test real radio mobility, or perform OSM upgrade.
- Functional E2/KPM evidence would still need a staging soak, failure
  injection under load, independent measurement, immutable image promotion,
  actual OSM rollback and release approval.
- The experiment uses a disposable runner and synthetic subscribers only.
  No HP cluster, production secrets, RF device, OSM or Kubernetes endpoint
  is contacted. No registry image is promoted.

**Current production decision: NO-GO**, regardless of whether Stage 4P
passes. Logs, bounded E2 SCTP capture, data-plane evidence and redacted
xApp output are retained on the GitHub-hosted test run.
