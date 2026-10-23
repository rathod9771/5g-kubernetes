# Phase 3 — observe-only CPU contention and Kubernetes capacity

Branch: feature/l3-detection-corroboration, following P2 HEAD 02e5517. The four
P1/P2 commits were verified as ancestors, the initial worktree was clean, and
execution/policy/shared-code protections were checked before development.

## Existing paths extended

- adapters.Prometheus.measurement: reject ambiguous source timestamp identities.
- adapters.Prometheus query/target normalization: retain matching identity labels.
- adapters.Kubernetes.snapshot: requested resources and scheduling metadata.
- adapters.Kubernetes.infrastructure: node allocatable, labels, taints, addresses,
  unschedulable status and explicit list completeness.
- adapters.Kubernetes.warnings: preserve fresh scheduler/resource UID evidence,
  extract only Insufficient cpu/memory classifications, never raw message text.
- observe.collect: feed existing policy events through the existing engine/store.
- observe.cpu_contention_evidence and capacity_evidence: scoped evidence decisions.

No exporter, watcher, scheduler, state store or lifecycle controller is created.
No Kubernetes read commands, API budget, polling interval or resource limit added.
Default collection still uses three Kubernetes list attempts and the existing
monitoring GETs (four without, six with Alertmanager when all sources respond).

## CPU semantics and source contract

| Measurement | Meaning | Phase 3 use |
|---|---|---|
| rate(node_pressure_cpu_waiting_seconds_total[5m]) | PSI some waiting-time ratio, not CPU utilization | Existing collector/source freshness query reused |
| container_cpu_usage_seconds_total rate | Container CPU core-equivalents, not contention | Existing performance collector reviewed; NOT_COLLECTED here |
| container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total rates | Fraction of CFS periods throttled, not CPU-time loss | Existing performance collector reviewed; NOT_COLLECTED here |

No substitute metric names or zero-filled missing measurements are introduced.
The PSI family was previously inventoried in L3-2. This phase does not claim a new
live source validation; usage/throttling queries from the separate performance
branch are not copied or activated.

A PSI observation requires a fresh, complete namespace/node inventory, a bound
active NS and canonical app/component workload on that node, a current node UID,
and exactly one valid node-exporter sample and matching fresh up scrape target.
Instance host must match the node name/InternalIP. Known target identity labels
must agree; an explicit cluster label must equal the bound cluster UID. Duplicate
identities, wrong labels, absent timestamps, future/stale samples, nonfinite or
out-of-range ratios stay unavailable. Context binding reuses the existing validated
runtime state; it is not cryptographic attestation of arbitrary input dictionaries.

The threshold >0.20 is the laboratory candidate in the existing example rule.
The existing policy's 60-second/distinct-observation persistence and clearing
windows are retained. The separate unapplied Prometheus example has a different
10-minute for window; it is not activated or represented as equivalent here.
A five-minute rate is bounded but does not independently certify full window
coverage after exporter startup/reset; source qualification remains required.
One high observation followed by a low one does not create an incident. Replayed
source timestamps cannot advance persistence. High utilization/throttling alone
cannot qualify an alarm. Service degradation remains unproven; only observe_notify
is proposed, never restart/switch/quota changes.

## Capacity semantics and proof boundary

CPU is converted to scheduler millicores internally and exposed as cores. Memory
uses integer bytes. Conversion supports nonnegative decimal/exponent quantities,
CPU n/u/m and memory decimal/binary suffixes; CPU is rounded to millicore precision
and memory to whole bytes. Numerical inputs are bounded. Integer CPU accounting
avoids floating-point boundary false shortages (300m minus 200m equals 100m).

Regular container requests are summed, with a configured limit fallback; absence
of both is scheduler-defined zero, not a missing usage metric. Init containers,
Pod-level resources, overhead and extended resources remain unknown rather than
using an incomplete accounting formula. No actual CPU/memory usage is substituted.

A pending, unscheduled owned pod can raise InsufficientClusterCapacity only if:

1. Pod/node/warning inventories are current, complete and unambiguous.
2. All observed nodes have explicit Ready=True and no memory/disk/PID pressure;
   unknown/failed node health is not converted into resource shortage.
3. Supported hard node selectors, cordoning and taints/tolerations identify the
   eligible nodes. Affinity, topology constraints, scheduling gates, custom
   schedulers and PVC-dependent placement are unknown in this implementation.
4. Its positive CPU or memory request exceeds the headroom upper bound on EVERY
   eligible node, with valid units and supported request accounting.
5. A fresh FailedScheduling warning from the recognized scheduler matches this
   exact pod name/UID/namespace and identifies a corresponding insufficient resource.

Namespace-bound allocated requests form a LOWER bound on total reserved resources.
Allocatable minus those requests is therefore an UPPER bound on headroom. Failing
that bound can prove current shortage; fitting it cannot prove schedulability,
since other namespaces and unsupported constraints remain unobserved. No cluster-
wide pod listing, new RBAC or schedulability simulator is introduced.

Unrelated pending causes, taints with no eligible nodes, missing metrics and node
pressure do not become resource-shortage alarms. Fresh PodScheduled=True on a
known Ready assigned node is explicit placement evidence for resolving this pod's
capacity condition, subject to existing 60-second clearing persistence. It does
not establish application or RAN health. Missing/replaced pods do not clear prior
identities. The existing capacity policy's zero firing persistence is unchanged;
a resource proof plus scheduler assertion can raise it immediately. All actions
remain observe_notify and production execution stays disabled.

## Offline validation and safety

46 new tests cover source/identity/freshness validation, high usage or throttling
without PSI, transient versus sustained pressure, unit conversion, exact milliCPU
boundaries, allocatable/request comparisons, placement constraints, unrelated
pending causes, incomplete/unknown state, deduplication/clearing and executor denial.
Both positive collector paths are exercised with fake adapters, retaining request
counts. Final complete regression result: **343 passed, 0 failed, in 183.256
seconds**: all 297 existing tests plus 46 new tests. The 16 existing sandbox
simulations remain covered. An earlier broad run also passed 343; after review,
label/inventory guards and exact millicore-boundary tests were strengthened and
the complete suite rerun. AST, whitespace, credential-pattern, allowed-path and
original-checkout fingerprint checks passed. No full-repository-suite claim.

No live API, kubectl, Helm, deployment, experiment or service command was executed.
No HP/Dell or Claude checkout modified. No changes to lifecycle.py, state_schema.py,
telecom/, workflows, OSM execution/transport, historical watcher, existing policies,
incident engine or SQLite schema. Existing production denial remains unconditional.
No push or merge; changes are local feature-branch commits.

## Deferred integration and remaining qualification

Claude's correlation interface can consume existing snapshots plus cpu_contention
and capacity, with NS/cluster/namespace/pod/node identities, original sample times,
explicit classifications and existing incident IDs. These are source assertions,
not proof that a release caused degradation. No second release tracker or rollback
executor is added. Before integration, agree schema/provenance/freshness and target
binding, and coordinate lifecycle.blocking_reason/pending_update under the existing
shared lock. Those shared-code changes remain deferred.

Live exporter labels and node mapping, PSI baselines/window coverage, scheduler
message variants, HA/multi-node placement, init/sidecar/Pod-level accounting,
affinity/topology/storage/GPU constraints require separate qualification. No live
activation is authorized by these offline changes.
