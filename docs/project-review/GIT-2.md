# GIT-2 consolidation review

Scope: offline source integration in `/tmp/5g-kubernetes-git1`, branch
`integration/layer3-l3-4d`. No push, live collection, service restart, workload,
OSM catalog or monitoring mutation was performed. Original worktree is untouched.

## Inventory and exclusions

The external file-level inventory covers all 25 modified tracked files, 802
untracked files and 2,955 ignored files: 3,782 original paths. It records path,
Git state, A–F classification, reason, hash, size and integration disposition.
It stays outside Git because it inventories private/runtime paths. No deleted
tracked files or staged original changes were present.

Final classes: A=29 validated reviewed variants; B=0 pending source candidates;
C=30 experimental/unvalidated; D=3,199 generated/runtime;
E=504 private/runtime-configuration; F=20 backups/duplicates.
28 C paths are integrated as experimental sources, not runtime acceptance.
The two excluded C paths are the systemd-based fog benchmark and its historical
performance report. Their baseline fixture is also excluded as F.

Excluded: virtualenvs, bytecode/build output, raw local Layer 3 evidence JSON/text,
SQLite journals/locks/state, private configuration/subscriber inputs, backups,
duplicate `cu_repro_*` archives and the unexecuted fog benchmark. All originals
remain present. Previously tracked immutable package archives are retained without
regeneration; no new archive/database/kubeconfig/log is introduced.

## Seven baseline failures and corrections

| Baseline failure | Root cause / correction | Impact |
|---|---|---|
| example_contract_matches_loader_defaults | Shared-readable public example carried password defaults. Secret defaults/example are now empty; private secrets remain owner-only. | Medium, corrected |
| watcher_validation_happens_before_installer_mutations | Default failover made implicit watcher selection valid. Pre-mutation validation now runs first; installer cannot install/start watcher and rejects an existing active watcher. | High, corrected |
| watcher_requires_explicit_ready_target | Implicit target default. Default removed, experimental targets excluded; launcher/unit rendering/failover function independently refuse watcher execution. | High, corrected |
| secrets_must_be_private_and_absent_secrets_fail | Omitted privileged credentials silently fell back to demo passwords. Required privileged fields now reject missing/common demo values; bootstrap must be supplied explicitly. | High, corrected |
| fresh_shell_bootstrap_order_without_real_commands | Fixture lacked the remote source-build script. Fixture now models config, input validation, host bootstrap, source build, image preflight, installer in order. | Medium, corrected |
| missing_image_or_wrong_manifest_mapping_rejected | Old error-text contract predated pinned-source verifier. Assertions follow current rejection while still proving no import. | Low, corrected |
| wrong_existing_tag_is_preserved_and_never_imported_over | Old error text/read-count expectation. Updated for extra read-only attestation checks; additionally require CRI image ID equals verified config digest. | Low, corrected |

Legacy `/api/deploy` failover is removed; the old function raises PermissionError.
Layer 3 LIVE_AUTO is rejected and ProductionExecutor always raises. No mode can
activate production remediation. Experimental recovery requires a matching explicit
instance approval and cannot target either protected service UUID.

The pre-existing push-to-main/self-hosted OSM onboarding workflow is now manual,
read-only `--dry-run`. Helm CI uses approved synthetic test inputs and validates
whole parent charts/packages rather than blindly rendering guarded/private inputs.
CI runs the full offline suite with a 15-minute job timeout and read-only repository
permission. It does not provision infrastructure.

## Component review

