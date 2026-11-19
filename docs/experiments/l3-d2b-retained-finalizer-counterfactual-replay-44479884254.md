# Stage D2B — Complete retained-evidence finalizer replay

**Decision:** PASS for offline/counterfactual validation **only**. Native qualification and production remain **NO-GO**.

- Successful offline GitHub Actions replay: https://github.com/rathod9771/5g-kubernetes/actions/runs/44479884254
- Reviewed source commit: `5f9e98af7f8ba73fe3cf87d2709c627dc22bf02b`.
- Original actual native-build run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44470794446 — remains **FAILED**.
- Original immutable evidence ZIP SHA-256: `982f06e48d3b5d9d5b95bf87cbb86f4a35d15ef435d2f53d863ac012deb1afa8`.
- Counterfactual report artifact ID: `14507378457`, ZIP SHA-256: `fe58fc71909540fce9194d6e1034e1de81afa491ede13982572892a04d714186`.

## Offline replay results

| Gate | Outcome |
|---|---|
| Original failed supervisor status rejected by `validate_native` | PASS |
| Actual pre-collection receipt census (10 native + 1 verification) | PASS |
| 30 dependency-acquisition output hashes against live-source representation | PASS |
| Tampered original acquisition snapshot rejected | PASS |
| Original ZIP upload-manifest hashes | 641/641 PASS |
| Original supervisor evidence bindings | 608/608 PASS |
| Counterfactual `validate_native` with simulated successful supervisor status | PASS |
| Counterfactual complete `validate` (including auxiliary, inspection, input and chronology checks) | PASS |
| Hypothetical changed supervisor status invalidates original signed upload manifest | CONFIRMED |
| Original failed supervisor status restored in temporary copy | CONFIRMED |
| Actual native build, native tests, or 5G runtime performed | NONE |

The reviewer fetched an exact prior failed-run artifact by ID, verified its immutable ZIP digest, and checked all original file and supervisor SHA-256 bindings before exercising the current verifier. In a disposable temporary extraction only, the old supervisor status was made hypothetically successful; the original approved input ledger was restored temporarily in the ephemeral runner checkout so the previous run's auxiliary identity checks remained exact. Neither the original archive nor any deployed system was modified.

**Important:** This is an admission-path replay, not an actual successful build. Compiled ELF bytes were intentionally excluded from the retained artifact, so their contents cannot be reverified offline. The previous GitHub run must remain FAILED, and its old upload signature would reject the altered hypothetical status.

## Next gate

Require independent review of this report before separately authorizing one bounded, single-use, native-build-only hosted experiment at the latest reviewed source. Preserve 6 GiB memory, zero swap, CPU 200%, 256 tasks, 12 GiB disk, and 4,500 seconds systemd limit. Do not launch a 5G protocol runtime, change Kubernetes, merge to `main`, or elevate a counterfactual replay to production qualification.
