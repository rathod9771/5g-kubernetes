# P8 validation record

Date: 2026-10-15. Baseline c63aa1c002e0ca0bb1b4100ba3b0457e531a734c,
branch feature/telecom-cicd. No commit, push, service restart, infrastructure
installation, real registry push, catalog write or live NS creation performed.
Prior P1–P7 uncommitted work retained. All journals/artifacts/logs created for
validation were isolated under /tmp.

## Results

- Focused telecom regression run: **118 tests passed**, 327.880s.
  Log: /tmp/telecom-p8-regressions.log. This overlaps the runs below; do not sum.
- Expanded P8 suite: **19 tests passed**, 117.612s.
  Log: /tmp/telecom-p8-final-initial.log.
- Final namespace-controller rejection and complete deployment-history assertions:
  **2 tests passed**, 5.787s. Log: /tmp/telecom-p8-final-deltas.log.
- Complete repository discovery: **733 tests, 731 passed, 1 failure, 1 error**,
  611.971s. Log: /tmp/telecom-p8-full-suite.log. This run collected 17 P8 tests;
  the subsequent expanded suite covers all 19. This is not a clean full-suite run.
- Isolated rerun of the two full-run failures: **2 passed**, 0.367s.
  Log: /tmp/telecom-p8-isolated-failures.log. Neither unrelated test nor its
  production implementation was modified to force success.
- Changed/untracked Python syntax checked: 42 files parsed successfully. The
  final source paths also execute through the P8 tests/demo. git diff --check
  clean. No staged files. Candidate artifact/secret scan found no runtime DB,
  archive, kubeconfig or generated log candidates. The sole private-key marker
  match was the existing literal `BEGIN PRIVATE KEY...synthetic` rejection fixture
  in tests/test_p0a_safety.py, not key material. Pattern scans are not a guarantee
  against every possible secret representation.

Commands (real Helm avoids the installed retrying wrapper):

```sh
PATH=/tmp/git1-review/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:scripts HELM_BIN=/usr/local/bin/helm.real python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:scripts HELM_BIN=/usr/local/bin/helm.real python3 -m unittest test_telecom_initial -v
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m telecom.demo_onboarding --output /tmp/p8-demo-new-directory --helm /usr/local/bin/helm.real
```

## Full-run exceptions

1. test_assurance_kubernetes.KubernetesRunnerTests.test_descendant_timeout_cleanup:
   FileNotFoundError while reading /proc/431852/stat after exists() succeeded.
   The child exited between the two test-side filesystem calls. The assertion's
   race does not demonstrate a production cleanup failure. Isolated rerun passed.
2. test_fog_server.APITests.test_counters_are_exact_under_concurrent_processing:
   assertion that all 30 concurrent requests returned200 failed. The traceback
   did not preserve exact non-200 statuses. Cause is not established; broader-run
   timing/load/admission interaction is a hypothesis. Isolated rerun passed.
   Do not claim an independently established baseline failure or a repaired bug.

Neither failure is in P8 changed functionality. Full-suite repeatability remains
an explicit validation limitation; no protected Fog service was exercised.

## Offline workflow and provenance

Delivery demo: /tmp/telecom-p8-delivery-demo/result.json.

- Source: prebuilt OCI + local Helm; no Git server, BuildKit or Skopeo required.
- Manifest/schema validation, registry fixture manifest/config hashes, real Helm
  lint/render and existing KNF/NSD archive generation succeeded.
- Fixture image digest:
  sha256:cb385cd9d2d13ab49c134df93869f345b7256fa991a98a257c1033a05951f646.
  This is an empty OCI test image, not a runnable HTTP/RAN/Core release.
- Independent catalog and initial-deployment approvals exercised separately.
- Simulated catalog readback PUBLISHED_CONTENT_VERIFIED; exactly one simulated
  create POST; SUBMITTED then WORKLOAD_READY on simulated OSM/pod evidence.
- Durable history: AWAITING_APPROVAL → SUBMITTING → SUBMITTED → WORKLOAD_READY.
- Audit chain valid; production_calls=0, production_eligible=false.
- Observations labeled SIMULATED **before persistence**; direct journal/event
  check found zero fixture observations labeled LIVE.
- Local standalone HTTP fixture /healthz returned200 in an isolated loopback
  test, with server/thread cleanup. No deployed application's health verified.
- App health in deployment adapter remains UNVERIFIED; telecom protocols NOT_RUN.
- Upgrade/rollback NOT_VERIFIED; existing Lifecycle.execute remains denied.

## Source changes attributable to P8

New source: scripts/telecom/initial.py, research_setup.py, demo_onboarding.py.
Modified existing telecom source: api.py (deployment routes/views/freshness),
execution.py (server policy section), package.py (namespace portability evidence),
pipeline.py (shared existing scoped NBI authentication helper).
Dashboard: ran-selector/telecom.html and telecom.js; no backend.py P8 change.
Tests: new tests/test_telecom_initial.py; browser view count updated in
 tests/test_telecom_browser.py.
Docs: P8.md, P8-LIVE-TEST.md, this validation record.
Example: examples/telecom/disposable/{manifest.json,README.md,app.py,Dockerfile,
chart/Chart.yaml,chart/values.yaml,chart/templates/workload.yaml}.
No Helm reference deployments, installer execution, OSM catalog packages or
protected service configuration changed by P8.

## Readiness

IMPLEMENTED / OFFLINE_TESTED: prebuilt onboarding, package generation, request/
approval/create-only adapter, one-shot status reader, isolated researcher routes,
research-only plan, disposable fixture and demonstration.

UNVERIFIED: real OSM v19 initial create response/status fields, project-role
isolation, VIM/KDU namespace placement, actual private image pulls, deployed app
health and telecom interoperability. Research-only setup is a non-mutating plan
plus existing manual loopback launcher, not an automatic infrastructure installer.

No live activation authorized or executed here. Follow P8-LIVE-TEST.md only after
separate approval; it specifies preflight, explicit catalog/initial approvals,
identity checks, stop conditions and separately approved owner-scoped cleanup.
