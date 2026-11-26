# P4 KDU upgrade experiment — successful run, 24 October 2026

## Result and evidence authority

The operator confirmed **79 passed checks, zero failed checks and three observations**
for `run-20261024T045657Z`. This report records that confirmation; the evidence and
archive directories were not available on the documentation-development host, so
this change does not independently reverify their hashes or contents.

- Source revision: `3f8cd84039e93ca10af1ca94ab0193665e920cf1`.
- Evidence on the experiment host: `~/p4-run/evidence/run-20261024T045657Z`.
- Operator-verified archive: `~/p4-archive/successful-p4-20261024T063811Z`.
- Experiment NS: `57fd4942-6c44-4da4-8297-1a29d101286e`.
- Existing methodology and safety specification: [P4 experiment](p4-kdu-upgrade-experiment.md).

No raw credentials, kubeconfig, subscriber data or unsanitized RO logs are included.
The additional VIM diagnostics described below were developed offline after this
run and must not be attributed to the original 79 checks.

## Objective and architecture

Validate revision-pinned, in-place OSM v19 Helm KDU upgrades, explicit and automatic
rollback, partial-failure resolution and transport-uncertainty reconciliation.
The disposable application models CU/DU lifecycle behavior, not a real 5G radio
protocol stack. Its results do not establish F1/NGAP, UE registration, PDU sessions,
hitless upgrade or production service continuity.

The isolated Dell target runs OSM NBI/LCM/RO, a dummy VIM connector and a registered
Helm-v3 Kubernetes cluster. Descriptor identities are `p4kdu_knf` and `p4kdu_ns`;
the NS is `p4-kdu-upgrade-test`. Helm releases `p4kdu-cu` and `p4kdu-du` reside in
`p4-kdu-upgrade`. The experiment uses the existing `scripts/lifecycle.py`
controller, `lifecycle_osm.NbiLifecycle` and durable P1 runtime release state.
The source's HP endpoint/identity denylist and per-target namespace/NS checks
remain mandatory. The committed `P4_KDU_UPGRADE_APPROVAL` gate remains closed;
the existing runner's temporary experiment-only approval is not production approval.

## Methodology and executed scenarios

| Scenario | Confirmed outcome and purpose |
| --- | --- |
| `preflight` | Passed target isolation, registration, prerequisites and gate checks. |
| `onboard` | Passed disposable descriptor publication and revision-pinned package preparation. |
| `instantiate` | Passed initial NS creation and CU/DU readiness checks. |
| `upgrade_rev2_lost_response` | Passed adoption/reconciliation of an accepted upgrade whose response was lost, without blindly submitting it again. |
| `explicit_rollback` | Passed operator-directed rollback and verification of the intended application content. |
| `failed_upgrade_auto_rollback` | Passed intentionally failing DU upgrade and restoration through Helm atomic rollback/controller recovery. |
| `partial_failure_operator_resolution` | Passed mixed-version detection, retention of pending state and explicit operator resolution. |
| `lost_before_send` | Passed uncertain-request reconciliation when the upgrade request had not reached OSM. |

`lcm_restart_recovery` was **skipped**, because `--allow-lcm-restart` was not enabled.
The successful run therefore supplies no LCM-restart recovery qualification.

Failed DU upgrades are intentional fault-injection cases, not failed overall
experiment checks. The runner prepares a failing candidate, observes terminal OSM
outcomes and verifies the restored or operator-resolved state. Exact attempt
counts, operation IDs and transient timestamps must be taken from the referenced
archive; they are not supplied by the confirmation and are not reconstructed here.

## Rollback and recovery convergence

OSM reporting a terminal failure does not imply Helm's atomic rollback has already
finished. Helm can still be `pending-rollback`, and Kubernetes can still be changing.
The runner's `Experiment.convergence_state()` / `converge()` compare OSM operations
and NS readiness with Helm revision/status/history, chart/application identity,
ConfigMap release markers and Deployment readiness. Pending state is retained when
convergence cannot be established; terminal OSM status alone must not clear it.

Explicit rollback selects previous application content. Helm revisions continue
increasing during rollback and do not equal application revision numbers. Thus the
final Helm revisions 6 and 8 are consistent with both KDUs running application r2.
Partial failure requires operator resolution rather than an invented successful
all-KDU update. Lost responses require observation/adoption, never automatic retry
of an uncertain lifecycle mutation.

## Final consistency (operator-confirmed)

