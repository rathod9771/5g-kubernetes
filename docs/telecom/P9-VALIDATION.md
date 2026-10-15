# P9 validation record

Date: 2026-10-15. Baseline c63aa1c002e0ca0bb1b4100ba3b0457e531a734c,
branch feature/telecom-cicd. No commit, push, service restart, infrastructure
installation, real registry push, catalog write or live NS creation performed.
Prior P1–P8 uncommitted work retained unmodified. All journals/artifacts/logs
created for validation were isolated under /tmp.

## Results

- Complete telecom suite discovery (`test_telecom_*.py`, 11 files including
  the two new P9 files): **139 tests passed**, 427.482s.
  `tests/test_telecom_upgrade.py` (new, 15 tests) and the updated
  `tests/test_telecom_browser.py` (11-view dashboard assertion) are both
  included and passing.
- `install.sh`-dependent suites, run separately because the new
  `--platform-only-plan` short-circuit touches the file's first lines:
  `test_reference_installer`, `test_ready_images`, `test_p0b1_adversarial`,
  `test_publication_safety` — **166 tests passed**, 17.713s. This includes
  `test_fresh_shell_bootstrap_order_without_real_commands`, which actually
  executes `install.sh` under `bash` with faked `sudo`/`python3`/`ip` and
  asserts the exact mutating bootstrap order; it is unaffected because the
  new flag only activates on `--platform-only-plan` as `$1`.
- `./install.sh --platform-only-plan` executed directly (read-only): prints
  the research-only plan JSON and exits before any root check, `sudo
  apt-get`, `common.sh` load or installer invocation — confirmed by reading
  its own output during this validation.
- Offline end-to-end demonstration (`python3 -m telecom.demo_upgrade`): one
  full run from onboarding through update through rollback completed with
  `audit_integrity: true` and `production_calls: 0` (see below).

Commands:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:scripts HELM_BIN=/usr/local/bin/helm.real python3 -m unittest discover -s tests -p 'test_telecom_*.py' -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:scripts HELM_BIN=/usr/local/bin/helm.real python3 -m unittest test_reference_installer test_ready_images test_p0b1_adversarial test_publication_safety -v
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m telecom.demo_upgrade --output /tmp/p9-demo-unique --helm /usr/local/bin/helm.real
./install.sh --platform-only-plan
```

## Offline demonstration summary

```json
{
  "status": "OFFLINE_TESTED", "remote_evidence": "SIMULATED", "source": "PREBUILT_OCI",
  "buildkit_required": false, "helm": "REAL_LINT_RENDER",
  "initial_catalog": "PUBLISHED_CONTENT_VERIFIED", "initial_submission": "SUBMITTED", "initial_deployment": "WORKLOAD_READY",
  "update_catalog": "PUBLISHED_CONTENT_VERIFIED", "update_submission": "SUBMITTED", "update_observation": "WORKLOAD_READY",
  "rollback_submission": "SUBMITTED", "rollback_observation": "WORKLOAD_READY",
  "release_history_length": 2, "certification": "FIXTURE_ONLY; NO_SHIPPED_CONFIGURATION_SETS_THIS",
  "simulated_nbi_calls": 10, "production_calls": 0, "production_eligible": false, "audit_integrity": true
}
```

The deployment's recorded release correctly moved release_v1 → release_v2
(update) → release_v1 (rollback), with exactly two durable `release_history`
entries. All registry/OSM/Kubernetes transport in this run is an in-process
fixture: `telecom.initial.scoped_catalog` and `telecom.upgrade.scoped_catalog`
are both patched, as is `InitialDeployment.snapshot`/`InstanceUpdate.initial.snapshot`.
`upgrade_rollback_certified` is set only inside this throwaway demo policy
object and is never read from any file, environment variable or shipped
configuration — no real deployment target anywhere sets it.

## Source changes attributable to P9

New source: `scripts/telecom/upgrade.py` (`InstanceUpdate` adapter),
`scripts/telecom/demo_upgrade.py` (end-to-end offline demonstration).
Modified existing telecom source: `scripts/telecom/api.py` (five new
`/instance-update-*` routes plus one GET-listing mapping entry),
`scripts/telecom/research_setup.py` (platform-only profile pointer, updated
lifecycle line — no behavior change to existing assertions).
Dashboard: `ran-selector/telecom.html` and `telecom.js` (new "Instance
Updates" view, five new operation options, updated guidance text; existing
six P1–P8 views, API paths and auth model are unchanged).
Installer: `install.sh` (`--platform-only-plan` non-mutating short-circuit,
placed before the root check and before any `sudo`/installer invocation).
Tests: new `tests/test_telecom_upgrade.py` (15 tests); `tests/test_telecom_browser.py`
updated for the 11th dashboard view.
Docs: `P9.md`, `P9-LIVE-TEST.md`, this validation record;
`docs/telecom/README.md` and `P8.md`'s own cross-references were left as-is
except for one added top pointer to P9.
No Helm reference deployments, installer execution, OSM catalog packages,
protected service configuration or any P1–P8 adapter's own logic
(`InitialDeployment`, `Lifecycle`, `Pipeline.request`'s existing
`action='update'/'rollback'` stub) was changed by P9.

## Readiness

IMPLEMENTED / OFFLINE_TESTED: guarded instance-update/rollback request →
independent approval → operator submit → bounded one-shot observation, with
real pre/post Kubernetes image-digest verification, pending-operation
conflict detection, rollback limited to the immediately prior deployed
release, stale-source-revision rejection, durable bounded history and safe
pre-approval cancellation. Dashboard view, operation menu and research-only
installer profile pointer are wired.

UNVERIFIED: the exact OSM v19 NS-update NBI operation shape (path, field
names, resulting status strings) — flagged explicitly in the module
docstring and in P9-LIVE-TEST.md. Live execution is blocked everywhere by
the `upgrade_rollback_certified` gate, which no shipped configuration sets.
Application health and telecom-protocol verification remain
UNVERIFIED/NOT_RUN exactly as in P8; `production_eligible` is always false.

No live activation authorized or executed here. Follow P9-LIVE-TEST.md only
after separate approval, and only after the P8-LIVE-TEST.md disposable
create procedure has already succeeded on the same instance.
