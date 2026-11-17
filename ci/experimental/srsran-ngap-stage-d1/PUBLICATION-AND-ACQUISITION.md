# D2A-P1 publication and acquisition policy

Publication candidate: feature/layer3-srsran-ngap-native-build-d2b, based on
c99f7c53d557a475f2dd563a810fe9385b2a5ced. No branch is created by preparation.
The only native workflow event is workflow_dispatch; job guard requires that event,
the exact branch and the exact review confirmation. Publishing cannot start it.

## Manual dispatch — BLOCKED until registration is established

GitHub documents default-branch workflow registration as a prerequisite:
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch

No remote/default-branch registration was inspected or changed here. A new workflow
on only the experimental branch is not established as dispatchable. Do not use a
push trigger, repository_dispatch, default-branch switch or production workflow to
work around this. Execution remains BLOCKED. Separately approve a read-only check
of default-branch registration. If absent, a separately reviewed/approved minimal
registration change outside this experimental branch is needed; it is NOT part of
this package, and main remains unchanged.

After registration, publication and execution are separately approved, the supported
manual command is (NOT executed here):

    gh workflow run experimental-srsran-ngap-runtime-build.yml \
      --repo rathod9771/5g-kubernetes \
      --ref feature/layer3-srsran-ngap-native-build-d2b \
      -f confirm_review=APPROVED_SOURCE_AND_DEPENDENCY_PINS

A registration change must preserve dispatch-only and the experimental branch guard;
never authorize a build on main. Remote registration and execution approval are not
inferred from this example.

## Pip installer — BLOCKED: approved pin is missing

The seven approved dependency wheel hashes are unchanged. Neither those wheel
records nor requirements pin pip itself. Ubuntu's venv/ensurepip installer version
is not an approved exact pin. POLICY.approved_pip_version remains None deliberately.
No pip upgrade, wheel acquisition or new dependency is authorized/prepared here.

pip_acquisition.py records BLOCKED before installation unless a separately reviewed
exact installer version is pinned. It then requires that version and verifies local
help supports every required option, including --resume-retries. Unsupported older
pip versions block before package acquisition; no fallback omits retry controls.
A future approved installer needs a reviewed wheel hash/source/bootstrap procedure
and compatibility evidence. Merely installing a newer unpinned pip is prohibited.

Future install policy (not executed): zero connection retries, zero resume retries,
15-second socket timeout, 240-second command deadline, no prompts, no version checks,
no cache, binary wheels only, --require-hashes, --no-deps, fixed HTTPS PyPI index.
The Python process uses -I; its entire environment is constructed anew, containing
only PATH, HOME, TMPDIR, LANG/LC_ALL and PIP_CONFIG_FILE=/dev/null. It inherits no
PIP_* options, proxy variables, PYTHONPATH or user home. devnull disables all pip
configuration files, including site/global configuration. CLI options bind policy.
All pip invocations: local --version, local --help, the single guarded install,
and workflow freeze --all (metadata only, sanitized environment, no version checks).
No pip is called by the offline native worker.

Sources:
https://pip.pypa.io/en/stable/cli/pip/
https://pip.pypa.io/en/stable/topics/configuration/
https://pip.pypa.io/en/stable/news/

No installer compatibility is claimed from mocked help/version fixtures. Installed
pip is not probed on HP; those checks are future acquisition admission only.

## Receipt timing and success contract

Every phase/command requires UTC ISO-8601 start/end (Z or +00:00), a finite,
nonnegative monotonic elapsed_seconds and wall-end >= wall-start. Wall interval
must agree with monotonic elapsed within 1.0 second. This is an admission tolerance,
not a clock-accuracy claim. Backward/large clock steps fail; missing times are never
synthesized. Command wall intervals must be ordered and contained in the phase;
command elapsed cannot exceed phase elapsed plus tolerance.

PASS requires integer exit 0 (not boolean), explicit signal=null, timeout=false,
validation_completed=true, resource evidence, valid SHA-256 identities, nonempty
output evidence and all nested commands PASS, unforced and untruncated. Alternative
contradictory timed_out/termination_signal fields also reject PASS. Supervisor must
explicitly complete, record normal successful systemd terminal outcome and valid
timing. Finalizer reuses the strict admission function, verifies resource admission,
full manifested artifacts and referenced command/phase log hashes. Native output
hashes are checked by the supervisor before scratch unmount; finalizer checks the
retained manifest, not nonexistent post-unmount binaries.

No change to resource limits, namespace confinement, NGAP semantics or native inputs.
Actual runner capabilities remain UNVERIFIED and must BLOCK if unavailable.
