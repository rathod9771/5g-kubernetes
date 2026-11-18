# Stage D2B — Open5GS static response-file audit

**Classification: REVIEW_REQUIRED — NO-GO for native compilation.**

## Provenance

- GitHub Actions run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44268486727
- Run SHA: `009b2019c2bf6178547167eb9f4ea61be845cf6e`.
- Artifact ID `14411182397` (14-day retention).
- ZIP SHA-256 `78a950ce5463a7e6b08070e0d543ecff9d070a3e88230538f1443a478d6e37ab`.
- 84 exported file digests independently verified against upload-manifest.json.
- Review JSON SHA-256 `77cc6f78b090d84d41013c0773926f59c28bfec80285125fc96eeb8fd8dfb0da` (matches supervisor evidence binding).
- Generated build.ninja: 6,400,563 bytes, SHA-256 `9229c134e0194868e9481784403c009a28116f487f718c5fdd80bab89f2e0723`.
- Open5GS command graph SHA-256 `9f086ee1c67d2716a69b5ccf45379231897c29a059f2c51d2d3bc5d257e8fd6c` (matches pinned graph).

## Static review result

The two Ninja link targets use rule `c_LINKER_RSP` with command `cc @$out.rsp` and `rspfile_content = $ARGS -o $out $in $LINK_ARGS`.

| Target | Explicit object inputs | Implicit dependencies | RSP materialized? |
|---|---:|---:|---|
| NGAP `libogsasn1c-ngap.so.2.7.5` | 1,062 | 4 | No |
| OpenAPI `libogssbi-openapi.so.2.7.5` | 1,350 | 2 | No |

Visible per-edge LINK_ARGS include normal shared-library flags, SONAME, balanced linker groups, Open5GS libraries, libtalloc, and relative $ORIGIN RPATHs. There were no plugin options, custom linker scripts, or nested @file references in those visible flags. This is NOT proof that fully resolved response files are safe.

## Unresolved gates

1. Neither response file was created, because no linking was attempted. Actual RSP bytes were not collected.
2. Only selected rule and edge blocks, not the full build.ninja variable environment, were preserved; the value of $ARGS and the full Ninja expansion remain unverified.
3. The worker stopped as designed with `OPEN5GS_RSP_INDEPENDENT_REVIEW_REQUIRED`; no native compilation phase receipts or protocol execution occurred.
4. Supervisor final reason: `FINAL_CGROUP_ACCOUNTING_UNAVAILABLE`. systemd returned an empty ControlGroup field after worker exit code 78. The final resource readback remains a separate qualification gap.
5. Hosted offline tests passed 350/350, but the workflow overall failed because of its intentionally blocked configuration-only outcome.

## Next controlled activity

Before any native compilation: independently derive or capture resolved response-file argument bytes using a bounded configuration-only parser with full Ninja variable scope. Bind resolved arguments and hashes to the pinned graph and reject unsafe flags, paths, or ambiguity. Separately repair terminal cgroup accounting without reducing resource enforcement.

**This audit does not authorize any new build, dispatch, production deployment, or 5G protocol execution.**
