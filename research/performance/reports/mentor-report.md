# Mentor research report — performance evaluation foundation

## Project objective and scope

Build reproducible numerical evaluation for the existing Kubernetes/OSM-based
5G orchestrator while preserving the HP F-RAN OAI/Core deployment. This work is
performance-only; Claude P4 owns KDU upgrades, rollback and lifecycle validation.
No lifecycle, traffic, UE activation, fault injection, production modification,
or merge was performed during evaluation. Publication is restricted to the feature branch. Development is isolated on
feature/performance-evaluation from origin/reproducible-installer
 e0602ec8067ac20452064fc728f77996f30a691a.

## Existing architecture and implemented evaluation capabilities

Observed CPU: Intel Core i7-4790, 4 physical cores and 8 logical CPUs.
HP hosts an OAI CU/DU split, fog application and OSM-managed Open5GS, with existing
Prometheus/Grafana, Istio and Layer2/3 code. Current namespace:
id-1b4ff6edc2bb. The current live scenario is F-RAN OAI, not
a simultaneous controlled comparison of C/O/v/H/F-RAN. Reusable benchmark
components now include read-only resource collection, existing bound OSM reader,
public CI step-history collection, strict CSV/JSON records, event-duration,
iperf3/ping import, ground-truth confusion/outcome analysis, independent-trial
statistics and guarded Matplotlib plotting scripts. Synthetic test evidence is
separate from real measurements. Exporter code changes are prepared, not deployed.

## Experimental methodology

Eight protocols A–H are specified in experiments/methodology.md and plan.json,
covering manual/automated deployment, architecture resources, OSM stages, UE NAS
procedures, data-plane traffic, L2 detection, L3 recovery and CI/CD stages. Each
includes variables, controls, equipment, tools, procedure, repetitions, outputs,
errors and limitations. Initial plan:≥5 independent trials per condition;
publication plan:≥20–30 independent blocks plus power/uncertainty justification.
Warmup, traffic, versions, placement and sampling must be controlled. Repeated
scrapes are not independent repetitions. No active protocol was executed here.

## Preliminary verified results

Final passive snapshot: **2026-10-22T06:06:38.486026Z**. This is idle/no-active-UE-pod evidence,
not a loaded RAN benchmark. CPU is core-equivalents, memory is cAdvisor working
set. One core=100% of one CPU, not100% of this four-physical-core/eight-logical-CPU node. Source scrape10s;
CPU/throttling windows 300s. Sidecars are separated from NF/application containers.

| Component | CPU, cores (5min rate) | Working set, MiB | Throttled-period fraction |
|---|---:|---:|---:|
| CU | 0.022938 | 128.461 | 0.0 |
| DU | 0.252342 | 777.852 | UNAVAILABLE |
| FOG | 0.005264 | 16.949 | 0.1574585635359116 |
| Open5GS 13 NF/application containers (includes Mongo/WebUI) | 0.005787 | 525.762 | Not aggregated |
| Open5GS 9 Istio proxy containers | 0.016891 | 366.379 | Not aggregated |


CU/DU/fog had zero cumulative restarts and Ready=true in the sampled inventory;
all13 Open5GS pods were Ready. Core applications have historical restarts, which
are retained in CSV; readiness does not imply no historical faults. DU throttling
was missing/nonfinite and remains null. Fog's throttled-period fraction is not
percentage CPU time lost and does not prove user-plane impairment.

Additional genuine observations:

- Prometheus21/25 targets up. Four scrape warnings remain monitoring availability
  findings; no control-plane outage/recovery inference.
- Live exporter0 parsed lines; this is a genuine counter0, not radio BLER0.
  No active UE pod found; bounded CU/DU samples contained no matched radio stats.
- Existing AMF-target TCP-connect mean over5min: **0.806570ms**, at its separately
  recorded audit timestamp. This is not SBI HTTP or UE-to-UPF latency. A success
  fraction is unavailable because the failure series is absent; no zero fill.
- Exact baseline GitHub Actions run39724140683: **150seconds** for its named
  regression/Helm/package-validation step. N=1, timestamp resolution1s.
  It is not a source-image-build or OSM-publication duration.
- OSM NBI operation timing: **UNAVAILABLE**, default-credential policy rejection
  before network access. No default credential bypass or P4/production config edit.

Two passive resource snapshots were collected, with the **same trial ID**. The
first and final datasets are preserved; their changing values are observational,
not evidence that a code change improved the RAN. N remains one exploratory
condition/block, not two independent controlled experiments. No architecture
comparison, recovery success rate, UE throughput or radio improvement is claimed.

## Collection and exporter engineering overhead

