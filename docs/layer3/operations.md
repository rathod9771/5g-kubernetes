# Operations, alarm catalog and remediation policy

Every candidate alarm is defined in `config/assurance-policies.json`; metric/API
support is recorded in `alarm-capabilities.json`. Local thresholds are reviewable
laboratory defaults requiring operator baselines. 12 Prometheus examples use observed
families; the rest remain API-evidence candidates or unsupported rules.

| Scenario / trigger | Source and supporting evidence | Priority | Safe policy / action class | Verify |
|---|---|---|---|---|
| 1 DU unavailable | Kubernetes DU readiness + F1 loss, OSM ownership | MAJOR | Propose targeted DU recovery; C | CU/DU Ready, F1/NGAP association AND setup, core healthy |
| 2 CU unavailable | CU readiness + F1 and NGAP loss | MAJOR | Propose targeted CU recovery; C | CU/DU Ready, both protocol setup states, core |
| 3 F1 lost | Kernel SCTP + DU/CU state + setup failure evidence | MAJOR | Investigate transport/configuration; C proposal, no automatic restart | F1 association AND F1 Setup |
| 4 NGAP lost | Kernel SCTP + CU/AMF health + NG Setup evidence | MAJOR | Investigate CU-to-AMF path; C proposal | NG association AND NG Setup; persistent core |
| 5 CPU contention | Sustained CPU PSI/trend + measured service degradation | WARNING | Notify; A. Reducing noncritical work remains unvalidated C | PSI trend + control/radio service, no added load |
| 6 Sustained RAN errors | Timestamped application errors + protocol symptoms | MAJOR | Notify/investigate; A. No current error-rate rule | Parser/configuration and protocol recovery |
| 7 AMF unavailable | Authoritative core workload state + reachability | CRITICAL | Notify/escalate; A. Core restart D | All protected core health plus protocol evidence |
| 8 SMF/UPF degraded | Workload and independent SBI/PFCP/user-plane evidence | CRITICAL | Notify/escalate; A. Core mutation D | Relevant SBI/PFCP and measured user plane |
| 9 Registration failures | Native failure/attempt windows + minimum attempts + correlated RAN/core observations | CRITICAL | Notify; A. Candidate metric rule needs semantic review | Registration outcome evidence; not tunnel inference alone |
| 10 PDU failures | Matched session attempt/failure counters + core/RAN state | CRITICAL | Notify; A. Complete paired metric not verified | Session establishment and measured user plane |
| 11 Fog unavailable | Fog readiness + ownership + core/RAN protection | MAJOR | Fog-only recovery proposal; C, currently unvalidated | Fog application response + RAN/core unchanged |
| 12 Fog latency target exceeded | Actual processing histogram/sample + target + load | MAJOR | Notify; A. Histogram absent in live audit | Processing latency under reviewed SLS |
| 13 Node failure | Kubernetes Ready=False + scope and workload impacts | CRITICAL | Inhibit same-node symptom alarms; notify A | Authoritative node and affected workload health |
| 14 OSM operation failed | Authenticated OSM state + NS/operation ownership | MAJOR | Block new operations, notify A | Trustworthy reconciled OSM state, Kubernetes and protocols |
| 15 Monitoring unavailable | Failed/missing/stale query + expected targets | WARNING | Block dependent autonomy, notify A | Fresh explicit measurements; absence never zero |
| 16 Insufficient capacity | Requests/allocatable + scheduling events + pressure | WARNING | Notify A; capacity plan C | Scheduling and protected service evidence |
| 17 Congestion/loss | Measured packet loss + path/protocol symptoms | WARNING | Notify A; no live traffic/firewall manipulation | Measured loss and protected service SLS |
| 18 Protected priority/emergency policy | Explicit protected classification; unknown fails closed | WARNING | Block disruptive changes, operator escalation A | Verified protection state, audit decision; no real emergency call simulated |

