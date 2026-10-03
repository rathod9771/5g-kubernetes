# P0-A review findings: fix disposition

This report covers the 21 findings in the preceding review. Source changes and
local tests only: no cluster, OSM, installation, service restart, Git commit/push,
original diagnostic files or persistent runtime state were changed.

| Previous finding | Status | Evidence |
|---|---|---|
| CRITICAL component shell injection | FIXED | Unknown logs/runtime/events components return 400 without a subprocess. Pod lookup filters Kubernetes JSON in Python. The demonstrated semicolon payload has a regression test. |
| HIGH catalog provenance before termination | FIXED | `Catalog.verify` requires unique NSD/KNF identities and exact downloaded package-byte hashes. Backend pins the verified NSD UUID; absent/stale/ambiguous/unavailable provenance stops before lifecycle calls. |
| HIGH concurrent lifecycle/state updates | FIXED | Local `flock` covers the entire request; state is reread inside it. Concurrent-request test proves sequential termination of old and then newly recorded instances. `atomic_write` uses fsync/replace. |
| HIGH ignored termination/deletion and Running-only teardown | FIXED | Only COMPLETED termination proceeds; deletion and catalog absence must succeed. Resource-name teardown checks include non-Running pods, workloads, services and configmaps, without relying on pod labels. Failed queries block progress. |
| HIGH unrestricted package inputs | FIXED | Explicit file policy, sensitive/credential rejection, symlink/traversal checks and Helm packaging exclusions. Independent fixtures cover ignored files, secrets, caches and editor debris. |
| MEDIUM incorrect Helm merge/parsing | FIXED | Helm itself resolves defaults and ordered profiles through a probe chart; Python does not merge values. Independent expected maps cover nested maps, lists, booleans, null deletion, null-to-map, scalar/map transitions, key coercion and precedence. Dependency charts deliberately fail closed. |
| MEDIUM missing fresh-clone artifacts | FIXED | `prepare` generates/validates/publishes locally. Onboarding calls it automatically before authentication. Fresh-empty-output regression verifies the full local path. Dashboard fails closed with an explicit preparation instruction if no publication exists. |
| MEDIUM inability to refresh default output | FIXED | Source edits publish a new release and atomically advance CURRENT; prior releases/legacy flat outputs remain. Other prepared scenarios are retained. |
| MEDIUM unlocked/nontransactional generation | FIXED | A process lock covers generation and publication. Immutable source bytes are captured and checked before use. Private staging is verified, synced and renamed before pointer publication; published content is checked again. |
| MEDIUM interruption/recovery | FIXED | Pointer failure retains the old release. Orphan partial directories are never accepted. Retry publishes the complete release; no destructive cleanup is needed. |
| MEDIUM validation/upload race | FIXED | Onboarding uploads validated bytes owned in memory, never reopened archive paths. Regression replaces the archive on disk after validation and verifies the original bytes are uploaded. |
| MEDIUM insufficient provenance | FIXED | Registry, canonical/profile, descriptor and archive hashes; generator version/implementation hashes; Helm/Python/PyYAML/zlib versions and compression/archive identity are recorded. No credential/environment dump is included. |
| MEDIUM implementation testing itself | PARTIALLY FIXED | Added independently authored Helm results, concrete descriptor identities/KDU structure, negative security fixtures, lifecycle mocks and concurrency/recovery tests. Full OSM schema acceptance still needs isolated validation; live verification is prohibited in this pass. |
| MEDIUM grep catalog identity and suppressed lookup errors | FIXED | Structured catalog parsing rejects errors, ambiguity and malformed identities before upload. A lookup failure does not become a creation attempt. |
| MEDIUM incompatible CI triggers/build preparation | DEFERRED | CI redesign is explicitly excluded. Existing legacy-only trigger and no-selection invocation remain documented incompatibilities. |
| MEDIUM machine-specific paths/namespace/VIM/network | DEFERRED | Explicit P0-B work: home-relative state/doc paths, namespace, OSM host, VIM UUID, interface/IP defaults, images and runtime dependencies remain. No reference-machine resources were altered. |
| MEDIUM automatic Git stage/commit/push | FIXED | Removed from deployment. Successful state updates use atomic filesystem writes only; lifecycle tests reject any shell invocation. |
| MEDIUM insecure TLS/credential argv/YAML interpolation | FIXED | Shared verified HTTPS transport, safe YAML serialization, no curl credential arguments or default password. Mock transport verifies certificate validation and password round-trip. |
| MEDIUM scenario definitions/policies outside registry | PARTIALLY FIXED | Generator split-RAN/no-HPA policy is now registry metadata. Historical standalone orchestrator choices/text and direct-Helm scripts remain outside the canonical pipeline; harmonizing those entry points is deferred. |
| LOW malformed request types | FIXED | Non-object JSON, missing/non-string ran, invalid JSON and unknown keys fail with 400 before lifecycle calls. |
| LOW suffix-based upload classification | FIXED | Python onboarding uses registry package fields and explicit package type; filename suffixes no longer select uploads. |