| Plane | Final observation |
| --- | --- |
| OSM | NS `57fd4942-6c44-4da4-8297-1a29d101286e`, READY / IDLE |
| Helm CU | Revision 6, application r2, deployed |
| Helm DU | Revision 8, application r2, deployed |
| Kubernetes | Both Deployments Ready; both Pods Running, zero restarts |
| P1 runtime state | Active CU/DU application revisions 2/2 |

These independent observations support the reported final consistency. They do not
claim an unchanged production RAN/Core or qualification of production upgrades.

## Historical VIM failure and repair boundary

Before the successful run, RO reported `Cannot decrypt information` with a diagnostic
asking whether the same COMMONKEY was used across OSM components. This identifies
a credential-decryption symptom; it does not by itself prove a specific key mismatch.
The operator reports repairing the dummy VIM credential through authenticated NBI
PATCH, after which RO reported connector load for VIM
`061f7fef-9e47-48cc-a0e6-764f4d5aff8d`, plugin `rovim_dummy`.
No password, token, key value, PATCH payload or repair automation is reproduced.
Kubernetes node health cannot establish RO credential or VIM health.

## New bounded preflight diagnostic

`p4_vim.diagnose_vim()` examines only known status/error fields in the selected VIM
record, `_admin`, `_admin.deployed` and `_admin.deployed.RO`. It reuses the existing
NBI registration response, adds no requests or subprocesses, examines at most
four sections and eight fields with 4096 characters per string, and emits fixed
reason codes only. It never traverses VIM configuration/credential dictionaries.

A known decryption error produces `FAILED / RO_VIM_CREDENTIAL_DECRYPTION_FAILED`.
Explicit ERROR/FAILED/DISABLED status produces `FAILED / VIM_REPORTED_UNUSABLE`.
Preflight records sanitized evidence and stops before onboarding. Missing metadata,
unrecognized messages, truncated status text or ENABLED alone remain INCONCLUSIVE;
they are not passed VIM health checks. Existing preflight can continue with that
explicit observation, preserving prior behavior for installations without RO status
exposure. No credential repair, COMMONKEY change or component restart is attempted.

This is a current NBI status diagnostic, not RO log surveillance. A failure appearing
only in RO logs remains invisible if NBI does not expose it. Historical errors must
not be presented as current after authenticated repair; operators should investigate
inconclusive evidence using separately authorized, scoped diagnostics.

## Limitations and future work

Independently verify the original archive manifest on its host before publication.
Inspect the archive for exact failed-attempt ordering and timings; no such values
are invented in this report. Validate installed OSM status-field coverage using a
separately authorized read-only check. Add a separately reviewed bounded RO status
provider if NBI cannot expose the failure; avoid broad log collection and ambiguous
VIM attribution. LCM restart recovery, real telecom interfaces, workload continuity,
performance under traffic and production lifecycle execution remain unqualified.

## Follow-up: original RO-only failure visibility

The first metadata-only diagnostic cannot detect the observed Dell failure when
NBI exposes a registered VIM without its RO error. Existence/identity checks do not
prove connector health. The original multiline error included `Cannot load vim`,
`Decrypting password database exception`, `Cannot decrypt information`, the
COMMONKEY question, and a subsequent `FailingConnector` subscription exception.
The latter is classified as connector failure, not independently as a key mismatch.

An optional `ro_diagnostics` configuration selects one exact RO `pod` and `container`
in the already verified target's configured OSM namespace. Preflight uses one
read-only `kubectl logs` request with timestamps, a 120-second lookback, 128-line
maximum, 65,536-byte limit, five-second request timeout and six-second parent
watchdog. Parent output is bounded independently of kubectl, stderr is discarded,
no retry occurs, and the child process group is killed/reaped on every exit path.
This path is not enabled without that explicit source configuration. No raw log
is written to disk or included in exceptions/evidence.

Only fresh timestamped records (including untimestamped continuation lines) with
exactly the selected VIM UUID can establish an outcome. Old, future, unbound or
multi-VIM records cannot produce FAILED or HEALTHY. A later explicit connector-load
record supersedes an earlier failure; a later failure overrides a load. Failure
wins equal timestamps. A missing error is never healthy. HEALTHY means only a
fresh explicit connector-loaded observation, not successful VIM operations, NS
instantiation or telecom protocol health. NBI-confirmed failure remains blocking
even if a log reports a load, pending operator reconciliation of contradictory
sources.

The RO Pod/container selection and installed log format require separate read-only
live validation. The exact historical raw records were not available here. If RO
logs split timestamped traceback messages without a VIM UUID in the same record,
this conservative parser returns INCONCLUSIVE rather than guessing attribution.
No automatic repair, live log query or experiment was performed during development.
