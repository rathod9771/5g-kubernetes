# Stage D2B — post-worker cgroup-accounting qualification

**Decision: PASS for the configuration-only diagnostic; NO-GO for native compilation / protocol runtime / production.**

## Reproducibility and provenance

- Final GitHub Actions run: https://github.com/rathod9771/5g-kubernetes/actions/runs/44278442399 — **SUCCESS**.
- Reviewed commit: `5fe9eb17027cebb0f944f2c3a1dc819c55352366`.
- Separate offline-only review: https://github.com/rathod9771/5g-kubernetes/actions/runs/44277840378 — **390/390 tests PASS**.
- Final diagnostic also passed 390/390 hosted offline tests.
- Uploaded artifact ID `14416295624`, retained until 2026-12-02.
- Artifact SHA-256 `29f6a4b0d2357e4608ff526bb610256c67f5d0c788ea71948dc0d333bc49879e`.
- Independent artifact integrity: all **85 of 85** upload-manifest hashes and **53 of 53** supervisor SHA-256 evidence bindings match.

## Terminal guard proof

1. A reviewed, unprivileged guard process launches exactly the prior Bubblewrap worker under the existing systemd unit and the unchanged limits.
2. The worker exited with code **78** (`OPEN5GS_RSP_INDEPENDENT_REVIEW_REQUIRED`).
3. The guard remained alive briefly, with **PID 3353**; the worker had been **PID 3355** and was fully reaped.
4. The supervisor verified one remaining cgroup process, `pids.current=1`, no child cgroups, and an exact systemd main-PID match.
5. With only the guard running, the supervisor captured the exact cgroup v2 resource counters, then wrote the release signal.
6. The guard exited with code **78**, matching the child; systemd observed `Result=exit-code` and a terminal unit state.

**Accounting mode:** `POST_WORKER_QUIESCENT_GUARD_HELD`.
At counter capture, the cgroup was **not empty** (the guard held it open). The snapshot is a verified post-worker/quiescent boundary, followed by a separate terminal systemd outcome. This is materially different from claiming an empty-cgroup snapshot or promoting stale last-live data.

## Bounded resource readback

| Metric | Evidence |
|---|---:|
| Peak memory | 606,339,072 bytes |
| CPU usage | 16,968,750 microseconds |
| Peak tasks | 11 |
| Sampled peak ext4 usage | 261,832,704 bytes |
| OOM kill events | **0** |
| PID-limit events | **0** |

Enforced limits remained 6 GiB memory, zero swap, 200% CPU, 256 tasks, and 12 GiB preallocated ext4 scratch. The disk-usage peak is sampled; the capacity limit is hard.

## Open5GS link evidence and native-build boundary

| Generated library | Object inputs | Expanded link SHA-256 |
|---|---:|---|
| NGAP `libogsasn1c-ngap.so.2.7.5` | 1,062 | `784bd8af203dd0dd0b9e26c2410235a1ad6b63350e7626caf1f35d37d099a00a` |
| OpenAPI `libogssbi-openapi.so.2.7.5` | 1,350 | `82cc28eec4b65f9799c75a26470be14ee179c7d395075a7f4729671605f4516c` |

The commands were expanded with Ninja's read-only `compdb -x` metadata tool. No native build, linker invocation, binary inspection or 5G protocol runtime was attempted. No `srsran_compilation.receipt.json` or `open5gs_compilation.receipt.json` exists.

## Next qualification gates

- Independent reviewer approval of the post-worker quiescent accounting contract, including failure behavior for guard timeout, unexpected children, missing status or resource-limit events.
- Separate explicit authorization for one bounded, source-pinned **native-build-only** GitHub-hosted run. Such a run must include complete compile/link receipts, ELF inspection, terminal guard evidence, 0 protocol runtime operations, and verified export hashes.
- Do not merge to `main`, deploy, or touch the on-prem Kubernetes/Open5GS workload as a consequence of this report.

**No native-build dispatch or production promotion is authorized by this diagnostic.**
