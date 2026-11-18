# Stage 5Y — bounded Open5GS AMF InitialUEMessage cell observer

**Status: research-only / NO DEPLOY / production NO-GO.**

## Why Stage5X needs a separate evidence correction

[Stage5X run 44076933593](https://github.com/rathod9771/5g-kubernetes/actions/runs/44076933593)
passed 12/12 genuine RFsim dual-UE recovery cycles across four
independent sessions. All 36 deliberately corrupted evidence copies
were rejected (nine per session). The pinned Stage5T UE/CU experiment
therefore fulfilled the specified 4x3 AMF-restoration RFsim gate.

However, a direct artifact audit of Stage5X diagnostics
(artifact IDs 14327185942, 14327586617, 14327419600, 14327109624)
found that the \`stage5x-f1c-amf-ingress-gap.json\` records still
classified **all 12 cycles as INCONCLUSIVE_INCOMPLETE_LIVE_SOURCES**.
The \`core\` live event category never appeared in the bounded trace;
only the old end-of-run \`amf-snapshot\` source was present. The
correlation report did NOT verify AMF cell ingress, despite successful
independent radio/NAS/user-plane recovery.

The archived \`cleanup-core.log\` data contains actual
\`InitialUEMessage\` lines, but its corresponding
\`RAN_UE_NGAP_ID[...] CellID[0xe0101/2]\` metadata arrives **two
or three lines later**. The Stage5X one-line regex could not pair
those records. A conservative offline structural check on the
four archive \`cleanup-core.log\` snapshots confirms they contain
four unambiguously paired InitialUEMessages per trial (both synthetic
cells represented). These are partial retained snapshots, NOT
cycle-complete live AMF-ingress proof.

## New Stage5Y observer

The AMF remains a synthetic Open5GS function in a **disposable**
GitHub Actions core container. Stage5Y does not change UE/CU/core
protocol behavior. Instead it:
- polls **only** the test AMF log files
  \`/tmp/stage4k-log/*amf*.log\` through \`docker exec\`, with
  a 600-line per-file tail, maximum 2 MiB response, 7 s timeout
  and 1.5 s interval;
- associates an \`InitialUEMessage\` only with **one**
  \`CellID[0xe0101/0xe0102]\` occurring in the following five
  lines; cross-message/ambiguous matches are ignored;
- emits **cell ID + event category only** into the bounded
  existing Stage5R protocol timeline; no raw IMSI, SUPI,
  SUCI, RNTI, NAS payload or subscriber credentials persist;
- separately flags repeated inaccessible sources, skipped
  log-tail ranges and critical event overflow as **incomplete**
  rather than silently creating a PASS;
- independently requires \`amf_initial_ue_cell\` observations
  for **both** synthetic cells in **each of three** consecutive
  successful AMF restoration cycles, in addition to existing
  real NAS/PDU/ICMP/F1-U/NG-U and nine negative-control gates.

Only cell-level AMF InitialUEMessage presence is asserted; a
subscriber's exact NAS contents and end-to-end NGAP message path
are NOT proved by an AMF text log. Missing AMF-cell evidence
fails the Stage5Y observer gate without changing genuine recovery.

Stage5Y initially runs **two independent 3-cycle RFsim sessions**
to qualify this new instrumentation while preserving the
same pinned Stage5T UE/CU source, synthetic subscribers, data-plane
requirements, and production **NO-GO**. Main, reproducible
installer, OSM and user's HP lab are protected.