## Adversarial checks and limits

The sensitive-file policy fails closed even if `.helmignore` hides that file.
Helm applies actual ignore semantics; arbitrary chart additions need review.
Dependency charts and embedded Secret resources are not admitted by this RAN
pipeline. This does not regenerate or reconcile Open5GS, IMS, O-RAN/OAI or H-CRAN.

Published snapshots are immutable by generator contract and files are read-only.
Content hashes detect tampering; a privileged or same-user attacker can still
change permissions/code. Memory-owned upload bytes remain unaffected by pathname
replacement. File locks coordinate cooperating processes on one local filesystem.

Exact archive download support, descriptor schema acceptance, TLS trust and
runtime behavior have not been verified against live OSM. Missing download
support or repackaging blocks deployment; there is no unsafe fallback. External
catalog actors can still mutate a descriptor after preflight; local locking does
not create a distributed OSM transaction.

Existing HTTP dashboard authentication/exposure is not redesigned here. Running
services have not loaded these source fixes because no restart was authorized.
Remaining shell-based read-only status helpers use constants/validated registry
metadata, not HTTP component interpolation. Secret detection cannot recognize
every deliberately disguised literal; canonical source still requires review.

A failed replacement after successful old-RAN teardown cannot automatically
restore availability. Instantiation intent/pending IDs remain for explicit
reconciliation; another dashboard deployment is blocked until that reconciliation.
No distributed rollback, automatic cleanup or installer/CI integration is claimed.

## Final local verification

47 regression tests passed (110.172 seconds). All seven ready scenarios passed
local prepare/validate, including canonical and baked Helm lint/template checks.
Python compilation without bytecode writes, shell syntax and git diff --check
passed. The original 18 diagnostics retained hash/size/mtime; all 426 legacy
files retained hashes. Runtime state, installer and CI match HEAD.

Branch: `reproducible-installer`. No files staged or committed.

Tracked diff statistic (new untracked source files are excluded by Git):

```text
 .gitignore                  |   3 +
 ran-selector/backend.py     | 255 ++++++++++++++++++++++----------------------
 ran-selector/osm_client.py  | 172 ++++++++++++++----------------
 scripts/build-cudu-batch.sh |  69 ++----------
 scripts/build-ran-batch.sh  |  80 ++------------
 scripts/build-ran-nsds.sh   |  62 ++---------
 scripts/osm-onboard.sh      | 122 +--------------------
 7 files changed, 237 insertions(+), 526 deletions(-)
```

Git status:

```text
 M .gitignore
 M ran-selector/backend.py
 M ran-selector/osm_client.py
 M scripts/build-cudu-batch.sh
 M scripts/build-ran-batch.sh
 M scripts/build-ran-nsds.sh
 M scripts/osm-onboard.sh
?? config/scenarios.json
?? docs/p0a-review-fixes.md
?? docs/scenario-source-of-truth.md
?? osm-packages/cu_repro_knf.tar.gz
?? osm-packages/cu_repro_ns.tar.gz
?? ran-selector/backend.py.before-client-events
?? ran-selector/backend.py.before-live-fix
?? ran-selector/backend.py.before-sctp-events-fix
?? ran-selector/index.html.before-client-events
?? ran-selector/index.html.before-dashboard-optimized
?? ran-selector/index.html.before-dashboard-redesign
?? ran-selector/index.html.before-dashboard-redesign-2
?? ran-selector/index.html.before-grafana-edit
?? ran-selector/index.html.before-layout-fix
?? ran-selector/index.html.before-live-fix
?? ran-selector/index.html.before-novathinktech-theme
?? ran-selector/index.html.before-novathinktech-v2
?? ran-selector/index.html.before-novathinktech-v3
?? ran-selector/index.html.before-ue-v4
?? ran-selector/index.html.before-v5
?? ran-selector/index.html.before-v7
?? scripts/local_safety.py
?? scripts/osm_catalog.py
?? scripts/osm_onboard.py
?? scripts/osm_packages.py
?? scripts/scenario_registry.py
?? tests/
```