| Component | Review and readiness | Offline validation / limits |
|---|---|---|
| Flask RAN Selector | Bounded requests, explicit experimental deployment acknowledgement, existing lifecycle lock/identity checks retained | Dashboard, switch, scenario identity, pending adoption, P0A/P0B tests |
| Dashboard APIs | Existing API routes and Layer 3 integration retained; mocks only | Dashboard guide/workspace/switch, UE discovery and assurance API tests |
| Dashboard performance | 16 request slots; bounded 4-command monitoring concurrency, TTL/cache coalescing, 2 MiB command output and 8 s deadline, process-group cleanup | Monitoring and dashboard tests; production load NOT VERIFIED |
| Fog-RAN deployment | Experimental source only; placement and service discovery; no active release/catalog update | Architecture/profile, readiness and immutable package tests |
| Fog processing | 4 workers; bounded body and numeric inputs; DNS cache freshness and bounded resolver child | 14 localhost fog tests; field latency NOT VERIFIED |
| OAI CU/DU | Integer-safe radio identities, optional placement, experimental multi-DU inputs; baseline defaults preserved | Integer rendering, architecture/profile, OSM package tests |
| OAI RFsim/USRP | Dedicated O-RAN implementation and RF settings retained; no radio/UE execution | O-RAN tests and static profiles; hardware/runtime NOT VERIFIED |
| srsRAN CU/DU | Pinned remote source-build pipeline retained; experimental role inputs | Source manifest/CRI image tests, profile/chart tests; multi-DU runtime NOT VERIFIED |
| C-RAN/O-RAN/vCRAN/H-CRAN/F-RAN | New H/F variants explicitly experimental; O-RAN/srsRAN remains blocked; HPA gates preserved | Architecture, readiness, O-RAN and vCRAN tests |
| Open5GS config/charts | 314-input reviewed vendored source contract retained; no Core chart/config changes | Reference installer reviewed inventory and package tests; live Core NOT VERIFIED |
| OSM KNF/NSD | Canonical helper/schema support, immutable bytes and descriptor references | Package preparation/validation, Helm tests; existing tracked archives unchanged |
| OSM catalog/lifecycle | Experimental replacement requires exact IDs/unused proof; recovery dry-run by default and matching approval; protected IDs rejected | Catalog/recovery/pending adoption/P0A tests; no lifecycle call executed |
| Kubernetes | No cluster configuration changes; offline manifests/reader safeguards retained | Assurance Kubernetes/adapter tests; live API NOT VERIFIED |
| Multus/networking | Existing inputs retained; discovery uses bounded reads; no network edits | Installer/reference and O-RAN/package render tests; actual CNI NOT VERIFIED |
| Rancher | Existing integration retained; missing/demo credentials fail closed | Runtime config and installer tests; live Rancher NOT VERIFIED |
| Prometheus/Grafana | Bounded dashboard reads and provenance; no monitoring config/rule activation; empty privileged password defaults | Monitoring, assurance adapter and installer tests; live endpoints NOT VERIFIED |
| Istio | Existing infrastructure source retained; no new rollout | Installer/static source review; traffic/mesh runtime NOT VERIFIED |
| Installer | Credential/watcher gates precede mutation, source build and private archive protections preserved | Installer/reference, runtime config and image tests |
| Installer cleanup/validation | Ownership and preflight protections retained; no cleanup command run | Reference installer and adversarial safety tests |
| Deployment/benchmarks | Manual experimental acknowledgement, exclude experimental default batches; UE preparation only, zero replicas; new systemd benchmark excluded | Architecture, UE discovery, adversarial tests; benchmark/UE runtime NOT VERIFIED |
| Layer 2 monitoring | Existing source retained; historical log evidence cannot imply current UE health | RAN health and UE discovery tests; runtime NOT VERIFIED |
| Layer 3 assurance | All assurance modules unchanged from integrated L3-4D; production executor disabled, LIVE_AUTO rejected, legacy watcher retired | All assurance suites, including 16 simulations and real inert guardian entry points |
| Documentation/tests/CI | Curated experimental/safety documentation and regressions; CI validates offline, OSM workflow manual/read-only | CI safety tests; hosted workflow itself NOT VERIFIED |

## History and compatibility

Remote baseline `c69fd9bda446fc9328f657178bb67bfb2e663eb1` and integrated Layer 3
`0bdbaeae40e14754656f31ed1ce2404ced5dc77f` remain ancestors. All 30 remote-only
commits are preserved. Their eight changed paths were compared: six remain byte
identical; only `install.sh` (pre-mutation safety gate) and `scripts/srsran_image.py`
(stronger CRI ID validation) were deliberately hardened. Remote source build,
private installer bundling, pinned versions and attestation are retained.
Of 80 Layer 3 paths, 78 are unchanged; dashboard backend/frontend contain reviewed
additive integration changes. No Layer 3 module, policy or journal schema changed.

## Structured source commits and exact files

### `2f1d9ec3a1ef1ac6e1261c8130e46138bbe20904`

fix(security): retire automatic watcher and require private credentials

- `config/global.env.example`
- `docs/SAFE_DEFAULTS.md`
- `install.sh`
- `layer3-autonomous/watcher.py`
- `scripts/installer/install.py`
- `scripts/runtime_config.py`
- `scripts/runtime_render.py`
- `scripts/service_entry.py`
- `tests/test_p0b1_adversarial.py`
- `tests/test_publication_safety.py`

### `8556a4c72376e56d2c2b4b8d2d8930520c2dca9d`

fix(installer): validate inputs before bootstrap and align image verification tests

- `install.sh`
- `scripts/installer/install.py`
- `scripts/srsran_image.py`
- `tests/test_ready_images.py`
- `tests/test_reference_installer.py`

### `392490421de2961ed7f02d1bd5958e3d71dc3b24`

