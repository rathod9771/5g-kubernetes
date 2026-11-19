# D2B native build succeeded; final upload digest-format rejection

**Source run:** https://github.com/rathod9771/5g-kubernetes/actions/runs/44486362732
**Current status:** GitHub Actions FAILURE, despite passing every native build and local evidence step. **Production remains NO-GO.**

The runner reported `ARTIFACT_DIGEST=2a289f1d542fcf0f15ebee6eec07230c2f8aed25f20a177ad966b8b296fd56a4` (bare SHA-256). `workflow_evidence.upload_admission` expected only `sha256:<64 lowercase hex>`, causing `UPLOAD_INCOMPLETE_NOT_EVIDENCE_QUALIFIED`.

The GitHub artifact ID is `14511734253`; its published ZIP digest and independently downloaded ZIP SHA-256 both equal `2a289f1d542fcf0f15ebee6eec07230c2f8aed25f20a177ad966b8b296fd56a4`. Its `supervisor-final.json` records `PASS_BUILD_ONLY_ZERO_NATIVE_TESTS`, `ci-evidence-status.json` records `PASS_BUILD_ONLY`, all 608 supervisor SHA-256 bindings and 641 exported manifest file hashes match, and the upload-manifest classification is `PASS` with zero errors. Native compilation/linkage, ELF inspection, cgroup accounting, offline regressions, evidence export, and actual upload all finished successfully.

Correction: admit **only** either `[a-f0-9]{64}` or `sha256:[a-f0-9]{64}`, retaining exact prefix, length, lowercase hex, artifact ID, prior-workflow-success, and full finalizer requirements. Hosted offline tests exercise the observed digest and both accepted formats; malformed, wrong-algorithm, uppercase, truncated, extended or whitespace-suffixed digests still fail closed.

**This change does not retroactively change GitHub Actions' run conclusion or authorize any protocol runtime, deployment, CI retry, production promotion or modification of protected branches.** Its purpose is to fix the deterministic final upload-verification mismatch without spending another native build.
