# Offline validation record

Baseline: `c63aa1c002e0ca0bb1b4100ba3b0457e531a734c`, branch `feature/telecom-cicd`.

Validation results are recorded after the final checks below. Earlier incremental
runs passed the real offline HTTP demo and 26, then 27 new MVP regressions. A
173-test compatibility run found a test-only streamed-response buffering issue;
that adapter was fixed, with the existing assertions retained. No live tests run.

- Complete regression run: **645 tests passed in 362.476 s**, including existing
  Layer 3 regressions and all 16 simulations.
- Final updated MVP/browser/monitoring run: **57 passed in 44.055 s**.
- Assurance/central-auth compatibility: **33 passed in 58.614 s**.
- Additional final contract checks: **6 passed**; replay/contention/end-to-end checks:
  **3 passed**. Counts overlap; the current tree contains **652 distinct tests**
  covered across these runs, not one claimed 652-test run.
- Python AST checks: **105 files passed**; new JavaScript syntax and retired shell
  syntax passed; `git diff --check` passed. Actual Node VM browser tests cover all
  six views, authenticated submissions, credential forgetting and no cross-origin
  token forwarding.
- One earlier full-run ResourceWarning came from a test response left open; the
  test now closes it and the final focused run has no warning. No assertion was removed.
- Secret scan: 50 changed/new source/documentation/test files; three inspected hits
  were synthetic rejection-test sentinels. No real credential/private-key finding or
  runtime database/auth file/archive/log in Git candidates. No staged changes.

Final persisted demonstration: `/tmp/telecom-mvp-delivery`. All 15 write requests and
six read views returned HTTP 200. Two sealed releases, four exported KNF/NSD archives
with matching reported SHA256 hashes, one immutable interoperability pair and one
synthetic L3 incident were produced. Audit integrity passed. The qualification is
**SIMULATED_WITH_ISSUES**, `production_qualified: false`, production calls: zero.

Measured final demo: **6.97 s wall**, **0.56 s user + 0.10 s system CPU**; GNU time
`ru_maxrss` maximum **43,392 KiB (42.375 MiB)**. This is the rusage maximum, **not**
simultaneous process-tree RSS or current guardian memory. Validation subprocesses
have separate 8 s/output/RSS bounds, single-core Go configuration and cleanup.


The demo uses synthetic OCI identities and protocol PASS assertions, real local
Helm lint/render, existing OSM descriptor/archive validation, separate fake
reviewer/operator identities and the existing L3 engine in a separate simulation
journal. Generated JSON/HTML/packages/journals stay under /tmp, outside Git.

Production source/workload mutation, real network-function tests, image builds,
registry operations and OSM/Kubernetes actions are not part of these results.
