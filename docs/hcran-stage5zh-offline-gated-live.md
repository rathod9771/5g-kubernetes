# H-CRAN Stage 5ZH — offline-gated live qualification and frozen evidence

**19 November 2026 | NO DEPLOY | production authorization: NO-GO**

## Why this change is necessary

Earlier Stage5Y/5Z*/5ZF exploratory workflows invoked an expensive, isolated
two-UE RFsim rebuild and three-cycle AMF fault-injection matrix **on every push
to the workflow**. Several runs failed **offline test contracts** that demanded
historical experimental branch names or an earlier pinned-source builder in
the active workflow. Those offline failures were often unrelated to the genuine
radio-access issue, but they occurred alongside costly live tests.

Fault injection also reproduced a genuine intermittent UE recovery failure:
macro and small UE failures occurred in different independent sessions. A
green experimental run cannot establish that a recurring race has been fixed.

## Verified stable evidence, not a production claim

The most recent Stage5ZG live run
[44457384676](https://github.com/rathod9771/5g-kubernetes/actions/runs/44457384676),
at commit `7fa911ece3b510b09351bf19c722007365ba47bd`, independently
completed **two sessions**, each with **three real AMF stop/restart cycles**.
Each session retained the same two UE processes, produced fresh PDU IPs,
passed genuine NAS and F1-U/NG-U/ICMP evidence requirements, rejected all
nine negative-control evidence modifications and passed the Stage5Y
per-cell AMF ingress gate. Both bounded Stage5R timelines had **zero issues**.
The source-pinned RRC SIB-send/MAC-dispatch/MAC-schedule call chain was
observed in passing cycles where new SIB1 scheduling was required.

The follow-on test-only commit
`c2cadb476e28e9a06354036d9d205ed21226f19c` passed full
[Offline Project Validation run 44457643438](https://github.com/rathod9771/5g-kubernetes/actions/runs/44457643438).
It changed only the Stage5ZF historical test invariant, not the UE/CU
radio or fault-injection implementation. Treat the result as a **repeatable
experimental reference checkpoint**, not zero known defects.

## Strict two-phase workflow

**Phase A — offline (automatic on source push).** Run the complete
`.github/workflows/helm-validate.yml` offline regression, including
all research/3GPP contract tests, Helm and OSM package validation.
A source revision must reach `status=completed`,
`conclusion=success`. A failed offline run must be fixed before a
live attempt is even requested. Never patch production logic just to
satisfy stale workflow labels.

**Phase B — isolated live (explicit, never push-triggered).** The
`.github/workflows/hcran-stage5y-amf-ingress-observer.yml` now has
**workflow_dispatch only**. To request a run, explicitly provide:

- `offline_run_id`: numeric ID of the successful full offline workflow
  for the **same exact 40-character source SHA and branch**.
- `confirm_isolated_no_deploy`: exactly `ISOLATED_RFSIM_NO_DEPLOY`.

A short GitHub-hosted `offline-gate` job uses the read-only GitHub API
and its ephemeral `GITHUB_TOKEN` to verify owner/repo, workflow name
and path, successful completed result, `push` event, branch and exact
commit SHA. Invalid or stale metadata **fails closed**. The two expensive
matrix jobs require `needs: offline-gate` and cannot start until this
verification succeeds. Only an explicitly dispatched, current and
validated commit may use live CI resources.

The full Stage5P genuine 3-cycle recovery checks, Stage5U nine negative
evidence controls and Stage5Y per-cell AMF-ingress gate are **unchanged**;
no recovery success is fabricated, no retry budget is loosened and no
failure is converted to a pass. The pinned UE/CU source and underlying
radio behavior are unchanged. This is workflow control, not a radio fix.

## Regression maintainability

Legacy Stage5ZB/5ZC/5ZD/5ZF/5ZG unit tests that used to require a
particular, obsolete push-trigger branch now verify
`workflow_dispatch:` and preserve their substantive checks for
source builder syntax, original Layer-3 acceptance, negative controls,
and no production-facing commands. This avoids needless failure when
moving to a newer isolated experimental branch.

The new `tests/test_hcran_stage5zh_gate.py` covers successful exact-SHA
verification; failed/in-progress/cancelled offline records; wrong repo,
workflow path, branch, commit and event; invalid ID; missing explicit
confirmation before any network; read-only token-safe request behavior;
and `needs` / no-push gate workflow semantics.

## Stop conditions and next research step

Do not fire another live test automatically to chase a green status.
First preserve success and failure evidence from the *same pinned* UE
build; compare UE and MAC RRC-SIB message sequence, RA conditions and
NAS retry window on reproducible failures. Successful log-only
instrumentation could perturb scheduling; no causal radio fix has been
demonstrated. If a failure is reproduced with a missing RRC/MAC
handoff, review source-level ownership, timing and queue drain, then
develop **a separate experimental behavior patch with offline tests**,
followed by repeated independent live acceptance. Never promote
without reproducibility and negative controls.

**Hard boundaries:** no `main` update, no HP Kubernetes/OSM changes, no
reproducible installer mutation, no live subscriber/SIM credentials,
no Near-RT RIC or E2SM-KPM, no production rollout. 3GPP Rel-15/16
production conformance, security, rollback and operator authorization
remain outstanding.