feat(dashboard): bound monitoring load and preserve telemetry provenance

- `ran-selector/backend.py`
- `ran-selector/index.html`
- `ran-selector/monitoring.py`
- `scripts/validate.sh`
- `tests/test_dashboard_guide.py`
- `tests/test_monitoring.py`
- `tests/test_ue_discovery.py`

### `afa3d148f62fc1e68edec2f510741668c55dc3ef`

feat(profiles): add gated experimental H-CRAN and Fog-RAN sources

- `bench-all-ran.sh`
- `config/scenarios.json`
- `deploy.sh`
- `docs/hcran-and-fog-ran.md`
- `docs/scenario-source-of-truth.md`
- `helm/cran-oai/cu/templates/cu.yaml`
- `helm/cran-oai/cu/values-fran.yaml`
- `helm/cran-oai/cu/values-hcran.yaml`
- `helm/cran-oai/cu/values.schema.json`
- `helm/cran-oai/du/templates/du.yaml`
- `helm/cran-oai/du/values-fran.yaml`
- `helm/cran-oai/du/values-hcran-macro.yaml`
- `helm/cran-oai/du/values-hcran-small.yaml`
- `helm/cran-oai/du/values.schema.json`
- `helm/cran-srsran/cu/templates/cu.yaml`
- `helm/cran-srsran/cu/values-fran.yaml`
- `helm/cran-srsran/cu/values-hcran.yaml`
- `helm/cran-srsran/cu/values.schema.json`
- `helm/cran-srsran/du/templates/du.yaml`
- `helm/cran-srsran/du/values-fran.yaml`
- `helm/cran-srsran/du/values-hcran-macro.yaml`
- `helm/cran-srsran/du/values-hcran-small.yaml`
- `helm/cran-srsran/du/values.schema.json`
- `helm/fog-compute/Chart.yaml`
- `helm/fog-compute/templates/_server.tpl`
- `helm/fog-compute/templates/fog.yaml`
- `helm/fog-compute/values-oai.yaml`
- `helm/fog-compute/values-srsran.yaml`
- `helm/fog-compute/values.schema.json`
- `helm/fog-compute/values.yaml`
- `scripts/image_metadata.py`
- `scripts/installer/install.py`
- `scripts/osm_packages.py`
- `scripts/prepare_simulated_ue.py`
- `scripts/runtime_config.py`
- `scripts/scenario_registry.py`
- `status.sh`
- `tests/test_architecture_profiles.py`
- `tests/test_experimental_readiness.py`
- `tests/test_fog_server.py`
- `tests/test_oai_integer_rendering.py`
- `tests/test_osm_packages.py`
- `tests/test_p0b1_adversarial.py`
- `tests/test_ready_images.py`
- `tests/test_reference_installer.py`

### `60a1b83dc454fcf0669a9f3b2c6781c1a7080d1b`

fix(osm): gate experimental catalog replacement and instance recovery

- `scripts/osm_onboard.py`
- `scripts/recover_experimental_ran.py`
- `scripts/state_schema.py`
- `tests/test_experimental_catalog_guard.py`
- `tests/test_experimental_recovery.py`

### `beae2d8f1357b20bdd91373c198e78f2e5f75580`

fix(ci): validate offline and make OSM catalog preflight manual

- `.github/workflows/helm-validate.yml`
- `.github/workflows/osm-onboard.yml`
- `tests/test_ci_safety.py`

## Validation

Full existing suite: **609/609 passed in 337.891 seconds**. The two new CI safety
tests, added while that run was in progress, passed separately: **611/611 current
tests covered, zero failures/errors/skips**. All seven formerly failing cases pass.
The assurance suite includes all 16 simulations with zero production executor calls.

Targeted checks: security 84; installer/config/image 131; image verifier 40;
profile/chart/package/fog 107 (14 fog tests rerun with ephemeral localhost sockets);
recovery/catalog/P0A/pending 67 with the explicit-approval fixture correction
confirmed by all 20 recovery tests; monitoring 16; CI workflow safety 2. Counts
overlap and must not be added. An initial sandbox socket denial was environmental,
not concealed as a passing test. A stale recovery fixture initially failed the
new authorization gate and was corrected to reach its intended identity check.

Python AST syntax: all 92 tracked Python files; shell syntax: 27 files; JavaScript syntax: 2 files.
`git diff --check` passed. Secret scan covers the complete change set versus remote,
including Layer 3, using credential/private-key patterns and private runtime
password/token comparison without printing values. All pattern hits are inspected
rejection sentinels; no unresolved credential finding or private-value match.
Heuristic scanning is not a mathematical guarantee of absence.

