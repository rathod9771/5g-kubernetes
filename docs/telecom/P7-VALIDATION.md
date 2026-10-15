# P7 validation and delivery record

Checkout `/home/rclab/5g-kubernetes-cicd`; branch `feature/telecom-cicd`;
HEAD remains `c63aa1c002e0ca0bb1b4100ba3b0457e531a734c`.
No commit/push. Existing P1–P6 uncommitted work was preserved.

## Final results

**167 relevant tests passed in 187.897 seconds**, exit 0, against final runtime source.
Log: `/tmp/telecom-p7-accepted-regressions.log`.
This includes **28 new P7 tests**, prior telecom MVP/execution/browser coverage and
OSM packaging/TLS/ingress plus monitoring regressions. It is not a claim of running
the entire project suite in P7. Earlier incremental 164/165/166-test runs also passed;
final additions cover traversal, bounded PAX timestamps and canonical registry names.

Reproduction used the real Helm binary rather than the host retry wrapper:

```sh
cd /home/rclab/5g-kubernetes-cicd
P7_TEST_BIN=$(mktemp -d /tmp/p7-test-bin-XXXXXX)
ln -s /usr/local/bin/helm.real "$P7_TEST_BIN/helm"
PATH="$P7_TEST_BIN:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:scripts \
HELM_BIN=/usr/local/bin/helm.real python3 -m unittest \
  test_telecom_registry test_telecom_execution test_telecom_mvp test_telecom_browser \
  test_osm_packages test_osm_tls_trust test_osm_ingress_tls test_monitoring -v
```

Tests cover success/digest readback/idempotence; scoped credential, TLS and digest
failure; interruption/cancellation; missing tools; timeout/resource/request watchdogs;
worker certificate identity and protected-address denial; archive bombs/traversal;
cross-tenant reads/actions; registry policy drift/expiry; blocked old unpublished
build promotion; NBI use without osm_im; default OSM identity denial; missing catalog
approval; no Docker fallback; existing operator and research dashboard behavior.

External BuildKit/Skopeo/NBI mutation transports are test doubles, not real services.
The actual command supervisor is exercised with disposable local processes for
request-budget cancellation, output/RSS/time bounds and child cleanup. No native
Skopeo/BuildKit installation or real push was needed to pass these offline tests.

## Final reproducible demonstration

`/tmp/telecom-p7-delivery-demo/result.json`: COMPLETED, audit integrity true,
AWAITING_APPROVAL catalog request, catalog_published false, instance update
NOT_VERIFIED, production_eligible false, production_mutations 0.

Real operations: local Git object capture/pinned commit, OCI byte/hash validation,
Helm lint/render, package/archive generation, private durable journals and six
loopback HTTP dashboard checks. BuildKit certificate/build commands and Skopeo
registry commands are explicitly OFFLINE doubles. actual_buildkit_or_skopeo_run=false.
The generated empty OCI fixture is not a functioning telecom NF.

Image digest:
`sha256:cb385cd9d2d13ab49c134df93869f345b7256fa991a98a257c1033a05951f646`.
It differs from the initial provider-declared placeholder and is propagated to
chart bytes, package identity, release and registry receipt.

Demo duration **8.08 seconds**, CPU **0.78 CPU-seconds** (0.66 user + 0.12 system),
GNU time peak RSS **43,264 KiB / 42.25 MiB**. This is rusage peak RSS, not concurrent
process-tree memory and not an actual build/push overhead measurement.
The loopback server stopped. No continuous worker/collector remains from the demo.

## P7 file inventory

New:
- `scripts/telecom/registry.py`
- `scripts/telecom/demo_registry.py`
- `tests/test_telecom_registry.py`
- `docs/telecom/P7.md`
- `docs/telecom/P7-LIVE-TEST.md`
- `docs/telecom/P7-VALIDATION.md`

Extended:
- `scripts/telecom/execution.py` — client observer/configuration isolation,
  worker certificate/IP/resource contract, bounded OCI TAR/PAX validation.
- `scripts/telecom/pipeline.py` — durable build/publication/readback stages,
  package/release lineage, fresh exact receipt/policy gates and non-default NBI scope.
- `scripts/telecom/api.py` — tenant-scoped registry-publication read view.
- `ran-selector/telecom.html`, `ran-selector/telecom.js` — nine views and explicit
  deployment-disabled status.
- `tests/test_telecom_execution.py`, `tests/test_telecom_browser.py` — current worker/
  registry boundaries and nine-view expectations; valid OCI layer fixture.
- `docs/telecom/README.md`, `docs/telecom/SECURITY.md` — current P7 entry point/review.

## Safety and residual verification

Syntax checks, JS checks and git diff --check passed. Candidate secret/artifact
screening found only the same three inspected synthetic rejection-test strings;
no real credential, private key, kubeconfig, database, generated archive or log is
among Git candidates. Staging is empty. Regex screening is heuristic, not proof
about all provider inputs. Evidence/auth fixtures stay under /tmp outside Git.

No P7 change to Helm deployment trees, OSM package catalog inputs, config/deploy
resources, production service units or Layer 3 policies/executors. No production
restart, live registry/catalog write, workload deployment or infrastructure install.
The P6 research-only launcher is reused; backend.py was not changed in P7.

Software path is IMPLEMENTED/OFFLINE_TESTED. Real BuildKit/Skopeo behavior, worker
quota/lease/egress enforcement, registry TLS/RBAC/request-trace coverage, actual
push/readback and OSM project/account/publication remain UNVERIFIED. Debug counts
are observed traces, not independently certified wire-request totals. Registry/proxy
limits must be certified in the approved isolated test. Unknown receipt recovery is
manual/read-only; no automatic retry/reconciliation is supplied.

Next step is only the separately approved test in [P7-LIVE-TEST.md](P7-LIVE-TEST.md).
It stops at a catalog request. Publication, instance upgrade/rollback and production
activation still require distinct approval and certification.
