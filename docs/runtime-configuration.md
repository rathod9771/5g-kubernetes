# P0-B1 runtime configuration checkpoint

Scope: portability and configuration against
`4db25352a4eb04e0bdf0849458a221b51991e2dc`. No live Kubernetes/OSM access,
installation, onboarding, restart, state adoption, private configuration migration,
legacy package modification or commit is part of this checkpoint.

## Configuration authority and handoff

`scripts/runtime_config.py` defines the runtime contract documented by
`config/global.env.example`. Interactive precedence is built-in defaults, private
`config/global.env`, then allowlisted environment variables. Values are literal;
configuration is not sourced or evaluated. Only path fields expand HOME notation.
Returned configurations are immutable. Unsupported PLMN/profile/network overrides
fail explicitly instead of being accepted without changing canonical charts.

An operation can hand off its validated configuration through an owner-only JSON
snapshot named by `P0_RUNTIME_CONFIG`. The snapshot is authoritative: subsequent
inherited overrides cannot silently alter that operation. Snapshot reads validate
one opened descriptor, its regular-file type, owner and permissions, with
`O_NOFOLLOW`; directory paths also reject symlinks/traversal. Private `global.env`
requires mode 0600 or stricter. The public example remains readable. The existing
private file has not been changed; its permission rejection remains intentional.
A future approved migration should parse literals without sourcing the old file,
create a validated replacement in an owner-controlled directory at mode 0600 before
writing secrets, and publish it atomically. It must not log values or implicitly
adopt historical instance IDs.

Shell consumers disable tracing before loading credentials and restore the prior
trace setting afterward. Credentials are neither retained as shell variables nor
exported individually. Children receive a private temporary snapshot path. Shell
buffers are removed at process exit. Configured values never enter shell evaluation.

Generated services use a persistent `.runtime/<kind>-service.json` snapshot.
Dashboard snapshots retain only the OSM password; watcher/Rancher-forward snapshots
retain no passwords. Secrets do not enter units or command arguments. The unit
contains a nonsecret configuration revision. Existing active dashboard/watcher
services must match both the generated unit and private snapshot before installer
mutations; configuration drift requires explicit reconciliation, not silent reuse.

Systemd `WorkingDirectory` uses path/specifier semantics. `ExecStart` uses a fixed
Python executable and a launcher with literal arguments; its `:` prefix disables
systemd environment expansion. The launcher uses `execv`/`execvp`, never a shell.
This supports spaces, single quotes, dollar signs, Unicode, percent signs and shell
metacharacters. Independent tests use `systemd-analyze verify`, not guessed strings.
Rancher forwarding resolves kubectl through the service PATH rather than assuming
it lives in `/usr/bin`. Its activation remains outside this checkpoint.

## Management and workload clusters

`KUBECONFIG_PATH` selects management: Kubernetes bootstrap, Longhorn, cert-manager,
OSM, Rancher and central monitoring. `OSM_KUBECONFIG_PATH` selects workload: core,
RAN and workload exporters/probes. They can intentionally select the same cluster.

Shell helpers `p_kubectl`/`p_helm` and `r_kubectl`/`r_helm` always pass an explicit
kubeconfig. The compatibility `k_` helper means workload. Management checks and
mutations use management helpers; core/RAN checks and exporter removal use workload
helpers. Python workload queries also carry the workload kubeconfig explicitly.

Lifecycle state binds project UUID, VIM UUID, OSM endpoint, actual cluster identity
(the kube-system namespace UID), workload namespace UID/name, checkout, workload
kubeconfig path and deployment profile. Missing stable identities fail closed.
When an OSM cluster ID is explicitly configured, its structured credentials must
resolve to the same kube-system UID. Executable authentication plugins, host credential-file references and disabled
TLS verification in credentials obtained from OSM are rejected. This is tested with fixtures; live API conventions
and permissions remain unverified. Without an explicit OSM cluster ID, state still
binds the actual workload cluster but no OSM scheduling-association claim is made.

The full state schema is validated before termination/instantiation, including
active identities/scenario consistency, additive mappings, pending stages/operations
and bound context. Even empty unbound state cannot be implicitly adopted. P0-A
lifecycle serialization, atomic replacement, durable pending identities and exact
catalog-byte verification before teardown remain in place.

New state lives under `.runtime/` or an explicitly configured private external
runtime directory. The historical tracked `ran-selector/active-ran.yaml` is preserved
and never automatically read as current state. `init-state` derives a missing OSM
endpoint consistently, verifies identities and an empty remote instance listing,
and initializes local state explicitly. Existing instances require separate adoption
review; no adoption/migration command was run here.

## Discovery and validation

Host discovery validates structured interface/address/route responses, including
nested types and IPv4 prefixes. Automatic selection rejects obvious Docker/CNI,
overlay, container and VPN interfaces. Exceptional interfaces require both explicit
HOST_IP and HOST_INTERFACE, which must match. Ambiguous routes, missing IPv4,
missing interfaces and malformed responses produce sanitized configuration errors.