Additional recursive chart probe: 39 chart roots; 22 bare lints and 12 bare renders
succeeded. Seventeen vendored Open5GS children lack standalone parent dependencies,
two common charts are libraries, and eight RAN/fog templates deliberately reject
missing approved images. These expected probe failures are recorded separately;
they are not deployment acceptance or regressions. Parent/package/profile tests
supply approved fixture inputs and must pass before readiness is claimed.


| Test module | Passed | Failed |
|---|---:|---:|
| `test_architecture_profiles` | 18 | 0 |
| `test_assurance` | 32 | 0 |
| `test_assurance_diagnostics` | 18 | 0 |
| `test_assurance_guardian_entry` | 8 | 0 |
| `test_assurance_kubernetes` | 16 | 0 |
| `test_assurance_live_adapters` | 19 | 0 |
| `test_assurance_memory` | 17 | 0 |
| `test_assurance_periodic` | 27 | 0 |
| `test_assurance_sqlite` | 18 | 0 |
| `test_ci_safety` | 2 | 0 |
| `test_dashboard_guide` | 4 | 0 |
| `test_dashboard_scenario_identity` | 4 | 0 |
| `test_dashboard_switch` | 2 | 0 |
| `test_dashboard_workspace` | 11 | 0 |
| `test_embed_policy` | 4 | 0 |
| `test_experimental_catalog_guard` | 7 | 0 |
| `test_experimental_readiness` | 10 | 0 |
| `test_experimental_recovery` | 20 | 0 |
| `test_fog_server` | 14 | 0 |
| `test_monitoring` | 16 | 0 |
| `test_oai_integer_rendering` | 6 | 0 |
| `test_oran_oai` | 17 | 0 |
| `test_osm_ingress_tls` | 3 | 0 |
| `test_osm_packages` | 32 | 0 |
| `test_osm_tls_trust` | 11 | 0 |
| `test_p0a_safety` | 28 | 0 |
| `test_p0b1_adversarial` | 33 | 0 |
| `test_pending_adoption` | 12 | 0 |
| `test_publication_safety` | 7 | 0 |
| `test_ran_health` | 11 | 0 |
| `test_ready_images` | 40 | 0 |
| `test_reference_installer` | 86 | 0 |
| `test_runtime_config` | 44 | 0 |
| `test_ue_discovery` | 4 | 0 |
| `test_vcran_autoscaling` | 10 | 0 |

## Preservation, remaining risks and readiness

Original branch `reproducible-installer`, HEAD
`7d38d718044442d831565b42722a83b7b4a56c25`, 25 modified/802 untracked status,
empty index and all 827 dirty-file hashes are unchanged. All 3,782 inventoried
original file hashes were also checked. Only isolated files and Git integration
branch commits were written. Original unfinished work remains uncommitted there.

Ready for offline CI/development review after all tests pass, not automatic
production deployment. Experimental H/F profiles need radio/multi-DU acceptance;
O-RAN/srsRAN remains blocked. RFsim/USRP, SCTP, live OSM/Kubernetes/version
interoperability, fog performance and Core health were NOT VERIFIED in GIT-2.
Existing monitoring gaps and KPM UNAVAILABLE are not repaired by Git integration.
No supported active fog-KDU-only upgrade/rollback is established here. Public
immutable snapshots are not regenerated into experimental deployment artifacts.
Hosted Actions execution, private runner provisioning and dependency supply-chain
pinning beyond the tested Helm version remain future validation/hardening work.

## Guarded publication procedure (not executed)

After separate push authorization, run from the isolated integration worktree.
Require a clean index/worktree and record the reviewed final HEAD. Fetch the target
branch, verify its tip is still the baseline below and that it is an ancestor.
If the remote advanced, stop for another integration review. Push the explicit
integration source ref to `reproducible-installer` without force; do not switch,
merge or reset the original branch. Verify `ls-remote` equals the recorded HEAD.

```bash
cd /tmp/5g-kubernetes-git1
test -z "$(git status --porcelain)" || exit 1
reviewed_head=$(git rev-parse HEAD)
git fetch origin reproducible-installer || exit 1
test "$(git rev-parse origin/reproducible-installer)" = c69fd9bda446fc9328f657178bb67bfb2e663eb1 || exit 1
git merge-base --is-ancestor origin/reproducible-installer "$reviewed_head" || exit 1
git push origin integration/layer3-l3-4d:reproducible-installer || exit 1
test "$(git ls-remote origin refs/heads/reproducible-installer | cut -f1)" = "$reviewed_head" || exit 1
```

Remote tip was read-only checked during GIT-2 and still equals the baseline. A
non-force push rejects a concurrent divergent update. No publication authorized.
