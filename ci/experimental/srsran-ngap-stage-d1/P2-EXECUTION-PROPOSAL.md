# D2A-P2: proposed resolution, not authorization

The P1 evidence/package remains frozen. This proposal supersedes its unresolved
installer choice and supplies a separate inert main registration file. Actual
publication, acquisition, offline installation, CI and native execution remain
unapproved. Main remains the default branch; no branches or refs were changed.

## Installer assessment

BLOCKED_SYSTEM_PACKAGE_PINS_NOT_ESTABLISHED: the reviewed workflow uses unversioned
apt install names. No approved exact Ubuntu .deb versions/archives for Python,
PyYAML/jsonschema or native build tools exist in this package. Installed toolchains
are frozen for the future run, not preselected immutable system-package pins.
Do not claim apt/ensurepip supplies the seven already hash-pinned wheel versions or
an approved compatible installer. Replacing them with arbitrary system versions
would change the validated test inputs. Native build scripts themselves use stdlib;
YAML/jsonschema dependencies are needed for the preserved preparation suite.

Proposed installer: pip 26.2, pip-26.2-py3-none-any.whl, SHA-256
931c303696af6fa3417112103b1cad26890e5a07eccb5b99783700e33f2b8aad.
Exact URL, size and metadata/source references: installer-pin.PROPOSED.json.
PyPI requires Python >=3.10; the approved platform remains CPython 3.10/x86_64.
Tagged __main__.py explicitly supports python <wheel>/pip; tagged cmdoptions.py
contains required --retries, --resume-retries, --timeout and prompt/check controls.
25.2 was considered but rejected after its PyPI metadata listed published
vulnerabilities; 26.2 metadata lists none at this review. This is not a guarantee
against unknown or subsequently disclosed defects.

No installer bootstrap/upgrade or pip installation is required. Proposed sequence:
1. Future authorized acquisition obtains exactly eight wheel files: seven unchanged
   approved dependency wheels plus the new proposed pip wheel. wheelhouse_acquire.py
   uses public HTTPS release metadata, selects exact filename/version/hash (and
   exact installer URL), and then verifies downloaded bytes against fixed hashes.
   No retries, redirects or fallback versions; 15s socket, 1MiB metadata/32MiB wheel
   limits, 5-minute workflow step timeout. Acquisition precedes offline installation.
2. offline_python.py rechecks the exact directory inventory, regular files/no links,
   hashes, CPython 3.10/x86_64 and unused venv path. Create a venv --without-pip.
3. Run the verified pip wheel directly using that venv Python with -I. Verify actual
   version and required help options before installing. No installed system pip is
   invoked, upgraded or trusted as a bootstrap. Install only reviewed local wheels
   using --no-index --find-links <exact wheelhouse> --require-hashes --no-deps
   --only-binary=:all: --no-compile. All retry counts zero, timeout15, no prompts,
   no version checks/cache, clean environment/PIP_CONFIG_FILE=/dev/null. Fresh
   wheelhouse is rehashed before every subprocess. Commands have 240s deadlines
   under the enclosing 5-minute step; logs and atomic receipts retained.
4. Verify the installed distribution/version set equals exactly the seven pins;
   pip itself is not installed. Neither native configuration nor compilation has
   a package acquisition path. The existing worker network/cgroup admission is
   unchanged and must fail closed.

All package bytes remain NOT_DOWNLOADED and installer/venv mechanism NOT_EXECUTED.
Tests use synthetic file bytes and mocked metadata/HTTP only. Native execution is
not implied by the source-level compatibility review. The new wheel and proposed
acquisition/setup implementation require explicit review and execution approval.

P1 pip_acquisition.py is preserved as reference/negative regression input, not called
by this revised workflow. Its unresolved POLICY is not the execution policy here.

## Two independent publications using the SAME workflow path

Target path on BOTH refs:
.github/workflows/experimental-srsran-ngap-runtime-build.yml

Main proposal is in the separate registration patch. Dispatch-only, permissions {},
no checkout/actions/dependency installation, hosted runner declaration, and its only
job has `if: ${{ false }}`. Even manual dispatch on main cannot execute its step.
Publishing this registration file has no push trigger and cannot build anything.

The D2B version is the build-only implementation in native-build.WORKFLOW.PROPOSED.yml.
Its job requires exact refs/heads/feature/layer3-srsran-ngap-native-build-d2b,
workflow_dispatch event and exact confirmation. Publishing it cannot trigger a build.
No production environment, secrets, self-hosted fallback, tests of built binaries,
AMF/NRF/driver startup, protocol operation or deployment job is introduced.

GitHub documents default-branch workflow registration and selecting a different ref:
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch

Supported future sequence, ALL subject to separate explicit approvals:
A. Review current main workflow path/collision and existing workflow triggers. Approve
   publishing ONLY the inert registration file on main, preserving main as default.
   This proposal does not authorize creating a main PR, commit or merge.
B. Approve publishing the full reviewed experimental package on D2B from c99 baseline.
   No branch was created here. Both publications are inert on push.
C. Independently verify actual registered workflow/ref/commit hashes and unchanged
   protections. Actual GitHub registration is UNVERIFIED until this check succeeds.
D. Approve one workflow_dispatch with acquisition and build-only scope, including
   the new installer wheel and existing privileged disposable-runner confinement:

   gh workflow run experimental-srsran-ngap-runtime-build.yml \
     --repo rathod9771/5g-kubernetes \
     --ref feature/layer3-srsran-ngap-native-build-d2b \
     -f confirm_review=APPROVED_SOURCE_AND_DEPENDENCY_PINS

Do not replace dispatch with push/repository_dispatch, change the default branch,
or run a second dispatch automatically. Dispatch on main or another ref cannot
execute native code. Resource/network capability failures remain BLOCKED before
configuration. Remote registration and current main collision state were not read.

## Unchanged controls and limits

Native 6GiB memory/swap0, 2 CPU quota, 256 tasks, 12GiB hard scratch capacity,
4500-second unit deadline, two compile jobs, external supervisor,
atomic receipts, strict P1 timing/command/resource/artifact admission and default-off
NGAP instrumentation remain unchanged. No clock, driver or protocol source changes.
Acquisition/Python setup are separate hosted preparation phases, not claimed to be
inside the native worker cgroup. No actual enforcement or installer was executed.
