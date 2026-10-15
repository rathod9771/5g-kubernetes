# Repository and installed-capability audit

The audit is dated **14 October 2026 (Asia/Kolkata)**. The exact UTC observation
instant and sanitized responses are in `live-inventory.json`. Installed resources,
API reachability and profile validation are different forms of evidence.

| Capability | Status | Evidence / limitation |
|---|---|---|
| Kubernetes state | SUPPORTED | One Ready node; API read succeeded; Memory/Disk/PID pressure false; NetworkUnavailable false |
| CU, DU, fog workloads | SUPPORTED | Active `fran-oai` pods Ready in the bound workload namespace |
| Persistent Open5GS workloads | SUPPORTED | 13 Open5GS pods Ready; readiness is not proof of every core protocol |
| Prometheus | SUPPORTED | Query and target APIs responded; native kube-state/node/Open5GS/fog families observed |
| Monitoring pipeline | PARTIALLY SUPPORTED | kube-controller-manager, kube-etcd, kube-proxy, kube-scheduler scrape targets down |
| Alertmanager | SUPPORTED | Monitoring Alertmanager readiness HTTP 200; separate OSM Alertmanager also deployed |
| Rancher | PARTIALLY SUPPORTED | Rancher pods Ready; existing dashboard URL/embed integration; no authenticated Rancher API check or Rancher Monitoring installation proved |
| Grafana | PARTIALLY SUPPORTED | Monitoring and OSM Grafana pods Ready; existing dashboard opens Grafana; datasource/dashboard content not authenticated/verified |
| OSM v19 integration | SUPPORTED | Existing verified-TLS client authenticated; both protected NS READY/IDLE; recorded lifecycle operations COMPLETED |
| Profile-specific OSM healing/scaling | NOT IMPLEMENTED | Generic OSM documentation does not establish safe KDU healing/scaling on this installed profile |
| Open5GS PM | PARTIALLY SUPPORTED | AMF registrations, SMF session creation/count, UPF sessions/N3 packet counts present; semantic validation and failure/attempt matching still required |
| OAI UE BLER / throughput | UNAVAILABLE | Log exporter is up but UE BLER/throughput samples absent in audited query |
| F1-C / NGAP | PARTIALLY SUPPORTED | Existing kernel/log evidence paths; transport associations separate from Setup, UE, PDU and user-plane state |
| srsRAN active execution | UNAVAILABLE | Repository profiles and protocol parser present; active profile is OAI, not srsRAN |
| FlexRIC / KPM | PARTIALLY SUPPORTED | FlexRIC pods Running; CU/DU KPM acceptance jobs Failed; no KPM alert enabled |
| Fog DNS evidence | SUPPORTED | Cached discovery age/stale/refresh counters present |
| Fog processing latency | UNAVAILABLE | Source instrumentation exists in local modifications; latency histogram families absent in live audit |
| Host CPU PSI some | SUPPORTED | `node_pressure_cpu_waiting_seconds_total` present |
| Host CPU PSI full | UNAVAILABLE | `node_pressure_cpu_stalled_seconds_total` absent |
| Emergency IMS/QoS end-to-end | UNAVAILABLE | No verified IMS emergency chain or SMF/PCF priority enforcement evidence |
| Autonomous remediation | NOT IMPLEMENTED | Production executor disabled, no validated allowlist, LIVE_AUTO rejected |
| New assurance UI rollout | NOT IMPLEMENTED | Source implemented/tested offline; running backend not restarted |

Repository areas inspected: legacy watcher/service/README; full scenario registry;
Flask backend, UI, OSM client, monitoring and embed policy; runtime configuration,
state schema, safety locks, catalog/package provenance, experimental recovery and
journal; RAN protocol health; deployment scripts and installers; monitoring values,
exporters and PodMonitors; fog chart/latency instrumentation; existing documentation
and regression tests; tracked file inventory and recent history. Large package
hash manifests, archives, virtualenv/vendor files and binary assets were inventoried,
not exhaustively reverse-engineered.

The legacy watcher queries `max(oai_gnb_ue_dl_bler_ratio) or
max(oai_gnb_ue_ul_bler_ratio)` and calls `/api/deploy` after a single threshold
observation. It has a process-local cooldown and append-only JSON actions log.
The existing backend owns runtime context discovery, deployment readiness, an
exclusive `.lifecycle.lock`, pending-instance reconciliation and recovery journals.
These features were reused rather than replaced. The new Layer 3 source does not
call `/api/deploy`, terminate an NS, edit Helm releases or alter ownership.

All original uncommitted modifications and untracked resources were retained. Only
small additive backend registration and the Layer 3 panel implementation were edited
in the already-modified backend/UI. The legacy `/api/layer3-actions` API remains.
