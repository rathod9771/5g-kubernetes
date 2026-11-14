# H-CRAN OAI UE recovery — accepted isolated research source candidate (14 Nov 2026)

**Source-only promotion. NO DEPLOY. Production decision: NO-GO.**

This directory records a small, **opt-in** source candidate for the pinned
OAI W45 UE. It does not change the default installer, dashboard, CU images,
Open5GS subscriber database or HP cluster. Existing `main` and
`reproducible-installer` H-CRAN Helm profiles remain RIC-free.

## Why these four patches are combined

- Stage5G handles NAS Service Reject cause #9: drops stale GUTI/security
  and starts a fresh SUCI-based registration.
- Stage5I adds bounded T3517-like Service Request retry and RRC idle
  transition when AMF is missing. This is **experimental**, not a claim
  of exact 3GPP timer implementation.
- Stage5K refreshes existing PDU-session TUN IPv4 and idempotent policy
  routes after the AMF restart changes the assigned UE IP.
- Stage5M defers MAC random-access initialization until active UL/DL
  bandwidth parts exist, addressing Stage5L's GDB SIGSEGV site:
  `nr_ra_procedures.c:747` (`init_RA`, null UL BWP dereference).

## Evidence accepted for research promotion

Stage5M `43588828565` PASS: one disposable Open5GS + macro DU + small DU
+ two synthetic UE test, AMF-only stop/restart, no UE/DU restart, both
cause9→fresh NAS registration→PDU setup, both TUN IP reassignments,
6/6 ICMP per UE and packet-captured bidirectional F1-U/NG-U G-PDUs.

Stage5N `43590147030` PASS: **three independent disposable GitHub runners**,
each with an identical archived source-pinned UE image and independently
provisioned test 5GC and synthetic UEs. **All 3/3 passed** both unchanged
UEs' post-fault registration, fresh PDU IPs, 6/6 ICMP per UE and F1-U/NG-U.
The Stage5M defer-RA marker fired 5 times and 2 times in trials 1 and 2,
respectively; no defer marker was observed in trial 3. Thus the guard was
exercised in two passing trials, though no randomized A/B or formal
race-proof establishes complete elimination of all crash paths.

The run IDs, upstream commit, exact candidate source Git blob SHA-1 values,
candidate binary/image IDs and input archive SHA-256 checksums are pinned in
`source-candidate-manifest.json`. CI artifacts expire; retain signed source
and independently rebuild before any new target-specific evaluation.

## Opt-in build and safe review only

The pinned experimental entrypoint is
`scripts/hcran_build_pinned_w45_ue_t3517_tunrenewal_raguard.sh`.
It is **not called from** `install.sh`, release-candidate publication,
OSM catalog management, or Kubernetes. The builder is intended only for a
separate ephemeral Linux RFsim runner, with all required variables explicitly
set and outbound source access. It clones upstream at the exact SHA,
checks the NAS source blob, applies four fail-closed source patch scripts,
checks the file diff, then compiles a candidate container.

Safe source-only tests (no cluster access):
```bash
python3 -m unittest discover -s tests -p 'test_hcran_recovery_source_candidate.py' -v
```

Do not treat historical PASS as an approved deployable image. Remaining
gates: ≥3 repeated AMF fault cycles per session, long soak at controlled load,
ASan/TSan evaluation, negative NAS/RRC timing cases, real RF if applicable,
independent staging and OSM rollback, immutable release provenance and
operator authorization. Full 3GPP conformance is **not** established.

- Stage5M: https://github.com/rathod9771/5g-kubernetes/actions/runs/43588828565
- Stage5N: https://github.com/rathod9771/5g-kubernetes/actions/runs/43590147030
