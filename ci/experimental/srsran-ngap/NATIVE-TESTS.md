# Stage B1 native mock correction and test inventory

Prepared against experimental commit `91736e4cd46612585e874cb50fb7ea001fc261eb` and upstream `11c9bbabb69873752500d676f55e0034f6caa5c5`.

Only the native test harness and CI evidence checks change. The ten Stage A files, complete source patch and upstream revision remain unchanged. No reapplication to the isolated source, HP native compilation, publication, CI rerun or production operation is part of this preparation.

## Native assertions prepared, not yet compiled or executed

The `l3_ngap_snapshot_component` executable runs 20 separately named synthetic cases, each using fresh bounded source state. Failure exits nonzero with the case name/line; verbose CTest retains individual RUN/PASS output. The CTest registration is one test containing 20 cases, not 20 separate CTest registrations.

| Case | Expected assertion |
|---|---|
| default_off | Unconfigured and fully configured-but-disabled source creation returns null; no queued work |
| setup_success | Initial UNKNOWN; matching successful setup becomes UP with generation/transition metadata |
| setup_failure | Matching explicit failure becomes DOWN/setup_failed |
| independent_amfs | Loss of AMF 0 leaves AMF 1 UP |
| stale_after_disconnect | Old successful completion cannot restore DOWN or advance revision |
| stale_after_reconnect | Old completion cannot satisfy a new pending generation; old failure cannot undo newer UP |
| duplicate_success | No state/revision/generation/transition-time change |
| duplicate_failure | No state/revision change |
| contradictory_success_then_failure | Retires generation and becomes UNKNOWN; old success cannot restore it |
| contradictory_failure_then_success | Contradictory success remains UNKNOWN |
| incorrect_peer | Unexpected AMF name becomes UNKNOWN/peer_mismatch |
| superseded_snapshot | Later loss invalidates the previously published ticket and scrubs states |
| rejected_defer | Rejected queue leaves no work and releases busy admission; next accepted request works |
| shutdown_queued | Close before deferred copy produces no valid snapshot or heartbeat |
| shutdown_published | Close invalidates already published snapshot and rejects further reads |
| incarnation_mismatch | Wrong expected process identity invalidates/scrubs result |
| bounded_size | Compile-time/runtime 64 KiB snapshot bound and excessive peer-count rejection |
| deferred_ownership | No inline publication, queued task moves once, one outstanding ticket, admission released on ticket destruction |
| removal_generation | Removal becomes DOWN; stale removal cannot clear reconnected UP |
| publication_metadata | Wall/monotonic stamps present; repeated inspection does not refresh sequence/timestamp |

The two verified compilation errors are fixed using `pending.is_empty()`, the exact public API in approved `include/srsran/adt/unique_function.h:276`. Moves into/out of the pending task retain unique ownership; the mock asserts moved-from emptiness. There is no listener, socket, service, live protocol peer or recovery action.

## Freshness and concurrency limitations

The unchanged header directly calls `steady_clock::now()` and `system_clock::now()`; it has no clock injection seam. Exact queue-age 10 ms and snapshot-age 30 s boundary tests cannot be deterministic with this interface without clock substitution or source changes. Neither is introduced here. No sleeps, arbitrary waits or expiry-pass claims are used. `publication_metadata` checks timestamp immutability only. The preserved Python clock model covers synthetic expiry; that is not native expiry validation.

The native smoke assertions still depend on the real source admitting immediate owner reads within its 10 ms budget; severe CI scheduling delay can fail closed. Do not automatically retry or relax freshness when that happens. The single-thread owner mock demonstrates deferred/move/admission behavior only; it does not prove strand affinity, race freedom, lock-freedom under contention, manager/executor destruction safety or hard-real-time performance. No sanitizer or stress testing is enabled. Multiple local AMF rows do not establish multi-AMF protocol support of the upstream community implementation.

## Build, filters and evidence gates

Existing CMake target wiring remains unchanged: the C++17 `l3_ngap_snapshot_component` target compiles the real snapshot header and links `srsran_support`/`srslog`. `srsran_cu_cp` and `cu_cp_test` remain native targets. Prior run archived the instrumented CU-CP library; the corrected test executable's successful compilation/linkage must be established on the separately authorized runner.

The unchanged GoogleTest filter `cu_cp_connectivity_test.*:cu_cp_setup_test.*` matches actual fixture declarations and TEST_F cases in the approved source. CI now performs a bounded, network-isolated `--gtest_list_tests` first and rejects an empty/missing suite. After native execution, XML must show completed, non-skipped tests in both suites, consistent totals and zero errors/failures. Twelve Python parser tests cover fail-closed handling; they are non-native validation only. CTest uses `--no-tests=error`, verbose output and a 30-second component timeout.

Runner remains GitHub-hosted Ubuntu 22.04; two build jobs, 75-minute build timeout, 100-minute workflow timeout, contents:read token, immutable Actions and no persisted checkout credential. The workflow itself is unchanged. No production secrets or Kubernetes/OSM operations are added. Existing artifact collection after successful tests is unchanged; failure-time binary/cache hash collection remains a known limitation from Stage B, not silently claimed as fixed here.

Input integrity is regenerated in SHA256SUMS. A commit/push of these paths would trigger the branch-restricted workflow; do not commit, push or dispatch until separately approved.
