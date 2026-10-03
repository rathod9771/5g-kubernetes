# Scenario sources and deterministic OSM packages (P0-A)

`config/scenarios.json` is the shared scenario specification. It preserves the
dashboard keys and aliases, describes implementation/profile inputs, and names
KNF/NSD packages. The dashboard reads this registry; it does not install the
chart paths directly. Deployment continues through OSM.

## Source authority

```text
config/scenarios.json + canonical Helm chart + ordered profile values
    -> generated KNF staging with baked default values
    -> deterministic KNF and NSD archives + provenance
    -> explicit, validated OSM onboarding
    -> dashboard NSD lookup using the same registry
```

Generated files live in ignored `build/osm-packages/`. Every scenario produces
two staging directories, two `.tar.gz` archives and a provenance JSON file,
inside a content-addressed `releases/<digest>/` snapshot. `CURRENT` is an
atomically replaced text pointer to the complete published snapshot.
Do not hand-edit them. The profile is baked into `values.yaml`, because the OSM
KDU installs chart defaults; selecting a profile only in dashboard metadata
would not apply it to an OSM deployment.

Existing tracked `osm-packages/` directories and archives are **legacy audit
references**, not independent source implementations. They remain untouched.
The new builder and onboarding script do not use them as packaging inputs.
Their reviewed content hashes protect against silently discarding a newly
discovered package-only change. A changed legacy input blocks that component
until the change is reviewed and reconciled. Do not simply refresh review
hashes to silence that error.

`scripts/rename-ran-packages.sh` is a historical migration script, not part of
this pipeline. It remains preserved and must not be used to maintain the legacy
trees. Experimental direct-Helm deployment scripts also remain outside generation.

## Eligible and blocked components

| Scenario | Canonical input | Generation |
|---|---|---|
| `cran-srsran`, `cran-oai` | `helm/cran-{srsran,oai}/{cu,du}`, defaults | Ready |
| `cloudran-srsran`, `cloudran-oai` | Same CU/DU charts, `values-cloudran.yaml` per KDU | Ready |
| `vcran-srsran`, `vcran-oai` | Same CU/DU charts, `values-vcran.yaml` per KDU | Ready |
| `fran` | `helm/fran-edge` | Ready, additive and vendor-independent |
| `oran-srsran` | `helm/srsran-oran/{cu,du}` | Blocked: package-only stable-AMF discovery fix |
| `oran-oai` | No authority chosen between `helm/oai/` and `helm/oran-oai/` | Blocked: source decision and package-only AMF fix |
| `hcran-srsran`, `hcran-oai` | H-CRAN chart plus macro/small profile | Blocked: AMF fix and one-KDU versus two-cell discrepancy |
| `open5gs` | Canonical/staging reconciliation outstanding | Blocked: verified NRF templating, metrics and stable-AMF fixes |
| `ims` | Helm/staging configuration reconciliation outstanding | Blocked: P-CSCF `.99` versus `.100`, external inputs |
| `full-stack` | Composite descriptor | Blocked: Open5GS and IMS dependencies; stale archive |
| `none` | Control operation, no NSD | Blocked: terminate-only implementation is separate work |

The ready CU/DU source decision follows commit `6dbf6b2`: shared implementation
with C-RAN/Cloud-RAN/vCRAN profiles. This is an intentional migration from older
monolithic/forked packages, not a claim that those implementations are identical.
The older copies are preserved. Latest vCRAN profiles explicitly exclude HPA;
older packaged HPA templates are not carried into generated artifacts.

Known package fixes already present in these canonical CU sources are retained:
stable `amf-ngap-stable` discovery, Pod-IP substitution, explicit slice SD and
RAN Istio opt-out. Local Helm checks enforce these properties. Cloud-RAN/vCRAN
deployment names, DU-to-CU discovery and resource constraints come from their
profiles. F-RAN's canonical and legacy chart trees were identical at review.
Generation-ready does **not** mean runtime-proven, image-pinned or offline-ready.

## Local commands

Requires Python 3.10+, PyYAML and Helm 3. No downloads, Helm repository updates,
Kubernetes API calls or OSM requests occur in these commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/osm_packages.py list
PYTHONDONTWRITEBYTECODE=1 python3 scripts/osm_packages.py prepare --ready
PYTHONDONTWRITEBYTECODE=1 python3 scripts/osm_packages.py validate --ready
PYTHONDONTWRITEBYTECODE=1 python3 scripts/osm_packages.py prepare cran-oai
PYTHONDONTWRITEBYTECODE=1 python3 scripts/osm_packages.py prepare --ready --output /tmp/ran-packages-review
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

`HELM_BIN` selects a Helm executable if the host has a wrapper. Rendering uses
Kubernetes capability version `1.29.15`, the audited reference baseline, with
`KUBECONFIG=/dev/null`. No live cluster is needed. The supported runtime/version
matrix is P0-B/P1 work.

Both KNF and NSD are generated together. The three old `build-*.sh` entry points
delegate to this generator; with no arguments they explicitly select only ready
RAN scenarios. Direct requests for blocked components fail before output writes.

`prepare` is the explicit local-only interface: capture canonical sources,
resolve profiles using Helm, generate, lint/render/compare, publish, validate.
The compatibility `build` command delegates to the same interface. A process
lock serializes generation, publication and validated readers. Inputs are
captured into a private snapshot and checked for changes during capture. All
subsequent generation and rendering use that snapshot.

