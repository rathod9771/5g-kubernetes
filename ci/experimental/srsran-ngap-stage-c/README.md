# Stage C1 sanitizer and native concurrency experiment — preparation only

Validated baseline: `2a533ccc8a4f345174e5bf095b33150fef21a82f`, upstream `11c9bbabb69873752500d676f55e0034f6caa5c5`, Stage B2 run `43578159794`. Original patch SHA256: `95a2a9598bdb44deca598c5e26478b276968169f54da538d5173a32432444747`. All B2 inputs are pinned in preserved-b2-hashes.json and unchanged. B2's 23 GoogleTests and 20 named cases inside one CTest remain in every job; the new 10 cases add one CTest. These are 53 named cases in total, not 53 GoogleTest registrations.

## Separate native configurations

Proposed workflow `.github/workflows/experimental-srsran-ngap-sanitizers.yml` has three fresh GitHub-hosted Ubuntu22.04 jobs. Matrix modes address, undefined, thread are sequential (max-parallel1), fail-fast false. Every job has its own source and build directory. No continue-on-error or retry. Actions remain pinned to reviewed checkout/upload-artifact commits; contents:read and no persisted checkout credential, production secret or OIDC grant.

| Mode | Upstream CMake switch | Driver compile/link flag | Runtime |
|---|---|---|---|
| ASan | ENABLE_ASAN=ON; UBSAN/TSAN=OFF | -fsanitize=address | halt_on_error=1, abort_on_error=1, detect_leaks=1 |
| UBSan | ENABLE_UBSAN=ON; ASAN/TSAN=OFF | -fsanitize=undefined | halt_on_error=1, print_stacktrace=1 |
| TSan | ENABLE_TSAN=ON; ASAN/UBSAN=OFF | -fsanitize=thread | halt_on_error=1, exitcode=66 |

All modes retain Debug/WERROR and add frame pointers and no sanitizer recovery. Upstream UBSan disables vptr checking because the build disables RTTI; this limitation is explicit. Minimal UBSan and RTSan remain OFF. Address and thread sanitizers never share a binary. TSan support is NOT established by preparation; compiler/platform/ASLR issues may prevent it. Known `unexpected memory mapping` is marked UNSUPPORTED_RUNTIME with nonzero exit, not PASS. No sysctl, ASLR adjustment, privilege escalation workaround, sanitizer suppression or automatic fallback is proposed. An upstream warning-as-error or sanitizer finding is retained and reviewed before any change.

Required targets: srsran_cu_cp, cu_cp_test, l3_ngap_snapshot_component, l3_ngap_snapshot_concurrency. compile_commands.json and Ninja link commands must demonstrate the selected flag and no incompatible positive sanitizer flags for all CU-CP/NGAP/support/log C++ entries, the owner worker implementation, all three changed translation units, and both snapshot test executables. Final executable undefined symbols must reference the correct sanitizer runtime. The compiler driver also receives explicit link flags. GoogleTest discovery is deferred to PRE_TEST so build-time post-link discovery does not launch instrumented test executables outside the reviewed test environment. Public prebuilt GoogleTest, libc and other system libraries are not rebuilt/instrumented; whole-process coverage is not claimed.

## Actual-thread functional test inventory

The C++ test uses the approved `task_strand<task_executor*,lockfree_mpmc>` over a real task_worker/task_worker_executor. Lifecycle writes, construction, close and destruction run on that serialized owner. Owner tasks check worker thread identity and nonoverlap. Reader threads use only request/inspect; no request overlaps source destruction. A single worker does not cover production pool migration or all interleavings.

