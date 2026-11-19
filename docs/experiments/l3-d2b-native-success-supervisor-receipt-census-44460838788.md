# Stage D2B — Complete build blocked by receipt census mismatch (run 44460838788)

**Outcome:** native compilation, linking, binary inspection and final worker
evidence generation all PASS. The GitHub job remains FAILURE because the
supervisor incorrectly classified extra toolchain-helper receipts as missing
native phase receipts. Production and runtime remain NO-GO.

## Independent retained artifact review
- Run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44460838788
- Run SHA: `227017d3c73d708199e539ebcb528a111982dc0c`.
- Worker native phases: 10/10 PASS, including both compilation phases,
  linkage, 177 binary candidates, and final evidence verification.
- 15 expected Meson `.so.symbols` sidecars excluded and recorded.
- The worker directory contains 15 receipts: the ten exact native PHASES plus
  five legitimate auxiliary toolchain receipts.
- Supervisor `verify_complete` requires exactly the ten PHASES, but the old
  supervisor passed all fifteen `*.receipt.json` files. This produced the
  misleading `MISSING_PHASE_RECEIPT` error even though none were missing.
- Last stage status: supervisor BLOCKED, `reason=MISSING_PHASE_RECEIPT`,
  `exit_code=0`, `systemd_result=success`.
- Evidence integrity independently checked: 608/608 supervisor SHA-256
  bindings and 641/641 upload-manifest entries match.
- Verified post-worker quiescent cgroup capture. Peak memory equals 6 GiB
  cap; 22,263 memory.max events, zero OOM kills and zero PID-limit events.
- The uploaded artifact is diagnostic, not a qualified native-build result.

## Scoped correction
`select_native_phase_receipts` admits exactly the ten reviewed native
receipts for `verify_complete`, requires five named toolchain helper
receipts to be present, rejects unreviewed extra receipts and unsafe
symlinks, and leaves helper receipt validation to the independent toolchain
verifier. It never changes compiler execution, the input source pins,
resource limits, the production branch, or runtime authorization.

**The correction must pass hosted offline tests and retrospective artifact
replay before any further full native build is considered.**
