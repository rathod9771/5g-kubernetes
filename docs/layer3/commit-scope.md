# Layer 3 commit scope

Only the completed assurance framework through L3-4D is selected. Dashboard
backend/index contain only assurance registration, asset route and Layer 3 panel
changes in this commit; other working-tree hunks remain uncommitted. The current
working dashboard files were not replaced with the staged versions.

Tests are run from an exported Git index in an isolated temporary directory, using
the repository HEAD versions of excluded files. No runtime service, Kubernetes/OSM
API, protected deployment, production journal or monitoring configuration is
changed by commit preparation. No push is performed.

Runtime databases, private configuration/kubeconfig material, credential files,
logs, backups, virtual environments, bytecode, generated JSON measurements and
local trial evidence are excluded. Policy-validation JSON is a reviewed required
input to the collector and is included. The credential scan found only explicit
synthetic test fixtures; no real credentials or key material were found.

## Included files

- `config/assurance-event.schema.json`
- `config/assurance-policies.json`
- `docs/layer3/README.md`
- `docs/layer3/alarm-catalog.md`
- `docs/layer3/architecture.md`
- `docs/layer3/capabilities.md`
- `docs/layer3/commit-scope.md`
- `docs/layer3/l3-2/README.md`
- `docs/layer3/l3-2/activation-attempt.md`
- `docs/layer3/l3-2/activation-completed.md`
- `docs/layer3/l3-2/activation.md`
- `docs/layer3/l3-2/monitoring-and-kpm.md`
- `docs/layer3/l3-2/policies.md`
- `docs/layer3/l3-2/policy-validation.json`
- `docs/layer3/l3-3/README.md`
- `docs/layer3/l3-3/activation-completed.md`
- `docs/layer3/l3-3/activation.md`
- `docs/layer3/l3-4/README.md`
- `docs/layer3/l3-4/activation.md`
- `docs/layer3/l3-4/trial-completed.md`
- `docs/layer3/l3-4a/README.md`
- `docs/layer3/l3-4a/activation.md`
- `docs/layer3/l3-4a/preflight.py`
- `docs/layer3/l3-4b/preflight-blocked.md`
- `docs/layer3/l3-4b/restart-attempt.md`
- `docs/layer3/l3-4b/trial-completed.md`
- `docs/layer3/l3-4c/README.md`
- `docs/layer3/l3-4c/activation.md`
- `docs/layer3/l3-4c/argument-shadowing-fix.md`
- `docs/layer3/l3-4c/preflight.py`
- `docs/layer3/l3-4c/trial-report.md`
- `docs/layer3/l3-4d/README.md`
- `docs/layer3/l3-4d/activation.md`
- `docs/layer3/l3-4d/preflight.py`
- `docs/layer3/l3-4d/trial-report.md`
- `docs/layer3/operations.md`
- `docs/layer3/security.md`
- `docs/layer3/simulation.md`
- `docs/layer3/standards.md`
- `docs/layer3/validation.md`
- `monitoring/assurance/README.md`
- `monitoring/assurance/alertmanager-fragment.example.yaml`
- `monitoring/assurance/prometheus-rule.example.yaml`
- `monitoring/assurance/rbac.example.yaml`
- `monitoring/assurance/rules.example.yaml`
- `ran-selector/assurance.js`
- `ran-selector/backend.py`
- `ran-selector/index.html`
- `scripts/assurance/__init__.py`
- `scripts/assurance/adapters.py`
- `scripts/assurance/api.py`
- `scripts/assurance/bounded_once.py`
- `scripts/assurance/catalog.py`
- `scripts/assurance/cli.py`
- `scripts/assurance/diagnostics.py`
- `scripts/assurance/engine.py`
- `scripts/assurance/governance.py`
- `scripts/assurance/guard.py`
- `scripts/assurance/guarded_trial.py`
- `scripts/assurance/http.py`
- `scripts/assurance/journal.py`
- `scripts/assurance/kubernetes.py`
- `scripts/assurance/locking.py`
- `scripts/assurance/memory.py`
- `scripts/assurance/model.py`
- `scripts/assurance/observe.py`
- `scripts/assurance/osm.py`
- `scripts/assurance/periodic.py`
- `scripts/assurance/policy.py`
- `scripts/assurance/simulation.py`
- `scripts/assurance/store.py`
- `tests/test_assurance.py`
- `tests/test_assurance_diagnostics.py`
- `tests/test_assurance_guardian_entry.py`
- `tests/test_assurance_kubernetes.py`
- `tests/test_assurance_live_adapters.py`
- `tests/test_assurance_memory.py`
- `tests/test_assurance_periodic.py`
- `tests/test_assurance_sqlite.py`
- `tests/test_dashboard_workspace.py`

## Excluded tracked changes (preserved)

- `bench-all-ran.sh`
- `config/scenarios.json`
- `deploy.sh`
- `docs/scenario-source-of-truth.md`
- `helm/cran-oai/cu/templates/cu.yaml`
- `helm/cran-oai/du/templates/du.yaml`
- `helm/cran-srsran/cu/templates/cu.yaml`
- `helm/cran-srsran/du/templates/du.yaml`
- `ran-selector/backend.py` — non-Layer-3 hunks only
- `ran-selector/index.html` — non-Layer-3 hunks only
- `scripts/installer/install.py`
- `scripts/osm_onboard.py`
- `scripts/osm_packages.py`
- `scripts/prepare_simulated_ue.py`
- `scripts/runtime_config.py`
- `scripts/scenario_registry.py`
- `scripts/state_schema.py`
- `scripts/validate.sh`
- `status.sh`
- `tests/test_dashboard_guide.py`
- `tests/test_osm_packages.py`
- `tests/test_p0b1_adversarial.py`
- `tests/test_ready_images.py`
- `tests/test_reference_installer.py`
- `tests/test_ue_discovery.py`

