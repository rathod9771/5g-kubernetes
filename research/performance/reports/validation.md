# Validation and safety record

- 43 new offline tests passed (0.034s): OAI UL/DL fields and units, no composite
  SNR truncation, native srsRAN JSON separation, split/monolithic discovery,
  discovery/log failures, replacement UID cleanup, duplicates, saturation,
  stale/disconnected UE expiry including API failure, clock rounding/skew,
  budgets/shutdown, strict schema/null/nonfinite rules, CSV/JSON/hash integrity,
  independent-trial grouping, target dimensions, event correlation/clocks,
  receiver traffic summaries, confusion/success/unknown outcomes and guarded
  radio collection. Inputs are synthetic fixtures, never mentor benchmark data.
- 32 existing tests passed (10.195s): test_assurance_kubernetes and test_monitoring.
  Total75 distinct focused tests. No complete-repository pass is claimed.
- Python AST syntax and both exporter YAML manifests parsed successfully.
- git diff --check clean; see publication-review.md for final publication checks.
- All5 genuine result bundles validate, including CSV/JSON SHA256 manifests:
  two251-record snapshots + one CI record + two TCP-probe records +16 final Pod Ready records =521 records.
  Both snapshots share one trial ID. No SYNTHETIC_TEST records occur in live data.
- The plot path refused current data (<5 independent trials); no fake comparison
  graph exists. Matplotlib is absent and was not installed. Plot implementation
  requires a separately approved analysis environment for rendering.
- Existing result hashes are accidental-corruption checks, not cryptographic
  source attestation. Metadata/source references still require review.
- Initial dimension-pooling summaries and the invalidated zero-series engineering
  run remain local under reports/diagnostics (excluded from publication); use final-statistics
  and offline-exporter-overhead-corrected only. No invalidated values support claims.

## Production/P4 preservation

Final read-only comparison:48 Deployment/StatefulSet/ConfigMap UID/spec/data
fingerprints unchanged.16 protected workload pods (CU/DU/fog +13 Open5GS) Ready.
Live exporter script hash remained6280f5cd0f21f7293ba98cd4d08c1ba4568952e2269a9175a171b33d7a15063a;
old selector and missing UL setter still present: candidate was not deployed.
This is a scoped comparison, not a claim of auditing every cluster object.
No Kubernetes apply/patch/delete/scale, Helm lifecycle, OSM lifecycle/catalog write,
UE activation, traffic generator or fault injection was invoked.

Original checkout remains at7d38d718 with its25 tracked modifications retained.
Claude's separate integration checkout remains clean on p4/kdu-upgrade-experiment
at97b170cf364d296b7056642e78940fa44c66e1db. No P4 files or Git state were edited.
The requested worktree creation/fetch changed shared Git metadata only; performance
branch was reviewed against baseline e0602ec; publication changes only the feature branch.
Dashboard remains active; legacy layer3-watcher inactive. No service was restarted.
OSM NBI state is explicitly UNAVAILABLE due to default-credential rejection; Ready
pods do not replace a READY/running/IDLE NBI verification.

Secret/artifact scan of isolated candidates found no private-key/token patterns,
runtime DBs, kubeconfig files, generated logs or archives. No credentials or raw
CU/DU/UE logs are included. Private runtime paths are references, not copied files.
The scan is a bounded pattern check, not a guarantee against every possible secret.

## Limits and corrections

Live radio exporter behavior, exact OAI image stats format and pinned srsRAN JSON
transport remain unverified. The future radio reader is offline-tested only.
Normal/malformed synthetic formats do not prove those binaries emit statistics.
Application SBI latency/success, UE traffic, deployment/recovery timing and
cross-architecture comparison remain unavailable/not yet tested.

The final collector's canonical-app binding was added after the passive pilot;
unit tests cover it and the observed pilot app labels match the registry. No new
live collection was performed solely to rerun that selection check. Metadata
normalization of the first snapshot corrected source cadence using actual target
paths (cAdvisor10s/exporter15s); numeric observations were not changed. The final
snapshot records actual cadence directly. Monitoring-target software_version uses
an API-contract tag; Prometheus build release was not independently collected.
CPU is Kubernetes logical-CPU equivalents. Hardware readback is Intel i7-4790,
4 physical cores /8 logical CPUs. Do not normalize by4 or claim whole-host percent.