Validation checks host connected-prefix overlap with pod/service/UE/edge ranges and
the reserved canonical IMS range `10.46.0.0/16`. Core and UE referring to the same
canonical UE range is an intentional relationship, not a second independent network.
IMS addressing and the P-CSCF discrepancy are not reconciled here. Noncanonical
UE/edge/PLMN settings remain blocked pending reconciled profiles.

OSM operations load one configuration snapshot. Explicit OSM endpoints do not require
unrelated host-route discovery. The installer and local-machine preflight explicitly
request host networking validation. Project/VIM selection rejects missing, ambiguous,
stale or malformed identities. An explicitly supplied project UUID is also used for
authentication and token-cache identity. Status preserves discovery errors and
nonzero exit codes; required OSM namespaces cannot be explicitly empty.

Watcher configuration requires an explicitly selected ready failover scenario before
installer mutations. There is no implicit target or silently disabled watcher.
The RAN throughput benchmark selects only ready non-additive scenarios; F-RAN is
not a RAN replacement and is rejected as a benchmark target. Benchmark server
management remains an explicit external dependency.

## Helm and preserved boundaries

Unused ready OAI `cu.amf.address` and `network.masterIf` remain empty. No reference
machine values are restored. Independent rendering against the baseline compares
13 charts/39 resources for all seven ready scenarios. Only three CU checksum
annotations differ; all DU renders and every other resource field match.

The existing checksum hashes the entire values map. Changing its semantics has not
been proven safe for every input, so it is retained. A future upgrade may cause a
one-time CU rollout. No arbitrary compatibility override is introduced and no
rollout occurs in this checkpoint. Open5GS, IMS, O-RAN and H-CRAN reconciliation,
CI redesign and full installer/offline wiring remain deferred.

The baseline inventory is [p0b1-reference-inventory.tsv](p0b1-reference-inventory.tsv).
Remaining source literals are intentional canonical defaults, dormant blocked/manual
workflows, tests, documentation or preserved legacy package references. This does
not claim that dormant manual scripts are portable or that fresh-host deployment
has been proven.

## Adversarial review disposition

| Previous finding | Disposition | Independent evidence |
|---|---|---|
| Systemd WorkingDirectory and executable encoding | FIXED | Actual parser accepts all requested path classes; launcher argv tested |
| Mixed management/workload kubeconfigs | FIXED | Synthetic binaries assert exact kubeconfig arguments for both tools |
| Missing cluster/namespace context binding | FIXED | Different stable UIDs reject previous state before lifecycle calls |
| Lost service environment overrides | FIXED | Snapshot round-trip, unit revision, secret-only drift and consumer-minimum tests |
| Shell tracing/exported credentials | FIXED | Synthetic sentinel absent from xtrace and child environment |
| Container/VPN interface guessing | FIXED | Auto-selection rejected; explicit matched pair required |
| Host-prefix and IMS overlap omissions | FIXED | Conflicting connected-prefix and canonical IMS fixtures rejected |
| Malformed nested discovery objects | FIXED | Interface/route/namespace/service malformed fixtures fail cleanly |
| Incomplete runtime-state validation | FIXED | Malformed additive/pending/identity state makes no lifecycle calls |
| Repeated config loads/mandatory unrelated route discovery | FIXED | One-load explicit-endpoint test and immutable operation snapshots |
| Empty-state automatic endpoint derivation | FIXED | Mocked initialization derives endpoint once and binds stable UIDs |
| Watcher activation without target | FIXED | Requirements checked before installer mutations |
| Status hides discovery errors | FIXED | Failing adapter preserves error and exit code |
| Secret-file pathname TOCTOU | FIXED | Descriptor validation before reading; pathname replacement reads original FD |
| Empty required namespace | FIXED | Empty namespace rejected |
| Tests assume their own systemd encoding | FIXED | Independent native parser and actual launcher tests |
| Documentation overstates evidence | FIXED | Local guarantees separated from deferred live compatibility |

Additional regressions cover explicit project-ID authentication, F-RAN benchmark
exclusion, missing cluster identities, OSM credential-plugin rejection, unbound empty
state, immutable snapshots and fresh-clone nonadoption of historical tracked state.

## Final local verification

- Complete regression: **121 tests passed** (88 existing and 33 added adversarial
  tests), in 119.896 seconds on the final source.
- Native systemd parser: all **18 service/path combinations** passed (three service
  kinds across six path classes), without starting services.
- Separate-context tests assert all four explicit management/workload kubectl/Helm
  argument lists. Context drift/malformed state makes no lifecycle calls.
- Synthetic password sentinel absent from shell tracing and child environments;
  snapshots/Helm secret values stay owner-only; units/argv contain no credentials.
- Temporary-output `prepare --ready` and `validate --ready`: all seven scenarios
  passed Helm/source/profile/descriptor/staging/archive/provenance checks.
