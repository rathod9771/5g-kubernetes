# Separate source-change proposal: deterministic snapshot clock tests

Status: DESIGN ONLY. No clock seam is applied, compiled or enabled in Stage C1. The original instrumentation patch and all ten Stage A files remain byte-identical.

## Current authoritative boundaries

In approved `ngap_application_snapshot.h`, `now()` directly samples system_clock and steady_clock. Deferred publication requires `now.monotonic_ns >= requested_at.monotonic_ns` and elapsed `<= 10,000,000 ns`. Ticket inspection rejects a future publication stamp or elapsed `> 30,000,000,000 ns`. Thus exactly 10ms admits publication and exactly 30s admits inspection if all other identity/revision/closure gates pass. Transition stamps represent locally observed events, not remote fault onset.

## Minimum proposed seam, requiring separate source review

Guard a test-only alternative implementation of this header's `now()` behind a private compile definition, for example `SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK`. Normal builds retain the original chrono implementation and receive no new runtime parameter, environment flag, listener or exported clock-control API. The guarded branch would call a test-owned function returning the existing `stamp` struct. Exact code and its updated source hash must be reviewed before use; this document is not an approved patch.

Use a separate freshness-test executable whose sole translation unit instantiates the snapshot header with that compile definition. It may link support/log libraries but must not link CU-CP objects containing another definition of the same inline snapshot code. Otherwise mixed real/fake inline definitions risk an ODR violation. Verify the final production-target compile commands contain no test definition and production symbols contain no test-clock control. Treat accidental inclusion as a build failure.

The test clock must be owned by one fixture, initially wall/monotonic positive, and be changed only at explicit barriers with all affected threads paused. Its returned pair must be internally consistent. Do not use process-wide libc interposition or replace system clocks: shared-library chrono calls, condition-variable deadlines and worker scheduling could otherwise be altered. Real wall-clock wait_for deadlines remain safety watchdogs only.

## Required deterministic table

Each row must use a fresh fixture/ticket, not reuse an already consumed deferred request:

| Measurement | Injected monotonic relation | Expected |
|---|---|---|
| Queue age | requested - 1ns | Invalid; no heartbeat |
| Queue age | 0ns | Admitted |
| Queue age | 9,999,999ns | Admitted |
| Queue age | 10,000,000ns | Admitted |
| Queue age | 10,000,001ns | Invalid; no heartbeat |
| Snapshot age | observed - 1ns | Invalid/UNKNOWN |
| Snapshot age | 0ns | Valid if other gates pass |
| Snapshot age | 29,999,999,999ns | Valid if other gates pass |
| Snapshot age | 30,000,000,000ns | Valid if other gates pass |
| Snapshot age | 30,000,000,001ns | Invalid/UNKNOWN |
| Retained ticket read | time advanced inside valid range | Sequence and observation stamp unchanged |
| Superseded revision | otherwise fresh clock | Invalid/UNKNOWN |
| Process mismatch/close | otherwise fresh clock | Invalid/UNKNOWN |

No real sleeps or arbitrary delays establish these boundaries. Until the seam is separately reviewed and executed, exact native freshness limits remain UNVERIFIED. Existing native timestamp-immutability and synthetic Python clock-model evidence remain clearly separate.
