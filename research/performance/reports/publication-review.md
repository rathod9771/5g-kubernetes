# Performance branch publication review

Publication scope: `feature/performance-evaluation` only, based on
`e0602ec8067ac20452064fc728f77996f30a691a`. No merge into the baseline branches.

## Focused verification

- 43 framework/exporter tests passed in 0.034 seconds.
- 32 existing Kubernetes/monitoring regression tests passed in 10.195 seconds.
- Total: 75 focused tests; no full-repository-suite claim.
- Python AST syntax, exporter YAML documents, JSON documents and source hashes pass.
- Five JSON/CSV bundles validate against their regenerated SHA256 manifests:
  521 real observation records. Source timestamps, values, units, missingness,
  trial identity and provenance are preserved. There is only one exploratory
  resource trial; no controlled architecture comparison is claimed.

## Publication sanitization

Operational private IP addresses, workload pod names, namespace/service/pod UUIDs
and private kubeconfig/worktree paths in evidence and instructions are replaced
with stable pseudonyms or operator-supplied placeholders. Equal original
identities have equal aliases, preserving joins and separate target dimensions.
Aliases are not usable endpoints or Kubernetes identities. Public image versions
and content digests remain for reproducibility; runtime image IDs are explicitly
not claimed as independently registry-verified manifest digests.

Publication hashes cover the sanitized copies, not the original acquisition
bytes. Original evidence was preserved in a mode-0700 private local directory
outside Git, with original manifests. The alias mapping is not published.
Sanitization does not turn real measurements into synthetic experiments.

No credentials, subscriber identities/keys, kubeconfig contents, runtime SQLite
journals, raw CU/DU logs or temporary test logs are included. A staged-content
pattern scan checks private keys, GitHub/AWS tokens, JWTs, credential-bearing URLs
and quoted secret assignments; it is not a proof against every possible secret.
Known protected identifiers in existing collector selection logic are configuration
bindings, not authentication secrets; they are not copied into published evidence.

Excluded from the commit: the three superseded files under reports/diagnostics/
and reports/dimension-preserving-statistics.json. They remain locally available.
Use final-statistics.json and offline-exporter-overhead-corrected.json.

## Production protection

Fresh read-only verification compared all 48 original Deployment/StatefulSet/
ConfigMap UID/spec/data fingerprints: zero changes. All 16 protected pods were
Ready (CU, DU, fog and 13 Open5GS). The live exporter SHA256 remained
`6280f5cd0f21f7293ba98cd4d08c1ba4568952e2269a9175a171b33d7a15063a`.
Dashboard service was active; legacy Layer 3 watcher inactive/dead.
OSM NBI health remains UNAVAILABLE under the existing credential-policy block;
Kubernetes readiness does not independently establish NS READY/running/IDLE.

No exporter deployment, monitoring reconfiguration, workload restart, active
performance experiment or lifecycle mutation was performed. Original checkout
HEAD, working-tree diff and status were compared before publication preparation.
Git object/reference changes for the authorized feature publication are shared
worktree metadata; original checkout files and branch are preserved.
