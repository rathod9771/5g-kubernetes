# L3-2 read-only investigation: 14 October 2026, Asia/Kolkata

## Four control-plane scrape failures

| Job | Configured scrape endpoint | Actual listener | Confirmed cause |
|---|---|---|---|
| kube-controller-manager | `https://172.30.18.32:10257/metrics` | `127.0.0.1:10257` | Static pod `--bind-address=127.0.0.1`; node-IP connection refused |
| kube-scheduler | `https://172.30.18.32:10259/metrics` | `127.0.0.1:10259` | Static pod `--bind-address=127.0.0.1`; node-IP connection refused |
| kube-proxy | `http://172.30.18.32:10249/metrics` | `127.0.0.1:10249` | Effective loopback listener; ConfigMap metricsBindAddress empty/default; node-IP refused |
| kube-etcd | `http://172.30.18.32:2381/metrics` | `127.0.0.1:2381` | `--listen-metrics-urls=http://127.0.0.1:2381`; node-IP refused |

Evidence: Prometheus active-target `lastError`, ServiceMonitor schemes/ports, pod
command flags, kube-proxy configuration and host `ss -lnt`. The failures occur before
TLS or authorization negotiation. Current controller/scheduler monitors already
configure bearer references and HTTPS with insecureSkipVerify; this was inspected,
not changed. Those settings cannot correct a listener-address mismatch.

All four components are Running. Missing internal metrics limit controller work-queue,
scheduling, proxy and etcd performance/leader/quorum evidence. Kubernetes API readiness,
kube-state-metrics, node PSI and Open5GS/fog exporters remain available. An unreachable
etcd metrics endpoint does not prove a failed etcd member or lost quorum. The existing
Alertmanager groups include TargetDown and component-unreachable alarms, plus
`etcdMembersDown` and `etcdInsufficientMembers`; treat those quorum claims as unconfirmed
until authoritative evidence establishes them. No remediation is inferred.

No monitoring configuration or listener was changed. Resolution requires a separate
review: a tightly scoped host-local authenticated metrics proxy or approved secure
listener exposure, along with monitoring auth/TLS/network policy. Do not merely bind
all metrics endpoints to every interface. Moving pod scrape addresses to 127.0.0.1
would address the Prometheus pod itself, not these host listeners. Listener/proxy
changes and any required control-plane restart are outside this phase's approval.
Observe-only assurance can report the four gaps before those repairs.

## KPM: historical success versus current unavailability

Both acceptance pods retain Pod phase Failed with `reason=DeadlineExceeded` and
`activeDeadlineSeconds=90`. They ran on **7 October 2026**, before the current
protected Fog-RAN CU/DU containers started on **13 October**.

| Test | Retained application evidence | Termination / interpretation |
|---|---|---|
| flexric-cu-kpm-acceptance | Connected E2 nodes=2; 26 KPM indication latency messages; MONITOR_FINISHED | Container exit 0; shell uses timeout 30s, echo and sleep 45s, which masks the inner monitor exit; Pod deadline failure is a separate lifecycle classification |
| flexric-du-kpm-acceptance | Connected E2 nodes=1; 57 KPM indication latency messages | Timeout 60s, exit 124; Pod deadline classification retained |

The earlier conclusion based only on Failed pod phase was incomplete. The logs prove
historical reception of KPM indications, not a complete calibrated cell/UE measurement
or conformance test. No compatibility/assertion failure appears in the retained filtered
logs. Exact pod-teardown/deadline timing cannot be reconstructed from the retained
application logs alone. The demonstrated failure is in bounded test completion/status
interpretation, rather than demonstrated E2/KPM protocol incompatibility.

Current read-only evidence:

- Both FlexRIC deployments are Running/Ready: standard `oai-flexric:1.0` and the
  separate candidate image used by the historical monitor.
- Current `fran-oai-cu-config` / `fran-oai-du-config` have no `e2_agent` block; the
  matching pod config mounts are `/opt/oai-gnb/etc`, and no E2/RIC env override appears.
- CU, DU and both RIC `/proc/net/sctp/assocs` tables contain no established ST=3 E2
  association on port 36421. CU F1/NGAP and DU F1 transport remain established.
- Candidate RIC retained logs accepted KPM function ID 2 and RC ID 3 on earlier
  setup requests, then recorded SCTP shutdown/removal. Those are historical assertions.
- No current KPM/E2 measurement exporter was verified. No xApp/subscription was started.

Classification: current protected profile has E2 disabled by configuration; historical
KPM reception was supported; live KPM assurance is UNAVAILABLE. There is no evidence
justifying a current compatibility failure diagnosis. Current image-version/service-model
interoperability remains untested because E2 is inactive. No CU/DU, E2 setting, FlexRIC
resource or monitoring subscription was changed.
