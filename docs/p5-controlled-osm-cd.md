# P5: controlled OSM CD — phase 0, Dell-only safety foundation

## Current result

This change introduces a **read-only, offline CD readiness check**, seven unit tests,
and a GitHub-hosted workflow. It **does not** implement or authorize unattended
deployment. It deliberately leaves the P1/P4 controller, production approvals,
GitHub release-candidate workflow, P3 verifier policy, OSM catalog, and running
Kubernetes clusters unchanged.

Run locally from the repository root:

```bash
python3 scripts/experiments/p5/p5_readiness.py --assert-locked
python3 -m unittest discover -s tests -p 'test_p5_readiness.py' -v
```

A successful exit means the **existing protection is still closed**, not that P5
deployment can proceed. The JSON output reports `status: BLOCKED` and
`deployment_authorized: false`. No OSM credentials, kubeconfig, runtime release
state, or runner secrets are required; no network or cluster calls are made.
The P5 CI workflow runs on `ubuntu-latest`, with read-only repository access.

## Why deployment is blocked

- The P2 `.github/workflows/release-candidate.yml` creates immutable package
  candidates and provenance, never a deployment. It triggers on
  `reproducible-installer` and `feature/**`, not P4's `p4/**` branch.
- The P3 release verifier's committed policy
  (`config/release-verification.json`) is pinned to `hp-lab`, branch
  `reproducible-installer`, and a trusted GitHub-hosted attestation.
  That identity **must not be silently reused for Dell**.
- `scripts/lifecycle.py` has `P4_KDU_UPGRADE_APPROVAL = None`. The P4
  runner temporarily opened an experiment-only, in-process gate for a
  disposable CU/DU application. That is not production approval.
- P1 `process_release()` can publish a previously verified package and
  conditionally upgrade the active RAN, but no authorized target-side agent
  presently joins verified release records, operator approval, exact live
  state, and that controller. Publication alone cannot guarantee a live
  KDU rollout; OSM's `CHANGE_VNFPKG` was previously observed as a no-op
  for same-package revisions.
- The confirmed P4 79-check test used two synthetic busybox KDU workloads,
  *not* a real srsRAN/OAI gNB or Open5GS traffic-path qualification.
- The old HP production system and its runner are strictly out of scope.

## P5 deployment contract (future implementation)

```
GitHub push
  → offline CI (fail closed)
  → immutable RAN/Core candidate + manifest + hashes
  → GitHub-hosted provenance attestation
  → Dell agent pulls candidate outbound (never GitHub runner → OSM)
  → Dell-specific trust policy + independent attestation verification
  → operator-authorized, target/NS/namespace-allowlisted plan
  → acquire the existing P1 .lifecycle.lock
  → re-evaluate active NS, OSM revisions, Helm and image readiness
  → catalog publish under the P1 shared lock, never delete in-use entries
  → explicitly approved CU/DU or Core KDU upgrade sequence
  → health checks and bounded rollback/reconciliation
  → immutable evidence, operator escalation on unknown outcomes
```

### Work remaining before enabling CD

1. **Dell trust root:** Add a *separate* Dell release policy and a real
   GitHub-hosted release workflow for approved Dell test artifacts. Pin
   repository id, branch, signer workflow, architecture and artifact digests.
   Do not weaken or repurpose HP's verifier policy.
2. **Artifact and user-package contract:** Versioned RAN/Core descriptors,
   charts, image digests, namespace restrictions, user-supplied package
   validation, immutable original blobs and deterministic provenance.
3. **Operator-controlled target agent:** Outbound polling, no GitHub Actions
   self-hosted runner on HP, no GitHub-to-OSM secrets. Use a dedicated Dell
   service identity with least-privilege OSM rights; user consent before
   any catalog publication or KDU action.
4. **Durable state:** Reuse P1's `.lifecycle.lock`, `active-ran.yaml`, release
   ledger and recovery states. Do not build a second, independent controller.
5. **P4 signoff:** Review P4 evidence, LCM digest and approval provenance,
   require an explicit reviewed approval mechanism. Never enable this by
   environment variable or by a GitHub workflow alone.
6. **Safe rollout:** Match the exact NS UUID, target namespace, Helm releases,
   registered VIM/K8s cluster and active topology. Do not delete old OSM
   entries before proving unreferenced; for in-use packages, record revision
   and use verified in-place KDU actions.
7. **Fault testing:** Repeat lost-response, no-op, partial failure, rollback,
   LCM-restart and sustained load tests in a *disposable Dell NS* before
   admitting real RAN/Core upgrades. An OSM terminal FAILED can precede
   Helm's completion of atomic rollback.
8. **Production isolation:** No route, credential, or automation that can
   mutate the HP production target from Dell testing.


