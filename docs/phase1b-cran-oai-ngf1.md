# Phase 1B0 — C-RAN OAI real NGAP/F1AP smoke (manual, disposable CI)

**Goal:** introduce a genuine single-DU CU ↔ AMF and CU ↔ DU integration
test while preserving the frozen successful two-DU H-CRAN study. This is
the first runtime subgate in Phase 1B, not full 5G SA protocol qualification.

## Implementation (research isolation)

- Branch: `experiment/phase1b-cran-oai-runtime-20261121`
- Trial: [`scripts/experiments/phase1b_cran_oai_ngf1.py`](../scripts/experiments/phase1b_cran_oai_ngf1.py)
- Receipt verifier: [`scripts/experiments/phase1b_verify_ngf1.py`](../scripts/experiments/phase1b_verify_ngf1.py)
- Exact-offline-SHA gate: [`phase1b_verify_offline_run.py`](../scripts/experiments/phase1b_verify_offline_run.py)
- Manual workflow: [`phase1b-cran-oai-ngf1-manual.yml`](../.github/workflows/phase1b-cran-oai-ngf1-manual.yml)
- Source: parent of Phase 1A validated static branch. H-CRAN Stage5ZG
  frozen live-tested commit `7fa911ece3b510b09351bf19c722007365ba47bd`
  remains unchanged. `main` and HP Kubernetes are untouched.

No Docker live integration jobs run on a push. A source push executes
offline negative-contract tests only. Live runtime runs **solely** on an
explicit `workflow_dispatch` event, after the same branch and same SHA
are verified against a successful full `Offline Project Validation` push
run. Workflow has a GitHub-hosted `ubuntu-24.04` runner and NO
Kubernetes/HP kubeconfig, SSH, OSM or subscriber API access.

**Reason for pinned-source build:** the historical Stage3 successful
artifact from GitHub run `43157479388` was no longer listed by the
GitHub artifacts API when preparing this test. The workflow does not
silently assume that downloadable artifact survives; instead it uses the
existing verified `scripts/hcran_build_pinned_stage3_ngap_cu.sh`:
OAI upstream `4941f44043e6dcec81b2041e83dbae4bee6f737d`,
checked original `ngap_gNB.c` blob,
reviewed `experiments/oai-ngap-2026w13/ngap-retry-v2.patch`,
then a source build in the disposable runner. The helper's filename
mentions H-CRAN because it originated in H-CRAN research, but the CU
executable's NGAP patch can be tested with the one-DU **C-RAN** profile.
This produces source-equivalent, not byte-identical, provenance.

The runtime renders **unmodified C-RAN OAI CU/DU charts** with explicit
one-DU radio identifiers (gNB ID 3584, DU ID 101), launches only
disposable Open5GS NRF+AMF, the source-built CU and one DU on a
throwaway Docker bridge, then asserts from the running CU:
- a genuine `NGSetupResponse` received from the AMF;
- `F1 Setup Request` received from real DU ID 101;
- `F1 Setup Response` sent to DU ID 101;
- AMF/CU/DU process liveness and captured SCTP diagnostic information.

The unchanged underlying Stage4C proven harness performs these real
Docker operations; we adapted only namespace, one-DU C-RAN source
identity and report stage names. It never starts UEs, injects faults,
publishes images, or touches HP. Logs and full rendered configs remain
runner-local; only bounded source IDs and sanitized validation receipts
are uploaded. Image tags for DU/Open5GS are historical test tags rather
than independently digest-pinned production release images; actual
locally pulled image IDs are recorded.

## Disposition / limitations

`NGAP_F1AP_RUNTIME_SMOKE_PASS_UE_NOT_TESTED` is the strongest permissible
result even if the trial succeeds. It means the running CU emitted the
expected NG Setup and F1 Setup log markers and processes survived.
It is NOT an independent decoder trace of all 3GPP packets or a
conformance certificate. These remain explicitly `NOT_RUN`:

- NAS registration / authentication / security-mode complete
- PDU Session Establishment Accept with UE-assigned IP
- F1-U and NG-U bidirectional user-plane GTP-U traffic
- UE recovery, handover, multi-DU, MEC, OSM / HP performance

Follow-on Phase 1B1 must introduce a compatible **5G NR** RFsim UE and
isolated synthetic subscribers, SMF/UPF/UDM/AUSF/Mongo etc. with
real N2/N3/F1-C/F1-U and UE tunnel evidence, then capture actual
registration/PDU/user-plane proof. Legacy srsUE is not an assumed
standalone NR SA UE; srsRAN RFsim/ZMQ interoperability needs its own
adapter/proof. Phase 2 fault injection is out of scope here.

## Guarded manual invocation

After the branch's **own full Offline Project Validation succeeds** at the
exact HEAD, use GitHub Actions workflow dispatch with inputs:

```bash
gh workflow run phase1b-cran-oai-ngf1-manual.yml \
  --repo rathod9771/5g-kubernetes \
  --ref experiment/phase1b-cran-oai-runtime-20261121 \
  -f offline_run_id='<successful-exact-SHA-full-offline-run-id>' \
  -f confirm_isolated_no_deploy=ISOLATED_CRAN_NGF1_NO_DEPLOY
```

If GitHub reports the experimental-branch-only workflow is not
dispatchable because the workflow has not been registered on the
repository default branch, **do not merge experimental runtime code
into main merely to force it to run**. Record the blocker for a reviewed
workflow registration/permission solution. Do not bypass the exact-run
verification, substitute another branch's offline result or auto-run
an expensive job.

### Current evidence ledger

- Phase 1A thirteen-scenario static audit:
  [successful #44868622251](https://github.com/rathod9771/5g-kubernetes/actions/runs/44868622251).
- Phase 1A full offline run `44868622250` failed in attempt 1 due an
  unrelated `test_fog_server.APITests.test_counters_are_exact_under_concurrent_processing`
  concurrency assertion. Single diagnostic failed-job rerun initiated.
  This DOES NOT count as a successful Phase 1B run.
- Phase 1B0 real trial: **NOT EXECUTED** until same-commit full offline
  CI is green and an explicit manual dispatch is accepted.
- HP research disk I/O and Longhorn CSI issues remain separately
  tracked in [Issue #15](https://github.com/rathod9771/5g-kubernetes/issues/15).
- All findings are research experimental and production decision = **NO_GO**.
