# Stage 5ZJ — S1 target staging read-only evidence handoff

**Scope:** bounded local evidence handling and read-only H-CRAN Kubernetes staging inventory.
**Status as of 19 November 2026:** S0 static qualification **PASS**;
S1 target inventory **NOT EXECUTED**; S2 fault injection **NOT AUTHORIZED**;
production **NO-GO**. Do not point this runbook at an active production
namespace. Stage5ZG frozen runtime remains at commit
`7fa911ece3b510b09351bf19c722007365ba47bd`.

## Verified inputs before S1

- [S0 static freeze preflight #44464221203](https://github.com/rathod9771/5g-kubernetes/actions/runs/44464221203):
  PASS; 12 frozen source blobs matched, CU/macro/small Helm chart rendering
  passed, existing inventory and freeze unit tests passed.
- [S0 full offline regression #44464221214](https://github.com/rathod9771/5g-kubernetes/actions/runs/44464221214):
  PASS; 1,166 tests executed, 3 skipped, zero failures.
- Target Kubernetes read-only RBAC context, dedicated staging namespace,
  image digest identity, host resource pressures, and actual pod readiness
  **have not been observed**. Do not interpret this document as their evidence.

## S1 execution on an explicitly approved target (operator only)

Do **not** use a default context, `kube-system`, the production namespace or
a general admin identity. The operator must confirm a dedicated staging
namespace and **read-only** service-account/RBAC access. This runbook never
creates the namespace or deploys workloads.

1. On the authorized control workstation, identify every node currently
   hosting one of the three H-CRAN pods, using a `get`-only request:

   ```bash
   CTX='<approved-staging-context>'
   NS='<dedicated-hcran-staging-namespace>'
   kubectl --context "$CTX" --namespace "$NS" \
     get pods -l ran-type=hcran \
     -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,STATUS:.status.phase
   ```

   If the target has no correctly labeled staging pods, S1 is
   **BLOCKED**. Never relabel, restart, scale, install, or silently switch
   Kubernetes contexts to make it pass.

2. On **each actual hosting node**, use the original read-only PSI helper.
   The `--node` parameter must be the actual Kubernetes node name printed
   by the approved inventory command. Do not collect host PSI only on the
   operator laptop; this would assess the wrong CPU/I/O device:

   ```bash
   python3 scripts/hcran_node_pressure.py \
     --node '<exact-hosting-node-name>' \
     --output ./stage5zj-pressure-this-node.json
   ```

   Copy just these small metric-only JSON files to the authorized control
   workstation (do not collect kubeconfig, raw ConfigMaps, Secrets, subscriber
   keys, RNTIs or logs).

3. On that control workstation, combine **all** node samples with the new
   Stage5ZJ local-only verifier. Repeat `--input` and `--expect-node` for
   every distinct hosting node. For one node, one pair suffices:

   ```bash
   python3 scripts/experiments/hcran_stage5zj_merge_pressure.py \
     --input ./stage5zj-pressure-node-a.json \
     --expect-node '<exact-node-a>' \
     --output ./stage5zj-pressure-all-nodes.json
   ```

   Two-node example (replace both placeholders with actual approved names):

   ```bash
   python3 scripts/experiments/hcran_stage5zj_merge_pressure.py \
     --input ./stage5zj-pressure-node-a.json \
     --input ./stage5zj-pressure-node-b.json \
     --expect-node '<exact-node-a>' \
     --expect-node '<exact-node-b>' \
     --output ./stage5zj-pressure-all-nodes.json
   ```

   The tool reads local files only, restricts input to exactly five
   allowlisted fields, rejects missing/extra/duplicate nodes, invalid
   measurements, future timestamps and samples older than five minutes,
   writes owner-only 0600 output, and reports **NO_GO**. Its completion is
   not a Kubernetes health verification or proof that the inputs originated
   on the expected machines.

4. With fresh pressure data, execute the **existing** vetted
   `kubectl get`-only H-CRAN staging inventory gate:

   ```bash
   python3 scripts/hcran_staging_inventory.py \
     --live-read-only \
     --context "$CTX" \
     --namespace "$NS" \
     --pressure ./stage5zj-pressure-all-nodes.json \
     --report ./stage5zj-s1-target-readonly-report.json
   ```

   The preflight checks the three CU/DU Deployments, Pods and Services,
   runtime image digests, restricted container security, token exposure,
   NEA2/NIA2 radio security, RIC/E2 disabled, live rollout readiness,
   exact hosting-node PSI coverage and pressure thresholds. CPU PSI
   `some avg10` must be <25, IO PSI <10, load per CPU <1.5, with
   fresh UTC timestamps. If any gate fails, report **BLOCKED** and stop;
   never weaken these limits to manufacture a pass.

Only the resulting **redacted assessment report**, after operator privacy
review, should be brought back for analysis. Do not commit a raw target
inventory, IP/IMSI/SUPI material, test credentials or kubeconfig to GitHub.

## Outcomes and next gates

- `BLOCKED` or missing report: S1 **NOT QUALIFIED**; no live staging.
- `READY_FOR_ISOLATED_STAGING_TEST_ONLY` with zero blockers: S1
  **read-only preflight passed**; this alone does not authorize deployment.
- S2 requires separate approval for an isolated namespace, immutable
  image digests, non-privileged networking, synthetic subscriber Secrets,
  independent observer, Prometheus/Grafana, rollback and Stage5P/5U/5Y
  fault-injection acceptance.
- Stage5ZG experiment/source freeze and Stage5ZH manual live approval gate
  stay untouched. No cherry-pick to `main`, OSM or production installer;
  no automatic runtime test and no production release.

**Limits:** Stage5ZJ combines operator-supplied data and cannot cryptographically
attest the host of origin. The target inspection has not been executed.