## Phase 1 — Offline release-plan inspection (implemented)

`scripts/experiments/p5/p5_plan.py` reads a **locally downloaded**, bounded
P2 release-candidate directory without contacting GitHub, OSM, Kubernetes,
Rancher, or any host. It reuses P3's strict layout/SHA256SUMS checking and
P2's independent manifest and package-identity verification against the
scenario registry. The operator must provide the **expected full Git SHA and
branch** independently; they are never taken from the artifact as authority.

Example after downloading a *real P2 release candidate* into an isolated
local directory (the previous P4 offline-test workflow produces **no**
release-candidate artifact):

```bash
python3 scripts/experiments/p5/p5_plan.py \
  --candidate-dir /path/to/downloaded/release-candidate \
  --expected-commit <full-40-character-commit-sha> \
  --expected-branch reproducible-installer
```

A valid input returns a JSON **proposal** with package hashes, RAN/Core
scenarios, registry KDU order, and blockers, always `BLOCKED`,
`trust: UNVERIFIED`, `target_observed: false`,
`catalog_publication_authorized: false`, and
`deployment_authorized: false`. The output is *not* an executable plan,
operator approval, a signed GitHub attestation, a Dell target observation,
or a lifecycle-controller input. It does not read any existing RAN
instance, change OSM descriptors, upload packages, or alter Kubernetes.

Rejection is fail-closed for missing, tampered, extra, symlinked, or
inconsistent artifact files; changed approval protections; mismatched
expected source identity; malformed manifests; or unsupported identities.
Run local Phase-1 regression tests:

```bash
python3 -m pip install 'PyYAML>=6.0,<7'
python3 -m unittest discover -s tests -p 'test_p5_*.py' -v
```

P5's GitHub Actions safety workflow runs these tests automatically.
A genuine Dell-specific signed release workflow, independently pinned
Dell trust policy, live target identity observer, and human authorization
remain **unimplemented**. Keep the HP verifier and P4 gate intact.

## Phase-0 acceptance criteria

- GitHub Actions reports a successful *P5 CD Safety Gate (no deployment)* run.
- `p5_readiness.py --assert-locked` returns exit 0 with
  `existing_policy_locked: true`, `status: BLOCKED` and
  `deployment_authorized: false`.
- Unit tests reject attempts to silently change the P4 gate, the P3 target,
  or P3 release branch.
- No catalog publication, NS action, rollback, image push, credential update,
  namespace mutation, or automatic live upgrade is performed.

**Important:** A green safety-check workflow is not completed CD. Subsequent
P5 phases need separate reviewed implementation and real Dell-only evidence.


## Phase 1B — Read-only Dell target observation

`scripts/experiments/p5/p5_target.py` is an *optional, explicit* read-only
observer. It tests the P4 baseline already confirmed by the operator on
27 October 2026:

- Local hostname must be `Precision-3680`.
- Current Kubernetes context must be `kubernetes-admin@kubernetes`.
- Kubernetes API URL must exactly match `https://172.30.0.20:6443`.
- P4 namespace `p4-kdu-upgrade` Active; exactly two ready deployments
  `p4kdu-cu`, `p4kdu-du`; two ready 0-restart pods.
- Helm CU revision 6 and DU revision 8, both `deployed`, chart
  `p4kdu-{cu,du}-0.2.0`, app version `r2`.
- The OSM Kubernetes namespace has ready pods, including NBI/LCM/RO,
  and internal NBI (9999/TCP) and RO (9090/TCP) services.

No cluster commands run until the hostname, context, API server and Helm
environment override checks pass. Once permitted, only `kubectl get`,
`kubectl config` and `helm list` with fixed arguments run. No
`kubectl exec`, `get secrets`, OSM tokens, namespace modifications,
catalog uploads, action primitives, or GitHub deployments are issued.

Dell operator execution, after verifying the currently active context:

```bash
cd ~/5g-kubernetes-p5
python3 scripts/experiments/p5/p5_target.py --observe
```

An `OBSERVED` result reports a **Kubernetes-and-Helm baseline match only**,
never deployment authority. Revisions are pinned to the successful P4
baseline, so planned future changes will intentionally require a separately
reviewed observation policy. No OSM NBI NS state or OSM VIM-to-cluster
binding is inferred from pod readiness.

For offline analysis of a previously collected JSON snapshot, use
`--snapshot <path>`; untrusted snapshots are **not proof of live host
identity** and cannot authorize deployment. Output contains reason codes
and fixed summary fields, not kubeconfig or OSM credentials.

The next phase must authenticate to OSM using a tightly scoped,
read-only service identity and independently validate the exact disposable
NS UUID, registered K8s cluster/VIM identity and absence of pending LCM
operations. A healthy Kubernetes namespace alone is insufficient.