A changed source creates a new complete release; old releases and older flat
build outputs are preserved. Publication merges previously prepared scenarios,
so preparing one scenario does not remove another. Identical preparation leaves
the pointer unchanged. Modified published snapshots fail closed. Incomplete
`.pending-*` directories are never reader inputs; interruption before pointer
publication retains the previous release, and preparation can be retried.
No legacy package tree is overwritten. `install.sh` integration is deferred.

## Validation and reproducibility

The builder preflights every selected scenario before writing artifacts:

- Registry identities, aliases, required charts, profile files and validation policy.
- Reviewed legacy hashes, blocking unreviewed package-only changes.
- KNF KDU chart paths, NSD package identity and KNF/member references.
- Helm lint and rendering of canonical sources with profiles and generated
  charts with baked defaults; rendered objects must match.
- Expected deployment identities, cross-KDU resource collisions, retained fixes
  and the no-HPA vCRAN decision.

Validation recomputes all expected files from canonical sources and compares
every staging file, provenance record and archive byte. Missing files, extra
embedded charts, edited defaults, source changes and stale archives fail.
Archives use sorted entries, fixed ownership/modes/timestamps, USTAR format,
and a gzip header with no filename and zero timestamp. Descriptor/default-value
serialization is sorted JSON, valid YAML. OSM acceptance remains unverified. Provenance records raw canonical/profile hashes, descriptor hashes, full registry
hash, generator version and implementation hashes, Helm/Python/PyYAML/zlib
versions and compression format. It excludes credentials and host environment
values. Compression reproducibility is tested within the Python/zlib toolchain; cross-toolchain and
OSM schema acceptance still require isolated follow-up verification.

## Onboarding and dashboard boundaries

**Do not run onboarding during local verification.** It changes OSM.
When separately authorized, `scripts/osm-onboard.sh` delegates to the Python
onboarding entry point and requires explicit scenario keys or `--ready` (and
optionally `--output`). It automatically invokes local preparation, then obtains
validated bytes in memory before authentication. Upload never reopens archive
paths, so later replacement cannot change the validated upload. Package type
and identity come from registry fields, not filename suffix classification.
KNFs upload before NSDs; exceptions stop the sequence. Catalog lookup parses
structured responses and fails on errors/ambiguity. HTTPS verification is
required; no insecure TLS switch or default password is supplied. Private CAs
must be trusted explicitly in the host trust configuration (P0-B).

The existing CI workflow is intentionally unchanged: its no-selection invocation
and legacy-only triggers remain incompatible. CI redesign and unattended
onboarding are outside this pass.

The dashboard rejects unknown components and malformed deployment bodies.
Component lookup uses Python filtering and argument-list subprocesses. A local
`flock` serializes lifecycle requests across threads/processes using the same
state directory. Registry and runtime state are reread after locking.

Before any termination, the dashboard validates a published local snapshot and
requires unambiguous onboarded KNF/NSD identities plus exact SHA-256 equality of
the downloaded KNF/NSD package bytes. The catalog UUID is pinned for subsequent
instantiation. Missing/ambiguous/stale catalog content, unsupported download
endpoints, repackaged content or TLS failures block deployment before teardown.
No name-only or descriptor-only provenance fallback exists. GET endpoint support
and response behavior have only been tested with mocks, not against live OSM.

Termination must reach COMPLETED, owned resource names must disappear (including
non-Running pods and remaining workloads/services/configmaps), deletion must
succeed and NS absence must be confirmed. Query failures block progression.
Runtime state uses fsync plus atomic replacement, with no Git operations.
Instantiation intent and returned instance/operation identities are persisted;
a failed or interrupted operation leaves pending state that blocks another
request until explicitly reconciled. Runtime failure is not rolled back by
silently recreating the old RAN.

No live catalog, cluster or running service was accessed/restarted. HTTP dashboard
authentication/network exposure, machine-specific configuration, catalog changes
by external actors, and isolated runtime/schema compatibility verification
remain outstanding. File locks coordinate cooperating processes on this local
filesystem; they are not distributed locks across machines. Other clients must
not independently edit runtime state or catalog contents during a switch.

### Content identity and audit metadata

Format-2 provenance has an exact required schema; missing/extra fields, malformed
hashes, unsupported generator/schema versions and unknown format versions fail.
Validation still checks the immutable publication digest and every raw chart,
descriptor and archive byte against current canonical sources before provenance
comparison. Scenario identity/hash, profile/source inputs, descriptor/staging/
archive hashes and Kubernetes render version remain strict content identities.

Toolchain fields and generator implementation hashes record original build
metadata. Differences produce a stderr notice listing JSON-pointer paths, but
cannot invalidate byte-identical payloads. A whole-registry hash difference is
also reported only after the resolved scenario hash and every payload identity
match: registry changes that alter this scenario remain fatal through its hash.
Generator version remains schema identity, not an ignored implementation hash.

`validated_snapshot()` returns the original validated stored bytes, including
the original provenance. Metadata-only validation never rewrites provenance,
CURRENT or archives, and never invokes preparation. In particular, original
PyYAML 6.0.1 build metadata validates with PyYAML 6.0.3 when all payloads match.
Explicit prepare also reuses an unchanged scenario's original provenance, so
audit-only changes do not republish identical payloads. Validation and replacement
preflight do not silently prepare packages or fabricate refreshed build history.
