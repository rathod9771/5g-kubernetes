# D2B final supervisor timing mismatch — run 44470794446

**Disposition: native build compilation/ELF/worker PASS, supervisor BLOCKED.**
Run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44470794446

The systemd worker exited 0, final worker phase PASS, post-worker cgroup
snapshot complete, 0 OOM-kill and 0 PID-limit events. The supervisor blocked
with `MISSING_TOOLCHAIN_HELPER_RECEIPT` for four acquisition receipts.

Root cause: supervisor validation happens **before** workflow_evidence.collect.
At that moment `/work/evidence` contains the ten native receipts and one
`toolchain-verification.receipt.json`. The four
`acquisition-toolchain-*.receipt.json` files still reside in
`RUNNER_TEMP/l3-d2a-acquired` and are only copied into the published
`worker/acquisition-*` namespace later. The uploaded artifact therefore
contains 15 receipts, but using that post-collection census as the supervisor
precondition was incorrect. Previous fixture tests modeled only 15.

The corrected supervisor demands ten exact native receipts plus the one
available verification helper. Unknown receipt names, missing mandatory
receipts and partially copied acquisition-helper groups remain blocked.
Its original acquisition-output hash check uses the live source directory at
the supervisor boundary, never a nonexistent future alias nor a different
later toolchain-progress revision. Post-collection offline replay still binds
the exact `acquisition-*` aliases by SHA-256. No source pins, resource caps,
compiler logic, transport or protocol runtime controls are changed.

**No run may be marked qualified merely from this offline correction.** A
fresh end-to-end run remains separately gated, and no native build was
dispatched by this change.
