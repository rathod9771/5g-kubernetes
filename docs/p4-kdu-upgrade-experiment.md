# P4: OSM v19 in-place KDU upgrade experiment (second Dell only)

P4 validates, on a real OSM v19, what P1 assumes and has only exercised offline:
revision-pinned KDU upgrades on the existing NS, NS identity preservation, CU-before-DU
sequencing, `helm --atomic` failure and automatic rollback, explicit rollback,
lost-response reconciliation and recovery from an OSM LCM restart.

It runs **only on the second Dell** (172.30.0.20). The HP production system is
excluded by design and by runtime guards. The committed P4 gate
(`lifecycle.P4_KDU_UPGRADE_APPROVAL`) stays `None`; the runner opens it in memory, in its
own process, only around steps acting on the verified experiment NS.

## Artifacts

| Path | Purpose |
|---|---|
| `scripts/experiments/p4/chart/` | source chart: one ConfigMap (`release` marker) + one busybox Deployment |
| `scripts/experiments/p4/p4_packages.py` | deterministic KNF `p4kdu_knf` (KDUs `cu`, `du`) and NSD `p4kdu_ns`, revisions 1–3 |
| `scripts/experiments/p4/p4_experiment.py` | guarded runner: preflight, steps, evidence, teardown |
| `scripts/experiments/p4/p4-dell.example.json` | configuration template |
| `tests/p4_sim.py`, `tests/test_p4_experiment.py` | offline OSM/Helm/kubectl target and tests |

Revisions (descriptors are byte-identical, so OSM accepts each revision of the in-use KNF):

| Revision | cu | du |
|---|---|---|
| 1 | 0.1.0, marker `r1` | 0.1.0, `r1` |
| 2 | 0.2.0, `r2` | 0.2.0, `r2` |
| 3 | 0.3.0, `r3`, ready after 45 s (slow, for the LCM restart) | 0.3.0, `r3`, **never ready** |

Workload footprint: two pods (four during rollouts), 5m CPU / 8 MiB requested and
50m / 32 MiB limited each, image `busybox:1.38.0` pinned by digest, no Services,
no host access, no cluster-scoped objects.

## Isolated identities