Persistence defaults to 60 seconds with distinct fresh observations; observability
and governance alarms can escalate immediately. Clearing requires 60 seconds of
resolved observations. Maximum sample age is 120 seconds; gaps over 90 seconds reset
persistence. Flapping suppresses proposals after four transitions in ten minutes.
Cooldown is 600 seconds and automatic retries are zero. All profiles preserve current
deployment as rollback; executable rollback requires future profile-specific testing.
This table specifies investigation evidence; fields unsupported by an adapter remain
missing and cannot authorize execution.

Action classes: A observes and records a notification/escalation in the journal;
external paging is not configured. B is reserved for explicitly tested non-disruptive
allowlisted operations; **none are registered**. C proposes potentially disruptive
operator-reviewed work. D forbids core terminate/restart, namespace deletion,
catalog changes, RAN termination/switching, CPU quota changes, firewall changes and
traffic manipulation. No class performs a live action in this implementation.

## Approval and emergency stop

Operational writes and sandbox UI execution require an explicitly configured
`L3_OPERATOR_TOKEN` of at least 32 characters. The webhook uses a separate
`L3_WEBHOOK_TOKEN`. Authorization is a bearer header; neither token enters journal
records or browser persistent storage. Individual operator identity / SSO integration
is not implemented; audit records identify the authenticated operator role.

Endpoints:

- `GET /api/assurance`: local journal, capped history, governance and cached telemetry.
- `POST /api/assurance/operator`: control, acknowledgement, bounded maintenance,
  proposal decision. Approval records intent only; production execution stays disabled.
- `POST /api/assurance/webhook`: up to 64 catalog alerts / 128 KiB; authenticated;
  simulation provenance/labels rejected. Unknown catalog names are rejected.
- `POST /api/assurance/simulate`: one of 16 sandbox scenarios; result is SIMULATED;
  temporary database does not enter production journal.

Operator command bodies are JSON, for example
`{"command":"control","mode":"OBSERVE_ONLY","emergency_stop":true}` or
`{"command":"decision","action_id":"...","decision":"CANCELLED"}`.
Maintenance is an explicit service identity and start/end epoch bounded to 24 hours.
Maintenance windows currently cannot be edited/deleted; choose short windows, and
use existing Alertmanager silences separately for notification maintenance.

No native webhook payload proves resource ownership, capacity, protected-state
clearance, F1/NG Setup or core health. Native Alertmanager `startsAt` is stored as
upstream onset; receiver time is notification freshness, not metric sample time.
The local persistence engine needs repeated observations within the 90-second gap;
Alertmanager's default five-minute grouped repeats alone do not meet that contract.
Before live routing, deploy a reviewed read-only event collector/normalizer with
explicit observation timestamps or approve an upstream-persistence adapter. Do not
shorten cluster-wide intervals to work around this gap. An empty or unchanged webhook
batch does not prove health and never clears all incidents.

## Profile transitions and recovery

`transition-matrix.json` enumerates every nonadditive profile pair from the current
registry. All transitions are UNVALIDATED, or UNAVAILABLE when the target is blocked.
No pair is considered hitless or autonomously eligible. A proposal must establish
current NS identity, alternative readiness, core/radio compatibility, UE interruption,
capacity, OSM ownership and rollback before independent operator approval.
`oran-srsran` retains its existing registry block. Experimental Fog/H-C-RAN profiles
are not converted to production-safe recovery paths by being listed in the matrix.

For ambiguous OSM outcomes, preserve the pending operation/recovery journal, stop new
lifecycle requests, reconcile authenticated NS/operation state, inspect Kubernetes
readiness and protocol evidence, and escalate. A COMPLETED operation alone cannot
resolve a protocol incident. Never edit OSM-owned Helm releases directly. Existing
experimental recovery tools are not invoked by assurance.

A future rollout must separately approve: dashboard deployment; least-privilege
read-only collector configuration; specific alerts and receivers; profile-bound
execution permissions; and each tested action/transition. Keep the stopped watcher
stopped. Review baseline, duplicate alerts (including semantic overlaps with default
Kube/Node rules), webhook timestamp/persistence, TLS, operator identity, database
retention, rollback, emergency protection and control-plane scrape failures first.
No action enabling is bundled with approval of a proposed incident.
