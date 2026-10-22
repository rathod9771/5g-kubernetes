# Exact collection and import instructions

## Read-only resource collection (no deployment approval implied)

Run from /path/to/performance-checkout, not Claude's integration checkout.
Choose a **new** private output directory each time; nothing overwrites results.
The supplied kubeconfig path is passed to the existing allowlisted Kubernetes
reader; its credential contents must never be printed or copied into reports.

```sh
export PYTHONDONTWRITEBYTECODE=1
python3 -m research.performance.collectors.passive \
  --kubeconfig /approved/private/kubeconfig.yaml \
  --namespace "$APPROVED_NAMESPACE" \
  --prometheus-url "$APPROVED_PROMETHEUS_URL" \
  --scenario fran-oai --implementation oai \
  --trial-id EXPERIMENT-B-APPROVED-BLOCK-ID --output /tmp/performance-new-snapshot
```

This is a one-shot read. It neither schedules continuous monitoring nor creates
an independent experimental trial merely because a new snapshot was collected.
Use one trial ID per controlled block, unique output directories per sample.
For an already-authorized stable deployment, collect at30s intervals over planned
blocks using an operator-controlled bounded session; stop if production health or
resource budget deteriorates. Do not add cron, timers or a background service.
No resource usage **during deployment** is inferred without known deployment bounds.

## OSM historical reads — currently BLOCKED by credential policy

```sh
python3 -m research.performance.collectors.osm_records \
  --snapshot /approved/private/schema-compatible-readonly-config.json \
  --root /path/to/production-checkout \
  --output /tmp/performance-new-osm-history
```

A non-default scoped read-only identity, trusted CA, configured project and bound
active/core IDs are required. Current production snapshot is rejected before any
NBI request. Do not modify production credentials, bypass defaults checks, or
change Claude P4 files. The reused adapter permits authentication once and at
most3 bound GETs, with4s timeouts/2MiB responses and no retries. It does not list
all history or infer onboarding stage time. Operation COMPLETED is not service
readiness. Failed/unfinished/absent boundaries remain explicit.

## Historical CI read — no workflow dispatch

```sh
python3 -m research.performance.collectors.github_ci \
  --repo rathod9771/5g-kubernetes --branch reproducible-installer \
  --sha e0602ec8067ac20452064fc728f77996f30a691a \
  --output /tmp/performance-new-ci-history
```

Two public GETs maximum,10 recent runs/10 jobs,6s socket timeout,1MiB response.
No token is used or logged. The exact-SHA regression step is reported separately
from runner queue time, source/image build, release verification and deployment.
Do not dispatch a workflow to generate a benchmark without separate approval.

## Recorded event/traffic import — safe offline operations

```sh
python3 -m research.performance.parsers.events /private/approved-events.json \
  --output /tmp/performance-new-event-import
python3 -m research.performance.parsers.traffic --format iperf3 \
  --input /private/receiver-result.json --metadata /private/trial-metadata.json \
  --output /tmp/performance-new-traffic-import
# Use --format ping for a recorded Linux ping summary.
```

Event JSON: schema_version1, scenario, ran_implementation, component,
experiment_id, trial_id, software_version, provenance RECORDED_LIVE, clock
{same_clock:true} or {synchronized:true,max_error_seconds:<verified error>},
metrics (names in parsers/events.py PAIRS), events array. Each event needs name,
timezone-qualified timestamp (or epoch seconds), operation_id, evidence_ref.
Correlate both boundaries to the same transaction/operation; duplicate boundaries
are rejected, missing ends are null. Do not invent timestamps or set same_clock
merely to bypass validation. Clock uncertainty must accompany cross-host results.
Manual-action metric additionally needs observed_at, operator_actions and an
independent operator_action_observer_ref. Missing action records are not zero.

