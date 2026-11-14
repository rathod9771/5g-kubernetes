# H-CRAN verified-source promotion (14 November 2026)

## Scope and decision

This promotion is **source-only** and **NO DEPLOY**. It synchronizes the
offline-tested **3GPP-oriented, RIC-free H-CRAN CU Helm profile** for OAI and
srsRAN with the reproducible installer; it also includes the **historical**
isolated two-UE Stage 4O acceptance summary, a read-only inventory, and offline
Helm contract tests.

**It is not a production certificate and it does not deploy a patched OAI CU
or UE to any host.** The installer still builds/uses the independently approved
images according to its existing image policy and source lock.

### Promoted evidence-supported capabilities

- H-CRAN CU/DU configuration based on NG, F1-C, and F1-U, with macro and
  small DUs as distinct logical instances.
- H-CRAN CU chart rejects `e2Agent.enabled`, `ric.enabled`, or
  `e2.enabled` when rendering a H-CRAN profile. Non-H-CRAN scenarios
  retain their existing behavior. Prometheus/Grafana remain independent.
- Stage 4O, run `43194751671`: disposable Open5GS + RFsim, two synthetic
  UEs with distinct identities and IPs, real NAS/PDU acceptance, both DUs,
  real UL/DL F1-U and NG-U GTP-U and concurrent ICMP. This was an **isolated
  CI experiment**, not a successful live-cluster deployment.
- Existing offline H-CRAN contract tests and read-only historical
  evidence inventory. They must fail closed on unverified production gates.

### Deliberately excluded from stable source promotion

- Stage 5I/5J/5K experimental UE recovery patches and patched UE Docker
  images. Stage 5K run `43569504071` **FAILED**: one UE exited with
  code 139 during AMF recovery; full post-fault user plane was not proved.
- Source-pinned OAI CU patch is not installed automatically; its artifacts
  require separate provenance and staging acceptance.
- No live RIC removal, Kubernetes modifications, OSM catalog changes,
  synthetic subscriber writes, or UE process restarts on the HP host.
- No unconditional image publication or production authorization.

### Read-only validation from a checked-out working tree

Run from the repository root after installing the project's normal CI tools
(Helm v3.21 and Python dependencies):

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_hcran_3gpp_contract.py'
helm template hcran-oai-cu helm/cran-oai/cu \
  -f helm/cran-oai/cu/values-hcran.yaml \
  --set-string image.reference=example.invalid/hcran-static:validate \
  --set-string image.pullPolicy=Never > /tmp/hcran-oai-cu-render.yaml
helm template hcran-srsran-cu helm/cran-srsran/cu \
  -f helm/cran-srsran/cu/values-hcran.yaml \
  --set-string image.reference=example.invalid/hcran-static:validate \
  --set-string image.pullPolicy=Never > /tmp/hcran-srsran-cu-render.yaml
```

These commands **do not deploy**. Do not execute `./install.sh`,
`helm upgrade`, `kubectl apply`, or any OSM write based solely on a
passing offline check.

### Local-machine adoption

There is no local SSH/desktop connection in this repository promotion.
**A GitHub commit is not a patch on the HP machine.** On that machine,
preserve uncommitted work first. To inspect the exact upstream promotion:

```bash
cd ~/5g-kubernetes-hp-integration
git status --short
git remote -v
git fetch --prune origin
git log --oneline --max-count=4 origin/reproducible-installer
git diff --stat HEAD..origin/reproducible-installer
```

Do **not** run `git reset --hard` or overwrite the active local worktree.
Use a separate clean worktree or a reviewed fast-forward only after verifying
the live inventory and selecting the intended release. Existing H-CRAN pods
continue running their prior images until an independently approved
deployment procedure changes them.

**Production decision: NO-GO** pending reliable unassisted dual-UE AMF
recovery, post-fault F1-U/NG-U, sustained stability, negative security tests,
staging rollout/rollback, and explicit operator authorization.