## Excluded untracked files (preserved)

- `docs/fog-processing-latency.md`
- `docs/hcran-and-fog-ran.md`
- `docs/layer3/alarm-capabilities.json`
- `docs/layer3/files-changed.json`
- `docs/layer3/l3-2/activation-results.json`
- `docs/layer3/l3-2/dashboard-replay.json`
- `docs/layer3/l3-2/files-changed.json`
- `docs/layer3/l3-2/investigation.json`
- `docs/layer3/l3-2/observe-validation.json`
- `docs/layer3/l3-2/test-results.json`
- `docs/layer3/l3-3/activation-results.json`
- `docs/layer3/l3-3/kubectl-startup.json`
- `docs/layer3/l3-3/offline-performance.json`
- `docs/layer3/l3-3/safety-validation.json`
- `docs/layer3/l3-3/test-results.json`
- `docs/layer3/l3-4/correlation-validation.json`
- `docs/layer3/l3-4/interval-model.json`
- `docs/layer3/l3-4/safety-validation.json`
- `docs/layer3/l3-4/supervisor-measurement.json`
- `docs/layer3/l3-4/test-results.json`
- `docs/layer3/l3-4/trial-results.json`
- `docs/layer3/l3-4a/copy-validation.json`
- `docs/layer3/l3-4a/lock-performance.json`
- `docs/layer3/l3-4a/root-cause.json`
- `docs/layer3/l3-4a/safety-validation.json`
- `docs/layer3/l3-4a/test-results.json`
- `docs/layer3/l3-4b/preflight-results.json`
- `docs/layer3/l3-4b/trial-results.json`
- `docs/layer3/l3-4c/activation-blocked.json`
- `docs/layer3/l3-4c/argument-shadowing-tests.json`
- `docs/layer3/l3-4c/launch-reproduction.json`
- `docs/layer3/l3-4c/memory-benchmark.json`
- `docs/layer3/l3-4c/ns-parser-probe.json`
- `docs/layer3/l3-4c/parser-probe.json`
- `docs/layer3/l3-4c/safety-validation.json`
- `docs/layer3/l3-4c/test-results.json`
- `docs/layer3/l3-4c/trial-results.json`
- `docs/layer3/l3-4d/forensic-evidence.json`
- `docs/layer3/l3-4d/journal-integrity.json`
- `docs/layer3/l3-4d/test-results.json`
- `docs/layer3/l3-4d/trial-results.json`
- `docs/layer3/live-inventory.json`
- `docs/layer3/simulation-resource.txt`
- `docs/layer3/simulation-results.json`
- `docs/layer3/test-results.json`
- `docs/layer3/transition-matrix.json`
- `helm/cran-oai/cu/values-fran.yaml`
- `helm/cran-oai/cu/values-hcran.yaml`
- `helm/cran-oai/cu/values.schema.json`
- `helm/cran-oai/du/values-fran.yaml`
- `helm/cran-oai/du/values-hcran-macro.yaml`
- `helm/cran-oai/du/values-hcran-small.yaml`
- `helm/cran-oai/du/values.schema.json`
- `helm/cran-srsran/cu/values-fran.yaml`
- `helm/cran-srsran/cu/values-hcran.yaml`
- `helm/cran-srsran/cu/values.schema.json`
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
- `osm-packages/cu_repro_knf.tar.gz`
- `osm-packages/cu_repro_ns.tar.gz`
- `ran-selector/backend.py.backup-20261012`
- `ran-selector/backend.py.before-client-events`
- `ran-selector/backend.py.before-live-fix`
- `ran-selector/backend.py.before-sctp-events-fix`
- `ran-selector/index.html.before-client-events`
- `ran-selector/index.html.before-dashboard-optimized`
- `ran-selector/index.html.before-dashboard-redesign`
- `ran-selector/index.html.before-dashboard-redesign-2`
- `ran-selector/index.html.before-grafana-edit`
- `ran-selector/index.html.before-layout-fix`
- `ran-selector/index.html.before-live-fix`
- `ran-selector/index.html.before-novathinktech-theme`
- `ran-selector/index.html.before-novathinktech-v2`
- `ran-selector/index.html.before-novathinktech-v3`
- `ran-selector/index.html.before-ue-v4`
- `ran-selector/index.html.before-v5`
- `ran-selector/index.html.before-v7`
- `ran-selector/monitoring.py`
- `scripts/benchmark_fog_latency.py`
- `scripts/image_metadata.py`
- `scripts/recover_experimental_ran.py`
- `tests/fixtures/fog_server_before.py`
- `tests/test_architecture_profiles.py`
- `tests/test_experimental_catalog_guard.py`
- `tests/test_experimental_readiness.py`
- `tests/test_experimental_recovery.py`
- `tests/test_fog_server.py`
- `tests/test_monitoring.py`
- `tests/test_oai_integer_rendering.py`
- `ran-selector/venv/` — 704 local virtual-environment files

Ignored `.runtime/`, private config, bytecode and other ignored artifacts are
not enumerated by Git status and remain excluded. No deleted files were present.

## Validation

Exact staged-snapshot offline regressions: **215 tests passed in 196.094 seconds**
(assurance, dashboard, lifecycle safety and RAN health), including all 16 sandbox
simulations. Excluded unfinished work was absent from that snapshot. The only
subsequent source cleanup removed two trailing-whitespace lines; AST equality
against the tested source was verified. JSON/YAML, 44-policy catalog, renderer
syntax and staged whitespace checks passed. No real credentials or private key
material found; two credential-like literals are explicit synthetic test fixtures.