| Case | Expected evidence |
|---|---|
| queued_then_loss | Snapshot queued on owner, loss before deferred copy; published state DOWN |
| queued_then_reconnect | Queue before loss/new generation; published state reflects new UP |
| stale_completion | Another thread dispatches old completion to owner; newer UP/revision unchanged |
| shutdown_queued | Owner closes before deferred copy; UNKNOWN, no heartbeat |
| retained_ticket_destruction | Real reader inspections race owner destruction; retained storage remains safe and final state UNKNOWN |
| competing_requests | Two real reader threads make 32 bounded attempts across queued/publication phases; no second ticket, no partial payload, busy released only on ticket destruction |
| executor_rejection | Actual 64-slot strand filled on owner; request rejected, queue drained and later admission works |
| revision_invalidation | Real reader inspections overlap owner loss; complete prior UP or UNKNOWN only; final UNKNOWN |
| independent_peers | Owner-routed loss from another thread changes one peer only |
| publication_ordering | Explicit not-ready inspection, release/fence then fully initialized peer fields, sequence and timestamp |

Promises/shared futures order phases; five-second waits are failure watchdogs, not freshness measurements. No sleeps, polling races, throughput loop or load/stress experiment. Each repeated-reader loop is fixed at16, the rejection fill is bounded at128 attempts, and CTest has a120-second cap. Temporary test-only owner rendezvous never changes production lifecycle code. The actual executor queue, not a sequential mock, handles publication. Thread interleavings are only the scheduled finite cases, not universal proof of race freedom.

Publication-required assertions fail if real queue-age admission exceeds10ms; no expiry-to-success fallback exists. Clock scheduling can therefore produce an incomplete experiment under sanitizers. Report that, never retry automatically or widen freshness. Freshness-boundary precision requires the separately reviewed proposal in FRESHNESS-SEAM.PROPOSAL.md. No source modification or clock-control behavior is included here.

## Resources, evidence and safety

Every job checks at least2 logical CPUs,5GiB available RAM and15GiB free runner-temp disk. Two compile jobs maximum;3-minute configure timeout;75-minute build timeout;85-minute native step timeout;100-minute workflow-job timeout. Public dependency installation and source acquisition each have a5-minute step cap. Three sequential jobs imply at most300 job-minutes,225 build-minutes. These are budgets, not measured usage or a guaranteed memory cap. Actual resources are recorded before build. Do not set RLIMIT_AS: TSan requires large virtual reservations. Sanitizer overhead and compiler memory may exceed runner capacity; abort/report rather than use the HP host.

No external AMF/gNB service, pod operation or production endpoint. Test commands enter a fresh network namespace, then drop to the runner's uid/gid before execution; no privileges are retained by tests. CU-CP discovery30seconds, CU-CP suite240seconds, combined CTest outer180seconds, concurrency CTest120seconds, preserved snapshot CTest30seconds. No fallback if namespace or privilege dropping fails.

Sanitizer log_path files, full console, CMakeCache, compile database, compile/link commands, runtime-symbol checks, test XML, per-case evidence, inputs and binary/archive hashes are retained even on handled failures. No binary is installed or deployed. CTest output budgets are raised for diagnostic capture; complete console/sanitizer files remain primary evidence. Unrecoverable runner loss or whole-job cancellation may prevent finalization; incomplete artifacts must never be called PASS. Sanitizer diagnostics cause failure even if a test report appears green. UBSan and ASan findings are not reclassified as unsupported to hide defects.

## Approval boundary

Nothing has been compiled, committed, pushed or triggered during C1. To execute later, explicitly approve: exact new-file hashes; commit of only the new workflow and stage-c directory on the existing experimental branch; one push that starts one three-job sanitizer matrix; public dependency/source downloads; bounded actual-thread tests and retention of diagnostics. The new exact-branch/path push trigger avoids default-branch registration changes. Existing B2 trigger matches only its original directory/workflow and does not match these new sibling paths. Main, reproducible-installer and existing production/self-hosted workflows are unchanged.

Clock-seam source modification, additional runs, source corrections, sanitizer suppressions, sanitizer stress/performance and isolated live protocol runtime validation need separate approval. No production deployment, Kubernetes/OSM access, credentials, journals, recovery or ProductionExecutor action is authorized.