Final passive collection used6 Prometheus GETs,1 logical Kubernetes list attempt,
**3.0525s** elapsed and **0.3957s** CPU (self+reaped child). Maximum sampled kubectl
process-tree RSS **50.340 MiB**; the self ru_maxrss field is a lifetime high-water
value, not simultaneous tree RSS. Kubernetes call counts exclude any implicit
kubectl discovery/cache requests. No ongoing collector or scheduled job was started.

Offline exporter engineering test:100 synthetic32-UE cycles,192 retained metric
series, **0.4811s** wall/**0.4612s** CPU. Direct current-image /proc RSS/HWM was
**21.293 MiB**. Legacy inherited ru_maxrss was584.21MiB and is recorded separately,
not interpreted as current exporter memory. These are code microbenchmark values,
not live exporter overhead or radio performance. Real API/network cost remains
unverified. No watchdog/resource limit was relaxed.

The initial offline microbenchmark exposed a submicrosecond source-timestamp
rounding eviction edge. It was fixed with the documented5s source-clock tolerance,
with a regression test. Its zero-retained-series result is retained privately as an excluded development diagnostic and excluded from conclusions. The
corrected test validates the expected192 series before accepting overhead output.

## Available/missing measurements and monitoring limitations

The54-entry CSV/JSON matrix identifies sources, names, units, methods, timestamps,
cadence and reliability. CPU/memory/readiness/restarts/configured constraints are
measured now. Native Open5GS registration/session family presence is observed,
not converted into transaction-duration evidence. Metrics-server is not installed.
29 dashboard ConfigMaps establish provisioning, not authenticated UI validation.
No active Istio application-request metrics were found. OAI/srsRAN radio output
formats are implementation/version-specific; synthetic parser tests are not proof
of emitted live BLER/MAC data. Missing KPM/radio metrics are not healthy values.

Exporter's live stale selector/UL-SNR setter remain unchanged pending approval.
Candidate code adds bounded architecture-aware discovery, reconnection, collection
health, timestamp/TTL handling and native parser separation. No CU/DU restart,
log-level change, metrics flag or srsRAN remote-control subscription was performed.

## Current Evidence vs Research Claims

| Claim | Classification | Evidence boundary |
|---|---|---|
| Current HP F-RAN/Core resources can be observed passively | Demonstrated | Two snapshots, real source timestamps, no scenario switching |
| CU/DU/fog/Core pods are Ready in the inspected snapshot | Demonstrated | Kubernetes status only, not full protocol/user-plane acceptance |
| Existing exporter supplies live radio statistics | Not yet validated | Wrong selector, parsed0, no matching bounded sample/active UE pod |
| Candidate handles discovery errors, replacement and stale UE samples | Partially demonstrated | Offline synthetic tests; production activation and real image format unverified |
| Native Open5GS metrics are available | Partially demonstrated | Active family presence; per-transaction timing not collected |
| AMF SBI transaction performance is measured | Not yet validated | Existing data measures TCP connect only |
| CI validation step duration is known | Demonstrated | One150s historical step at exact baseline; no distribution claim |
| One architecture outperforms another | Not yet validated | No matched architecture trials |
| Automated deployment is faster than manual | Not yet validated | No approved matched deployment trials |
| L2/L3 detection/recovery latency or success is quantified | Not yet validated | No independent onset/restoration ground truth or recovery execution |
| UE registration/PDU/throughput/loss/jitter are quantified | Not yet validated | No active UE/approved traffic test |
| Production was not changed by this task | Demonstrated within inspected scope | Read-only calls;48 workload/ConfigMap specs matched before/after; unchanged live exporter hash; isolated source edits |

## Validation and recommended next steps

43 new offline tests pass;32 existing Kubernetes/monitoring regressions pass.
No full-suite count is claimed. Syntax/manifests/evidence digests and plot refusal
are checked separately in validation.md. No comparison charts were generated:
there are fewer than5 independent trials and Matplotlib is absent on this host.

1. Review/approve exporter-only activation and independently establish the actual
   DU log/metrics format under an approved UE procedure; preserve rollback bytes.
2. Supply an approved non-default scoped read-only OSM configuration, without
   altering P4 or production defaults; collect existing operation evidence.
3. Use A–H protocols with Claude P4 as lifecycle owner. Obtain separate approvals
   for deployment, UE activation, traffic or failure/recovery experiments.
4. Run controlled independent trials, retain failures/unknown outcomes and clock
   uncertainty, then generate descriptive graphs on an approved analysis machine.
5. Review source completeness, units and control variables before any performance,
   availability or service-continuity claim to the mentor.

The final Pod Ready condition observations are also normalized into16 standard
CSV/JSON records under results/live-2026-10-22/pod-readiness. Container readiness
is retained separately and never substituted for a missing Pod Ready condition.
Final descriptive statistics are in final-statistics.json and retain per-target
job/instance dimensions. These do not qualify an architecture comparison.
