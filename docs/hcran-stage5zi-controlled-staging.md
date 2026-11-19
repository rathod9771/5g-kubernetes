# H-CRAN Stage 5ZG freeze and Stage 5ZI controlled staging qualification

**Decision recorded 19 November 2026.** Stage 5ZG experimental qualification
is **COMPLETE** for the bounded, isolated two-UE RFsim experiment. This is
**not** an H-CRAN production release or a claim that historical intermittent
recovery defects are proven eliminated.

## 1. What is frozen

Authoritative frozen experimental commit:
`7fa911ece3b510b09351bf19c722007365ba47bd`.

Read-only reference branch (by convention; branch pointers are movable unless
protected by the repository owner):
`experiment/hcran-stage5zg-qualified-freeze-20261119`.

The full SHA and Git blob IDs in
`experiments/oai-ngap-2026w13/stage5zi/stage5zg-experimental-freeze.json`
are the integrity anchors; do **not** assume the moving head of
`experiment/hcran-stage5zg-sib1-message-handoff-20261119` is the exact
runtime-tested source. The later `c2cadb476e28e9a06354036d9d205ed21226f19c`
was a *test-contract-only* edit. The Stage 5ZH CI safety gate is
`107069be89166c4b9a58845beb1b75a2250083dc`; that revision passed
[Offline Project Validation #44461469754](https://github.com/rathod9771/5g-kubernetes/actions/runs/44461469754),
1,158 executed / 3 skipped / no failures.

Stage 5ZG live [run #44457384676](https://github.com/rathod9771/5g-kubernetes/actions/runs/44457384676):
2 independently hosted sessions, 3 actual AMF restart cycles each,
6/6 genuine recovery passes, 18/18 negative-control rejections,
both per-cell AMF-ingress checks passed, fresh PDU and F1-U/NG-U/ICMP
data-path recovery. Two bounded timelines had 0 collection issues.
This evidence concerns isolated RFsim with synthetic subscribers only.

UE upstream W45 pinned to
`dfb296ab867050a7bde81b393fa31b757ada2c83`, with frozen Stage5G/I/K/M/Q/T
research changes and log-only Stage5ZD/ZF/ZG probes. CU remains from the
separately pinned Stage3 source-builder. The frozen UE is **test-only**.
No production runtime artifact or digest-qualified staging image exists here.

### Hard freeze rule

Do **not** edit frozen UE/CU builders, protocol patches, protocol timeline
collector, Stage5P/5U/5Y acceptors or H-CRAN source charts while claiming
the same Stage5ZG experimental baseline. To change any of those, create
a new experiment identifier, peer review the diff and re-run qualification.
The Stage5ZI static gate compares **current Git blobs** with historical,
exact-SHA blobs and rejects unreviewed drift.

Repository owners should protect the snapshot branch against force-pushes
and deletion; the branch itself is **not cryptographically immutable**.

## 2. Branch and merge strategy

**Now:**
- Preserve the exact passed commit via the frozen reference branch.
- Make new *staging/qualification-only* commits to
  `experiment/hcran-stage5zi-controlled-staging-20261119`, based on the
  validated Stage5ZH CI-gated revision.
- Do not cherry-pick every earlier experiment's fixes into `main`
  or the production installer. Do not merge every branch separately.

**After static and target staging gates pass:** produce one **reviewed,
squashed/integrated change set** containing the accepted implementation,
pinned images, real production security and monitoring configuration,
rollback and signed evidence. A staging/integration branch can be used
for that PR; preserve commit provenance in its description and freeze manifest.

**Only after separate production GO approval:** merge reviewed release
changes to `main` via branch protections and required checks. Do not
replace the current runtime with a log-only research build by default.
Near-RT RIC/E2SM-KPM remain excluded for H-CRAN; monitoring is Prometheus
and Grafana. No unreviewed changes to OSM/reproducible installer.

## 3. Staging acceptance gates (finite, fail-closed)

### Gate S0 — static freeze/source qualification (GitHub-hosted)

Automatic, static-only GitHub workflow
`.github/workflows/hcran-stage5zi-staging-static.yml`:

- Verify pinned source commit is an ancestor of staging branch and all
  frozen builder/verifier/chart Git blob IDs match **exactly**.
- Verify metadata for the six-cycle qualification and production NO-GO.
- Verify Stage5ZH expensive live workflow is explicit-dispatch only,
  requires the exact-SHA offline validation gate, and retains Stage5P/5U/5Y.
- Run adversarial unit tests of existing staging inventory and new freeze
  guards; render CU + macro/small DU Helm templates **without deployment**.
- Emit source-integrity report marked `STATIC_FREEZE_VALIDATED`.
  Static render uses historic *test image tags*, not deployable digest pins.

**Pass S0 grants only permission to consider target READ-ONLY inspection.**
It does not approve staging pod modifications, production, or a live UE test.

### Gate S1 — target staging read-only inventory (NOT EXECUTED)

For the explicit per-node read-only handoff, sample format, strict source checks,
and safe operator commands, see [`Stage 5ZJ S1 evidence handoff`](hcran-stage5zj-s1-readonly-handoff.md).

Target must be an explicitly approved namespace/context. The existing
`scripts/hcran_staging_inventory.py` already performs bounded
**`kubectl get` ONLY**, checks all three H-CRAN deployments/pods/services,
ready current rollout, container identity, immutable image digests,
nonprivileged operation, restricted token mount, service/network exposure,
strict NEA2/NIA2 radio-security config, no Near-RT RIC/E2 agent, and
independent fresh resource pressure for **every** hosting node.

On each hosting node only, capture a timestamped resource sample
*on that node* (running this on a workstation does **not** measure a
remote node):

```bash
python3 scripts/hcran_node_pressure.py --node '<actual-K8s-node-name>' \
  --output ./local-node-pressure.json
```

For multi-node staging, combine each local `nodes[]` entry into one JSON
`{"nodes":[...]}` with accurate matching node names and UTC timestamps,
and run the assessment within the script's five-minute freshness window.

On an authorized operator workstation with a **read-only Kubernetes RBAC**
identity and explicit non-system namespace:

```bash
python3 scripts/hcran_staging_inventory.py --live-read-only \
  --context '<approved-staging-kube-context>' \
  --namespace '<dedicated-hcran-staging-namespace>' \
  --pressure ./staging-pressure-all-nodes.json \
  --report ./hcran-stage5zi-target-readonly-report.json
```

The script does **not** apply/delete/patch/scale anything, execute in
pods, read Secrets, contact OSM or collect subscribers. Review only the
redacted report before considering Gate S2. **Do not upload raw pod/config
snapshots, passwords, subscriber keys, private evidence ZIPs or kubeconfig.**

S1 passes only if the report states
`READY_FOR_ISOLATED_STAGING_TEST_ONLY` with no blockers.
On the previously observed overloaded HP node, CPU/IO pressure and
privileged containers may trigger expected **BLOCKED** findings; do not
weaken the safety gate to manufacture a pass.

### Gate S2 — controlled staging experiment (NOT AUTHORIZED)

Requires S1 success, separately approved nonproduction staging namespace,
reviewed digest-pinned CU/DU/UE images, distinct **synthetic** subscriber
Secrets, isolated network/access policy, explicit resource quotas and
node placement, verified Prometheus/Grafana telemetry, safe rollback
strategy, and independent operator approval.

Only then run bounded two-UE registration/PDU, per-DU F1AP/F1-U and
NGAP/NG-U evidence, AMF recovery repetitions with genuine Stage5P
success criteria, Stage5U negative controls, Stage5Y per-cell ingress,
baseline and fault-injected SLOs, and rollback. Existing fail-closed
runtime gates must be preserved. On any regression: collect bounded,
redacted proof, stop and roll back. **Do not treat one green run as
proof the historical intermittent failure is permanently fixed.**

### Gate S3 — release and production (NOT AUTHORIZED)

Separate security review (NEA2/NIA2 interoperability, secrets, RBAC,
network policy), sustained stability/resource capacity on target hardware,
Prometheus/Grafana KPIs, fault coverage and repeatability policy,
backup/recovery and controlled downgrade, full documented 3GPP Rel-15/16
scope and gaps, release image provenance/SBOM and signatures, human
approvals, and reproducible installer compatibility.

Release decision remains **NO-GO** unless all planned S2/S3 controls
have evidence from the **exact staged release digest set**.

## 4. Current outcome

- Stage5ZG bounded isolated experimental qualification: **COMPLETE (6/6)**.
- Stage5ZH offline-gated CI controls: **VALIDATED**.
- Stage5ZI S0 source freeze, dry-run chart render, static CI: **IN PROGRESS**
  until GitHub Actions confirms exact staging branch commit.
- Stage5ZI S1 target read-only: **NOT EXECUTED** (no authorized target access).
- S2 live staging: **NOT AUTHORIZED**.
- S3 production: **NO-GO**.

No mutation of `main`, HP Kubernetes, OSM, production SIMs/keys, or
the reproducible installer.
