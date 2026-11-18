# Stage 5Z — observe-only CU AMF selection boundary

**Status:** Research / isolated RFsim / **NO DEPLOY**, production **NO-GO**.

## Trigger

Stage 5Y-A live run [44234327537](https://github.com/rathod9771/5g-kubernetes/actions/runs/44234327537) failed both independent tests during the first AMF fault. The macro UE made zero successful UPF ICMP probes (nineteen rounds of six probes) while the small UE replied six out of six in every round. The macro retained its old PDU IPv4 address, did not observe fresh NAS Service Reject, re-registration, or PDU acceptance, and exhausted its bounded Service Request retries.

The archived post-fault CU logs in both trials contain **CU selection warnings during the AMF outage**, including `No matching AMF found for PLMN` and `No AMF is associated to the gNB`. These are plausible precursors to the macro-cell non-recovery, not proof of where a message was dropped. The original Stage5R CU classifier missed the latter exact wording.

## Stage 5Z instrumentation

The `stage5s/cu_amf_selection_stage5z.py` parser processes only *live CU Docker log lines*, in memory, during the preexisting Stage5R observation. It records category-only markers:

- `cu_amf_selection_no_match`
- `cu_amf_selection_selected`
- `cu_amf_no_association`

A warning/selection is associated with a CU cell only when it appears within five lines of an explicit `[UL] ... Received RRCSetupComplete` for CU cell `3804001` (macro) or `3804002` (small). The association is **bounded log adjacency**, *not* verified UE or NAS identity. Unattributed events remain unattributed. The parser stores no raw messages, subscriber identifiers, transient UE IDs, RNTI, PLMN identifiers, authentication data or NAS content. The pinned UE/CU protocol implementation, AMF handling, and all real recovery acceptance checks remain unchanged.

The independent `stage5z/analyze_amf_selection_stage5z.py` reads the allowlisted Stage5R evidence and partitions each cycle into the AMF outage/reconnection phase (T31 through T34) and the post-NGSetup phase (T34 to cycle end). It correlates CU selection outcome categories with per-cell CU RRC/UL NAS, UE Service Request/PDU markers, and AMF cell ingress, under the limitations stated in its output.

The diagnostic only ever emits `DIAGNOSTIC_ONLY` or `INCOMPLETE_OBSERVATION`, always with production authorization **false**; it does not certify NGAP packet delivery and cannot override a Stage5P **FAIL**.

## Execution and controls

Existing [Stage 5Y workflow](../../.github/workflows/hcran-stage5y-amf-ingress-observer.yml) can be dispatched explicitly against this experimental branch; no change to its original two-session by three-cycle real acceptance or pinned upstream image builders is needed. Stage5R in this branch will passively add the three new category types to the already archived redacted timeline. When the run ends, execute Stage5Z analyzer separately against each downloaded trial artifact, including genuine failed cycles.

Do not compare a post-fault dual-UE run to the pre-fault macro-only baseline as if it were a valid macro-only AMF recovery control. A genuine separate macro-only recovery experiment requires its own isolated qualified harness. No previously passing Stage5X/Stage5Y archive can substitute for new evidence.

**Promotion gates unchanged:** both real UEs must complete NAS/PDU/ICMP and F1-U/NG-U recovery after each AMF fault; all nine independent negative controls must reject corruption; each cell must show per-cycle AMF ingress; all sessions must pass. Stage 5Z is diagnostic, **not a production gate bypass**.

Nothing in this branch deploys to the HP cluster, touches OSM, installs on main, or alters the reproducible installer.
