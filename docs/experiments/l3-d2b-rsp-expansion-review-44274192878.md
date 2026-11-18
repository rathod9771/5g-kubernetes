# Stage D2B — configuration-only RSP expansion qualification

**Outcome: PASS for bounded configuration-only diagnostic; NO-GO for native compilation and production.**

## Provenance

- Final successful GitHub Actions diagnostic: https://github.com/rathod9771/5g-kubernetes/actions/runs/44274192878
- Run SHA: `b0f42ba3ea21ba8fe582296d26d70ff82870eea4`.
- Evidence artifact ID: `14413483867` (14-day retention).
- Uploaded ZIP archive SHA-256: `dbff33f57fec0f8b838f537a956f5016e900ece613fd1870dfbae2243e9d72e7`.
- Export manifest: `PASS`. Independently rehashed all 84 referenced files; zero missing or mismatching hashes.
- Offline tests: 375/375 PASS on disposable hosted GitHub runner.

## Bounded read-only Ninja expansion

Used the Ninja metadata tool `ninja -C /work/ogs-build -t compdb -x c_LINKER_RSP` inside the original isolated worker, without calling build targets.
The tool expands `rspfile_content` into command metadata; it does not invoke the compiler or linker.

| Target | Objects | Expanded command SHA-256 |
|---|---:|---|
| NGAP `libogsasn1c-ngap.so.2.7.5` | 1,062 | `784bd8af203dd0dd0b9e26c2410235a1ad6b63350e7626caf1f35d37d099a00a` |
| OpenAPI `libogssbi-openapi.so.2.7.5` | 1,350 | `82cc28eec4b65f9799c75a26470be14ee179c7d395075a7f4729671605f4516c` |

The expanded commands match the independently reconstructed commands derived from retained pinned Ninja rule and edge evidence. No unreviewed plugin flags, custom linker scripts, or nested response-file references appeared in either target command.
The output remains explicitly `INDEPENDENT_REVIEW_REQUIRED` with `native_compilation_authorized: false`.

## Resource enforcement and terminal-accounting limitation

- Supervisor: `BLOCKED`, exit code `78` (the expected review-only stop).
- Worker status: `OPEN5GS_RSP_INDEPENDENT_REVIEW_REQUIRED`.
- Last-live cgroup snapshot: captured under active limits of 6 GiB RAM, zero swap, CPUQuota 200%, pids 256, and 12 GiB enforced ext4 scratch.
- Last-live readback mode: `LAST_LIVE_ONLY__TERMINAL_MISSING__NO_NATIVE_ADMISSION`.
- `terminal_accounting_complete: false`. No terminal cgroup readback was available after systemd removed the unit cgroup.
- Last-live sampled peak RAM: 598,159,360 bytes; no OOM kills and no pids-limit events.
- The supervisor cannot use this last-live snapshot as a substitute for terminal accounting when qualifying a successful native build.

## Control and evidence integrity

- The pinned Open5GS graph admission and source SHA-256 ledgers were verified at runtime.
- No `srsran_compilation.receipt.json` or `open5gs_compilation.receipt.json` was present.
- Supervisor SHA-256 bindings for `open5gs-rsp-expanded.json`, `open5gs-rsp-review.json`, and `resource-usage.json` were independently confirmed.
- The review-only workflow completed **SUCCESS** after handling the deliberate supervisor exit code correctly. This is not a successful native build.

## Decision

**Qualified:** source integrity, offline tests, bounded static linker-expansion evidence, intentional stop, evidence export, and live resource-limit readback.

**Still unqualified:** terminal resource accounting for a successful native build; native compilation/linking; binary inspection, 5G protocol runtime tests, production promotion.

**Do not start a native build or deployment on the strength of this diagnostic.** A separately reviewed design for durable terminal cgroup accounting and new explicit build authorization are required.