Traffic metadata: scenario, implementation, component (e.g. ue-to-upf or ue-to-dn),
experiment, trial, version, at (actual observation epoch), provenance RECORDED_LIVE,
labels {evidence_ref:<real archived receiver evidence>}. Never include subscriber
keys, IMSI, passwords, tokens or raw kubeconfig. iperf input must have protocol,
reverse direction and receiver summary with sender:false; failed output is rejected.
Native iperf bitrate is bit/s, UDP jitter ms, loss ratio. Ping is RTT, not one-way
latency; reported mdev is not renamed into sample standard deviation.

## Active traffic procedures — NOT AUTHORIZED BY THIS TASK

Only after separate approval: verify a real PDU session, UE tunnel route and an
approved ICMP/test server endpoint at UPF/DN. A control-plane AMF address is not a
substitute. Example commands for the administrator in the approved UE context:

```sh
# Active tests: do not run until explicitly approved.
ping -I "$APPROVED_UE_INTERFACE" -c 20 -i 0.2 "$APPROVED_TEST_ENDPOINT" > "$PRIVATE_PING_OUTPUT"
iperf3 -c "$APPROVED_DN_TEST_SERVER" -t 30 -J > "$PRIVATE_UL_OUTPUT"
iperf3 -c "$APPROVED_DN_TEST_SERVER" -t 30 -R -J > "$PRIVATE_DL_OUTPUT"
# UDP load must have an explicitly approved bitrate; capture receiver/server JSON.
```

No server, UE, package, NS, fault or recovery is created by these scripts. Server
setup, traffic caps, subscriber use and lifecycle cleanup need their own approval.
P4 deployment/upgrade/rollback commands are intentionally absent. The benchmark
observer receives P4's approved boundary/outcome evidence without modifying it.

## Analysis and graphs

```sh
python3 -m research.performance.benchmarks.summarize \
  /private/trial-one/measurements.json /private/trial-two/measurements.json \
  --output /tmp/performance-new-summary.json
python3 -m research.performance.benchmarks.plot /private/combined-measurements.json \
  --metric cpu_usage --output /tmp/performance-new-plot.png
```

Summary reports equal-weight independent trial means, not a pooled scrape-level
confidence interval. Containers/software versions/units are separate groups.
P95 is of trial means; it is **not** a pooled packet-latency tail. Keep packet-
level traces separately for properly estimated tails. Plotting refuses groups
with <5 real trials and all SYNTHETIC_TEST/stale/unavailable data. Matplotlib is
not installed on this host and was not installed by this task; render on an
approved analysis environment. No comparison chart was fabricated/generated.
A plot is descriptive and never establishes causation without the controlled
methodology and a reviewed environment/traffic contract.

## Exporter candidate activation — separate approval required

Do not apply the changed manifest, Role or ConfigMap now. Future activation must
review exact files, compare current ConfigMap/Deployment, preserve rollback bytes,
verify namespace-only RBAC and API budgets, identify the approved Python image,
and test synthetic logs in isolation before a dashboard/Prometheus scrape check.
An approved real UE/compatible recorded format is a separate gate for radio claims.
No RAN restart, radio-stat enabling flag, remote-control subscription or P4 change
is part of the exporter fix. Confirm freshness TTL, source health, API reconnect,
pod replacement and absence of stale UE labels before acceptance. Rollback is
exporter-only and separately authorized; never restart CU/DU/Core to force metrics.

## Future passive radio read — no exporter or UE activation

Once separately approved exporter/format validation exists, collect already-present
radio series with the same explicit namespace/scenario identity:

```sh
python3 -m research.performance.collectors.radio \
  --kubeconfig /approved/private/kubeconfig.yaml \
  --namespace "$APPROVED_NAMESPACE" \
  --prometheus-url "$APPROVED_PROMETHEUS_URL" --trial-id APPROVED-TRIAL-ID \
  --scenario fran-oai --implementation oai --output /tmp/performance-new-radio-read
```

This reader is offline-tested, not live-radio verified. It performs only3
Prometheus queries and1 bounded pod inventory; it requires collection-health,
original per-metric timestamps and current canonical-app pod UID binding.
Legacy samples without those gates remain unavailable. No metric/logging setting,
UE, remote-control subscription or RAN deployment is activated by this command.