| Item | Value |
|---|---|
| Kubernetes namespace | `p4-kdu-upgrade` (`additionalParamsForKdu.k8s-namespace`) |
| Helm releases | `p4kdu-cu`, `p4kdu-du` (`kdu-deployment-name`) |
| OSM VNFD / NSD | `p4kdu_knf` / `p4kdu_ns` |
| OSM NS | `p4-kdu-upgrade-test` (member-vnf-index `p4kdu`) |
| P1 state | `<work_dir>/state/active-ran.yaml` (never the dashboard's runtime state) |

## Steps and what each proves

| Step | Checks |
|---|---|
| `preflight` | target guards; OSM project/VIM/K8s cluster; LCM image digest; no leftovers; no P1 runner state from an earlier run (`<work_dir>/state` empty or absent); committed gate closed |
| `onboard` | revision 1 KNF/NSD published through P1's `publish` |
| `instantiate` | NS COMPLETED; KDUs in `p4-kdu-upgrade` as `p4kdu-cu`/`p4kdu-du`, member index `p4kdu`; revision-pinned `kdu-model` `…:1/p4kdu_knf/helm-chart-v3s/<kdu>`; runner/OSM clocks agree |
| `upgrade_rev2_lost_response` | P1 `process_release` to revision 2: CU then DU, `timeout_ns_action` sent, kdu_model `/app/storage/<id>:2/…`; the DU response is dropped after OSM accepted it → `UpdateUncertain` → `adopt_lost_request` finds the operation → `reconcile_update` confirms; both releases revision 2 / chart 0.2.0 / `r2`; **same NS id, same NS count**; DU started after CU completed; P1 records active=2, previous=1; NSR `kdu-model` and VNFR revision recorded as observations |
| `explicit_rollback` | OSM `rollback` action on du twice: 0.1.0/`r1`, then 0.2.0/`r2`; each a new Helm revision; cu untouched |
| `failed_upgrade_auto_rollback` | revision 3 with `auto_rollback`: CU upgrade COMPLETED, DU FAILED (`helm --atomic`: failed revision + "Rollback to …"), CU rolled back by P1; both back on `r2`; no pending update |
| `partial_failure_operator_resolution` | revision 3 without auto rollback: `failed-partial` (CU `r3`, DU `r2`); reconcile → operator decision; operator rollback of CU; pending update discarded with a receipt |
| `lost_before_send` | CU request lost before reaching OSM → adoption finds nothing → previous release confirmed; no new OSM action, Helm revisions unchanged |
| `lcm_restart_recovery` (opt-in) | OSM LCM restarted while the CU upgrade is PROCESSING; outcome, OSM operation state and Helm state recorded as observations; recovery (adopt, reconcile, `helm rollback` of a pending release in `p4-kdu-upgrade` only, OSM rollback actions) must end with both KDUs on `r2`, nothing pending, NS READY/idle |

The run stops at the first failed or broken step; evidence collected so far is kept.

### Helm release observation

Chart versions are never read from `helm status`: on the Dell its JSON carries no
`chart` object. Every check uses one cross-checked view per release:

| Source (`-o json`, namespace `p4-kdu-upgrade`) | Fields used |
|---|---|
| `helm status <release>` | `name`, `namespace`, `version` (revision), `info.status` |
| `helm history <release>` | per revision: `revision`, `status`, `chart` (`<name>-<version>`), `app_version`, `description` |
| `helm list -a` | the single entry named `<release>`: `revision` (string), `status`, `chart`, `app_version` |

The chart field must be exactly `p4kdu-<kdu>-X.Y.Z` (this release's chart, plain
version) in every history entry; the latest history entry must equal status
(revision, state) and the list entry (revision, state, chart, app version). A `chart`
object in `helm status`, if a Helm version emits one, must agree too. Anything
missing, duplicated or inconsistent (after one re-read) is recorded in the snapshot as
`helm.<kdu>.error` and fails that release's checks; nothing is inferred. The app
version is the marker (`r1`/`r2`/`r3`) and is checked alongside the ConfigMap.

### Namespace isolation

`validate-baseline` and `teardown` list `p4-kdu-upgrade` (Deployments, ReplicaSets, Pods,
Services, ConfigMaps, Secrets, StatefulSets, DaemonSets, Jobs, CronJobs, PVCs,
ServiceAccounts, Roles, RoleBindings, NetworkPolicies, Ingresses, HPAs, PDBs, Endpoints,
EndpointSlices, LimitRanges, ResourceQuotas, ReplicationControllers) and keep
only identity metadata (kind, name, uid, type, labels, `meta.helm.sh/*` annotations,
owner references); Secret data never reaches the evidence. Accepted objects:

| Object | Required identity |
|---|---|
| ConfigMap `kube-root-ca.crt`, ServiceAccount `default` | no owner, no labels (created by Kubernetes) |
| Deployment `R`, ConfigMap `R-release` (R = `p4kdu-cu`/`p4kdu-du`) | P4 chart labels, `app.kubernetes.io/instance=R`, `managed-by=Helm`, `meta.helm.sh/release-name=R`, `release-namespace=p4-kdu-upgrade`, no owner |
| ReplicaSet `R-<hash>` | sole controller owner = that Deployment (uid), `pod-template-hash=<hash>` |
| Pod `R-<hash>-<id>` | sole controller owner = an accepted ReplicaSet (uid), same hash and instance |
| Secret `sh.helm.release.v1.R.vN` | type `helm.sh/release.v1`, labels `owner=helm`, `name=R`, `version=N`, no owner |

Everything else, including objects merely named like an experiment object, is foreign:
`validate-baseline` fails and `teardown` removes nothing.

### Teardown safety

1. Target guards (as for every step).
2. Witness: identity and state of every other OSM NS are recorded (`other-ns-before`).
3. Namespace contents checked before anything is removed (table above).
4. NS identity proven from OSM records, not the name: the NS by id is named
   `p4-kdu-upgrade-test`; OSM recorded exactly one instantiate operation for it, for the
   `p4kdu_ns` package, requesting only (`p4kdu`, cu/du, `p4kdu-cu`/`p4kdu-du`,
   `p4-kdu-upgrade`); while instantiated its `nsd-id`, deployed KDUs and VNFR descriptors
   match; once terminated nothing is listed as deployed. Recorded as `ns-identity`.
5. Terminate only an idle NS; continue only if the operation COMPLETED and, re-verified,
   the NS is NOT_INSTANTIATED and idle with no running operation and no P4 Helm release
   left (`after-termination`). Otherwise the NS record and packages are kept.
6. Delete the NS record and confirm it is gone; delete `p4kdu_ns`/`p4kdu_knf` only if no NS
   references the NSD; delete the namespace only when no release and no foreign object
   remains.
7. `other-ns-after` must equal `other-ns-before` (check `every other OSM NS unchanged`),
   also when teardown stopped early.

Any refusal is recorded as the failed check `teardown completed` and leaves the remaining
resources in place for inspection.

`teardown-check` runs the same target, G1, G3 and G4 inspections without removing
anything: NBI GET only, `kubectl get/version` and `helm status/history/list/version` only
(enforced for `preflight`, `validate-baseline` and `teardown-check`), no P1 state access.
It also records the experiment NS operations (deleting the NS deletes them in OSM), explicit
`nsd-id`/`vnfd-id` checks, whether any other NS uses the experiment NSD, and every
NOT_INSTANTIATED NS with the number of KDUs it still lists.

### OSM v19 record formats (v19.0 sources, NBI and LCM)

- `NsLcmOpTopic.new` (`osm_nbi/instance_topics.py`) stores non-empty `primitive_params`
  JSON-encoded, otherwise `additionalParamsForVnf` JSON-encoded (seen on the Dell).
  `lifecycle.primitive_params()` and the G1 decoder accept the string or a mapping and
  reject invalid JSON, duplicate keys and non-mapping/non-list values.
- `NsLcm.terminate` (`osm_lcm/ns.py`) sets `nsState`/`_admin.nsState` to
  NOT_INSTANTIATED, `currentOperation` IDLE and `operational-status` terminated, but keeps
  `_admin.deployed.K8s` and `nsd-id` (reset only by the next instantiation). The current
  post-termination guard requires an empty KDU list, so on such an OSM teardown stops
  after a COMPLETED terminate with the NS record and packages kept. The guard is unchanged
  until Dell evidence (e.g. `teardown-check`'s terminated-NS observation) confirms it.
- NBI deletes an NS only when `_admin.nsState` is not INSTANTIATED, and deleting it
  deletes its operation records and VNFRs.

## P1 changes made for P4 (on this branch)

- `lifecycle.instance_name(entry)`: NS name from `ns_name` in the registry entry,
  defaulting to the dashboard's `ran-<key>` (unchanged for every existing scenario).
- `NbiLifecycle.kdu_action(..., timeout=None)` → OSM v19 `timeout_ns_action`
  (policy `kdu_timeout`); omitted when unset, so existing payloads are unchanged.
- `NbiLifecycle.ns_operations()` and `lifecycle.adopt_lost_request()`: lost-response
  reconciliation from OSM's operation records (P1 review item D3). Read-only towards
  OSM; ambiguous matches are never guessed; operations that ended before the request
  are ignored.

## Preconditions on the Dell

1. Kubernetes healthy again (Flannel recovered, `/run/flannel/subnet.env` present,
   all nodes Ready). That recovery is a separate activity; P4 must not start before it.
2. OSM v19 pods Running; NBI reachable; the K8s cluster registration ENABLED with
   helm-chart-v3; a VIM account (dummy) exists.
3. `python3` with PyYAML, `kubectl`, `helm` 3; repository checkout of this branch.
4. Recommended: LCM image digest equal to the HP's (`sha256:2a401465…71fbd`); the
   preflight records it so P4 approval can name the validated LCM.
5. Run on the Dell itself (the lost-request adoption window relies on the runner and
   OSM clocks agreeing; `instantiate` checks this).
6. The image `docker.io/library/busybox:1.38.0` pulls from Docker Hub unless cached.

## Commands (on the second Dell)

```bash
# 0. Get this branch (after it is pushed or copied with git bundle)
git -C ~/5g-kubernetes fetch origin p4/kdu-upgrade-experiment
git -C ~/5g-kubernetes worktree add ~/5g-kubernetes-p4 origin/p4/kdu-upgrade-experiment
cd ~/5g-kubernetes-p4

# 1. Read-only health checks (Flannel recovered, OSM running)
kubectl get nodes -o wide
kubectl get pods -n kube-flannel
kubectl get pods -n osm
helm list -A

# 2. Configuration (private files, never committed)
install -d -m 700 ~/.config/p4 ~/p4-run
cp scripts/experiments/p4/p4-dell.example.json ~/.config/p4/p4-dell.json   # then edit paths/names
( umask 077; printf '%s' 'OSM_ADMIN_PASSWORD' > ~/.config/p4/osm-password )

# 3. Offline self-test on the Dell (no cluster access)
python3 -m unittest tests.test_p4_experiment

# 4. Optional: inspect the packages
python3 scripts/experiments/p4/p4_experiment.py build-packages --output ~/p4-run/packages

# 5. Preflight (read-only: guards, registrations, LCM digest, leftovers)
python3 scripts/experiments/p4/p4_experiment.py preflight --config ~/.config/p4/p4-dell.json

# 6. Experiment (creates only the isolated identities above)
python3 scripts/experiments/p4/p4_experiment.py run --config ~/.config/p4/p4-dell.json --allow-lcm-restart
#    without --allow-lcm-restart the LCM restart step is skipped (recorded as skipped)

# 7. Evidence
ls ~/p4-run/evidence/run-*/
python3 -m json.tool ~/p4-run/evidence/run-*/summary.json | less

# 7b. Read-only check of an existing revision-1 deployment (after instantiate):
#     target guards, catalog revision 1, revision-pinned kdu-models, both releases
#     at Helm revision 1 / chart 0.1.0 / app version r1, ConfigMaps r1, Deployments
#     ready, NS READY/idle with only the instantiate operation, namespace contents,
#     P1 state bound to this NS with nothing pending. Writes only evidence files.
python3 scripts/experiments/p4/p4_experiment.py validate-baseline --config ~/.config/p4/p4-dell.json

# 7c. Read-only teardown inspection (target, NS identity, namespace contents, other NSs)
python3 scripts/experiments/p4/p4_experiment.py teardown-check --config ~/.config/p4/p4-dell.json

# 8. Teardown (NS, descriptors, releases, namespace p4-kdu-upgrade only)
python3 scripts/experiments/p4/p4_experiment.py teardown --config ~/.config/p4/p4-dell.json
```

`--allow-lcm-restart` runs `kubectl rollout restart deployment/lcm -n osm` on the Dell:
it interrupts every OSM operation there, so use it only when nothing else on the
Dell's OSM matters during the run.

## Opening the gate later

Only after a passing evidence summary is reviewed: a separate, reviewed commit sets
`P4_KDU_UPGRADE_APPROVAL` to the summary's `summary_sha256` (`p4_report_sha256`), the
LCM digest from preflight (`osm_lcm_digest`) and the approving commit. The HP's LCM
digest must equal the validated one before any live upgrade.
