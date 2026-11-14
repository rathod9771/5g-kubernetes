# Stage C3 — proposed deterministic freshness validation

PREPARATION ONLY. Based on validated commit f6954b7ac1327dadc82cf517a8bca12cc4d719b2 and upstream 11c9bbabb69873752500d676f55e0034f6caa5c5. Nothing here has been compiled, published or executed natively. The existing Stage A patch, approved source checkout, Stage B2 and Stage C2 inputs remain unchanged.

## Proposed source change and test-only interface

`source-clock-seam.PROPOSED.patch` adds six preprocessor/declaration lines around only the snapshot header's `now()`. With `SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK` undefined, the original chrono function is byte-for-byte unchanged. With that private macro enabled, `now()` calls `stamp l3_ngap_snapshot_test_clock_now() noexcept`. Only freshness_component.cpp defines the hook. There is no runtime setting, environment clock-control switch, listener, timer, protocol operation, subscriber information or controller callback. config.enabled remains false by default.

The diff is NOT applied to the approved Stage A source. Upon separate approval, prepare_overlay.py verifies the original header and patch hashes, copies the single header into a fresh private overlay, applies the proposed diff there, verifies the proposed hash and confirms the source header is unchanged. No original patch is reapplied locally. The future Stage C2 runner applies the original instrumentation only to its fresh CI clone as already reviewed.

## ODR and target isolation

One translation unit instantiates the test-clock variant in one standalone executable. Its target gets PRIVATE macro/include paths, with the overlay first. It links only the existing support/log/Threads libraries and their normal dependencies, never CU-CP or NGAP implementations. The validated Stage C2 production and regression targets use their original header and no clock macro/overlay.

verify_isolation.py rejects macro/overlay leakage into ANY other compilation entry, duplicate freshness translation units, wrong include precedence, missing sanitizer flags, or forbidden CU-CP/NGAP executable linkage. After build, Ninja dependency evidence must show exactly the overlay snapshot header for the freshness TU and no snapshot header in linked support/log/fmt source objects. Missing object dependency evidence blocks execution. Symbol checks require a defined test hook in freshness and its absence from normal CU-CP/support/log/fmt archives and all three original test binaries. This avoids linking mixed inline `now()` definitions; symbols alone would be insufficient without the compile/dependency checks.

System libraries are not rebuilt; no whole-process instrumentation claim is made. Static dependency/link layouts and compiler compatibility remain to be established on the runner. Unexpected layouts fail closed and require review rather than automatic fallback.

## Deterministic clock and fixtures

A single-threaded fixture owns one coherent stamp pair. Reads check fixture lifetime and thread identity. Clock writes occur between explicit mock-executor queue drains; no background worker reads this clock. No process-wide clock replacement or interposition occurs. Other support/library clocks remain real. Each boundary creates a fresh fixture, source and ticket. Initial monotonic time is 1,000,000,000 ns and wall time 1,700,000,000,000,000,000 ns; integer additions stay within int64 limits. Peer identities are synthetic; incarnation is the original bounded getrandom-generated value and is never fabricated.

These 15 cases test deterministic freshness semantics, not thread safety. The unchanged 10 Stage C2 cases retain real-thread coverage separately. No sleep or elapsed-wall-time assertion is used to establish a boundary.

| Boundary kind | Delta from request/observation, ns | Expected |
|---|---:|---|
| Publication | -1 | invalid, incomplete, all UNKNOWN; sequence/timestamp zero |
| Publication | 0 | valid, complete, both peers UP |
| Publication | 9,999,999 | valid, complete, both peers UP |
| Publication | 10,000,000 | valid, complete, both peers UP |
| Publication | 10,000,001 | invalid, incomplete, all UNKNOWN; sequence/timestamp zero |
| Inspection | -1 | invalid, incomplete, all UNKNOWN |
| Inspection | 0 | valid, complete, both peers UP |
| Inspection | 29,999,999,999 | valid, complete, both peers UP |
| Inspection | 30,000,000,000 | valid, complete, both peers UP |
| Inspection | 30,000,000,001 | invalid, incomplete, all UNKNOWN |

Every inspection preserves the published timestamp and sequence, including invalid inspection results. The additional cases verify retained heartbeat/transition timestamp immutability through the inclusive freshness limit; old-ticket invalidation after a revision with a fresh DOWN/independent UP snapshot and advanced generation; shutdown invalidation and request rejection; process-incarnation mismatch without corrupting the correct ticket; and default-off rejection with otherwise valid configuration.

## Proposed CI, not executed

Future branch: feature/layer3-srsran-ngap-freshness-stage-c3, created from validated Stage C2 only after review. New workflow: .github/workflows/experimental-srsran-ngap-freshness.yml. Exact branch/path push trigger and job guard; separate ASan, UBSan, TSan matrix; max-parallel 1; GitHub-hosted ubuntu-22.04; contents:read; pinned checkout/upload actions; no persisted checkout credentials or production secrets. Never dispatch AND push-trigger the same experiment.

First run the unchanged Stage C2 runner, including all 53 actual native cases. Only on PASS run the new freshness runner in the same mode. It creates the overlay, reconfigures only the disposable CI source/build, adds the PRIVATE freshness target, compiles it, verifies isolation and runs exactly one CTest registration containing the 15 named cases. The result parser requires every RUN/PASS marker in order, no duplicates/skips/failures. Results do not become native evidence until execution actually happens.

Prior admission minimums remain 2 CPUs, 5 GiB available RAM and 15 GiB free disk; compiler parallelism 2; only one sanitizer job active. Original build cap 75 minutes; C3 configure cap 3 minutes; incremental freshness build cap 5 minutes; CTest 30 seconds inside a 60-second outer watchdog; native step 90 minutes; job 100 minutes. These are budgets, not peak resource measurements or a hard memory limit. An incremental rebuild exceeding its budget fails rather than shifting compilation to HP. Network namespace isolation and privilege drop match Stage C2. No AMF connection, deployment or installation.

No suppressions, retries, expiry fallback or automatic corrections. Keep any sanitizer finding and fail. An unsupported TSan runtime is INCONCLUSIVE, never PASS. UBSan retains upstream no-vptr checking because RTTI is disabled. A successful finite test is not universal race-freedom proof or real fault-diagnosis validation.

## Evidence and approvals

SHA256SUMS pins all new inputs and the new workflow. preserved-c2-hashes.json pins all old Stage B2/Stage C2 inputs. source-manifest.json pins approved revision, original Stage A patch, baseline header, proposed patch and resulting overlay header. CI checks the ten original Stage A file hashes before and after C3 and records the overlay hash. Retain compile/link commands, dependency evidence, symbols, toolchain/resources from Stage C2, test XML, RUN/PASS output, sanitizer logs, exit status, all input manifests, archive/binary hashes and per-artifact SHA-256 inventories. If job/runner termination prevents complete evidence, report incomplete, never PASS. Binary hashes are retained; compiled binaries are not deployed or installed.

Required approvals: (1) review the exact six-line header proposal and isolated clock contract; (2) approve committing/publishing only the reviewed new files on the separate Stage C3 branch; (3) approve one workflow run with source/dependency downloads, recompilation of the unchanged 53 cases and 15 new cases per sanitizer, and evidence retention. No approval has been issued by this preparation. Source changes, fixes, reruns, runtime testing and deployment remain separate gates.