- Independent baseline Helm comparison: 13 charts/39 resources, only three CU
  checksum differences; no workload behavior/configuration differences.
- Python compilation without bytecode: 13 files; shell syntax: eight scripts;
  dashboard JavaScript syntax and `git diff --check`: passed.
- Original 18 diagnostics unchanged by SHA256/size/mtime; all 426 legacy package
  files unchanged; historical tracked state matches HEAD.
- Secret-signature scan: only the preexisting synthetic private-key negative test
  fixture matched. No new real credential/private-key/runtime-data additions.
- Branch and HEAD unchanged; nothing staged, committed or pushed. Generated build
  artifacts are not tracked. No live cluster/OSM/service or private-config changes.

The nine new source/documentation/test files remain untracked for review. Git's
normal diff stat below counts only the 28 modified tracked files. The original
18 diagnostic untracked files are preserved alongside the nine intended new files.

### git diff --stat

```text
 .gitignore                                   |   5 +
 bench-all-ran.sh                             |  54 ++++++----
 config/global.env.example                    | 138 ++++++++++--------------
 deploy.sh                                    |   4 +-
 deploy/ran-selector.service                  |   2 +-
 deploy/rancher-portforward.service           |   4 +-
 helm/cran-oai/cu/values.yaml                 |   4 +-
 helm/cran-oai/du/values.yaml                 |   2 +-
 install.sh                                   | 112 +++++++++----------
 layer3-autonomous/layer3-watcher.service     |   7 +-
 layer3-autonomous/watcher.py                 |  26 ++---
 monitoring/grafana-ingress.yaml              |   3 +-
 monitoring/kube-prometheus-stack-values.yaml |   2 +-
 monitoring/latency-probe/deployment.yaml     |   5 +-
 monitoring/prometheus-nodeport.yaml          |   3 +-
 monitoring/ran-exporter/deployment.yaml      |   5 +-
 monitoring/ran-exporter/rbac.yaml            |   9 +-
 ran-selector/backend.py                      | 154 +++++++++++++++++++--------
 ran-selector/index.html                      |  23 +++-
 ran-selector/osm_client.py                   |  88 +++++++++++----
 scripts/common.sh                            |  73 +++++++------
 scripts/osm_catalog.py                       |   4 +-
 scripts/osm_onboard.py                       |  11 +-
 scripts/preflight.sh                         |  11 +-
 scripts/validate.sh                          |  22 ++--
 status.sh                                    |  17 ++-
 tests/test_p0a_safety.py                     |  14 ++-
 uninstall.sh                                 |  24 ++---
 28 files changed, 464 insertions(+), 362 deletions(-)
```

### git status --short

```text
 M .gitignore
 M bench-all-ran.sh
 M config/global.env.example
 M deploy.sh
 M deploy/ran-selector.service
 M deploy/rancher-portforward.service
 M helm/cran-oai/cu/values.yaml
 M helm/cran-oai/du/values.yaml
 M install.sh
 M layer3-autonomous/layer3-watcher.service
 M layer3-autonomous/watcher.py
 M monitoring/grafana-ingress.yaml
 M monitoring/kube-prometheus-stack-values.yaml
 M monitoring/latency-probe/deployment.yaml
 M monitoring/prometheus-nodeport.yaml
 M monitoring/ran-exporter/deployment.yaml
 M monitoring/ran-exporter/rbac.yaml
 M ran-selector/backend.py
 M ran-selector/index.html
 M ran-selector/osm_client.py
 M scripts/common.sh
 M scripts/osm_catalog.py
 M scripts/osm_onboard.py
 M scripts/preflight.sh
 M scripts/validate.sh
 M status.sh
 M tests/test_p0a_safety.py
 M uninstall.sh
?? docs/p0b1-reference-inventory.tsv
?? docs/runtime-configuration.md
?? osm-packages/cu_repro_knf.tar.gz
?? osm-packages/cu_repro_ns.tar.gz
?? ran-selector/backend.py.before-client-events
?? ran-selector/backend.py.before-live-fix
?? ran-selector/backend.py.before-sctp-events-fix
?? ran-selector/index.html.before-client-events
?? ran-selector/index.html.before-dashboard-optimized
?? ran-selector/index.html.before-dashboard-redesign
?? ran-selector/index.html.before-dashboard-redesign-2
?? ran-selector/index.html.before-grafana-edit
?? ran-selector/index.html.before-layout-fix
?? ran-selector/index.html.before-live-fix
?? ran-selector/index.html.before-novathinktech-theme
?? ran-selector/index.html.before-novathinktech-v2
?? ran-selector/index.html.before-novathinktech-v3
?? ran-selector/index.html.before-ue-v4
?? ran-selector/index.html.before-v5
?? ran-selector/index.html.before-v7
?? scripts/runtime_config.py
?? scripts/runtime_render.py
?? scripts/runtime_state.py
?? scripts/service_entry.py
?? scripts/state_schema.py
?? tests/test_p0b1_adversarial.py
?? tests/test_runtime_config.py
```
