# Experimental methodology and approval boundaries
All live deployment, UE activation, traffic, fault injection and recovery require **separate explicit approval**. These tools execute none of them. Claude P4 owns lifecycle/rollback validation. No automatic experiment runner selects scenarios.
Common environment: record hardware/CPU topology, OS/kernel, Kubernetes/OSM/Helm, image digest/version, git revisions/dirty state, namespace/ownership, radio/traffic/subscriber setup (without secrets), node placement, time synchronization, background CPU/PSI, scrape cadence and data gaps. Freeze configuration per trial. Randomize mode/scenario order where feasible.
At least five **independent trials**, not five scrapes. Use trial-block means for resource data, report raw observations separately. Report mean, median, sample SD, min/max, P95, N, units, failures, exclusions and censored outcomes. Current summary uses seeded1000-resample exploratory bootstrap CI of trial means for N≥5; this is not automatic publication qualification. For publication use ≥20–30 independent blocks, pre-study power/effect-size analysis, justified bootstrap/t/Welch methods and account for autocorrelation/multiple comparisons. Empirical P95 with N=5 is unstable; one observation has no SD/CI.

## Experiment A — Manual versus automated deployment
- **Independent variables:** Deployment mode; same immutable package and initial state
- **Dependent variables:** Service-verified total duration; operator-action count
- **Controlled variables:** Verified package pre-staged for both; exclude preparation equally; explicit approval boundary; same NS/namespace capacity
- **Equipment and environment:** Approved isolated P4 deployment workflow; synchronized observer clock; existing Core unchanged
- **Measurement tools:** Approved isolated P4 deployment workflow; synchronized observer clock; existing Core unchanged
- **Experimental procedure:** Record first approved operator action or automated-request acceptance, then independent Kubernetes+protocol readiness; retain failed/censored trials, count actions from an observer journal
- **Number of repetitions:** Five independent deployments per mode, randomized order; ≥20 per mode for stronger inference
- **Expected outputs:** Duration/action JSON/CSV, complete outcome counts and trial-mean summaries
- **Measurement errors:** Different operators, cache warming, image pulls, asynchronous readiness, human reaction
- **Limitations:** No automated deployment implementation/certification is asserted; P4 owns lifecycle execution
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment B — Resource use across architectures
- **Independent variables:** Scenario, fixed traffic profile
- **Dependent variables:** CU/DU/fog/Core CPU cores, working-set bytes, throttling fraction, restarts
- **Controlled variables:** Same hardware/kernel/CNI/images where applicable; same bandwidth/UE count/traffic; fixed limits; no unrelated jobs
- **Equipment and environment:** Existing Prometheus/cAdvisor10s; passive collector; fixed per-architecture environment
- **Measurement tools:** Existing Prometheus/cAdvisor10s; passive collector; fixed per-architecture environment
- **Experimental procedure:** After approved scenario setup, warm up10min; sample10min blocks with read-only collection at30s; summarize each independent trial block, not every scrape
- **Number of repetitions:** Five blocks per architecture initially; ≥20 randomized independent blocks with power analysis for publication
- **Expected outputs:** Per-container samples; sidecars separate; trial-level stats and controlled comparisons only
- **Measurement errors:** Autocorrelation, scheduling, thermal throttling, shared-host load, rate extrapolation, scrape gaps
- **Limitations:** Current evidence is only F-RAN OAI idle/no-active-UE snapshot; no architecture ranking
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment C — OSM stage durations
- **Independent variables:** Package/scenario; cache state
- **Dependent variables:** Onboarding+readback, instantiation, termination, service readiness
- **Controlled variables:** Disposable project, unique immutable package IDs, same OSM/VIM cluster, explicit ownership
- **Equipment and environment:** Existing approved P4/NBI executor supplies timestamps; benchmark tools only read/import
- **Measurement tools:** Existing approved P4/NBI executor supplies timestamps; benchmark tools only read/import
- **Experimental procedure:** Capture publication start/readback, instantiate submit/terminal state, independent readiness; terminate only separately approved disposable NS; never use protected NS
- **Number of repetitions:** Five independent cycles per scenario; ≥20 with operation failure/censor reporting
- **Expected outputs:** Operation IDs and start/end pairs; stage duration/outcome tables
- **Measurement errors:** Client queue time, OSM asynchronous states, clocks, cached chart/image pulls
- **Limitations:** OSM COMPLETED is not Kubernetes/application readiness; current reader is credential-policy blocked
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment D — UE registration and PDU timing
- **Independent variables:** RAN/UE software and procedure condition
- **Dependent variables:** NAS registration Request->Accept; PDU Request->Accept durations
- **Controlled variables:** Same RFsim configuration, distinct controlled subscribers, same PLMN/TAC/slices/DNN, fixed starting state
- **Equipment and environment:** Approved UE and Core protocol traces, correlated transaction/opaque trial IDs, synchronized clocks
- **Measurement tools:** Approved UE and Core protocol traces, correlated transaction/opaque trial IDs, synchronized clocks
- **Experimental procedure:** Approve UE activation; separately mark registration and PDU transactions; redact subscriber secrets; keep retries and failed outcomes; separate initial RF/RRC time
- **Number of repetitions:** Five independent procedure trials per condition; ≥20–30 with timeouts and CI
- **Expected outputs:** Per-transaction durations with clock uncertainty and success/censor counts
- **Measurement errors:** Clock skew, UE retries, cached authentication, RF attachment before NAS, timeout censoring
- **Limitations:** No active UE found, no current registration/PDU durations; do not derive from aggregate counters
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment E — UE data-plane latency/goodput/loss/jitter
- **Independent variables:** UL/DL direction, offered load, packet size, endpoint
- **Dependent variables:** RTT, receiver goodput, UDP loss/jitter
- **Controlled variables:** Same UE tunnel route, UPF/DN responder, radio, server CPU, test duration and load cap
- **Equipment and environment:** Approved UE ping and iperf3 receiver JSON; no tools installed here
- **Measurement tools:** Approved UE ping and iperf3 receiver JSON; no tools installed here
- **Experimental procedure:** After verified PDU, verify route/source interface; run approved bounded traffic independently UL/DL; import receiver summaries; include all trials and explicit errors
- **Number of repetitions:** Five30s trials per direction/condition; ≥20 blocks for tails, more packets for P95
- **Expected outputs:** Ping summary and iperf3 JSON + scenario/version metadata; actual units retained
- **Measurement errors:** TCP slow start, server bottlenecks, clocks, RTT vs one-way delay, radio scheduler, UDP load
- **Limitations:** TCP handshake-to-AMF is not this experiment; no traffic generated here
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment F — Layer2 degradation/failure detection
- **Independent variables:** Approved isolated degradation or replay; threshold/poll settings
- **Dependent variables:** Ground-truth onset->alarm delay; FPR on negative windows
- **Controlled variables:** Fixed sampling/evaluation intervals and thresholds; independent UE/service ground truth; same traffic
- **Equipment and environment:** L2 metric/alert history + independent onset marker; offline replay tests labeled synthetic
- **Measurement tools:** L2 metric/alert history + independent onset marker; offline replay tests labeled synthetic
- **Experimental procedure:** Use offline replay first; live fault/load injection only after separate approval in disposable environment; record onset and first qualifying alarm; evaluate equal-duration known-normal windows
- **Number of repetitions:** Five positive and five negative windows initially; ≥20 each with confidence intervals
- **Expected outputs:** Detection intervals, TP/FP/TN/FN, FPR=FP/(FP+TN); undefined denominator is null
- **Measurement errors:** Scrape/evaluation/cache delay, observer clock, uncertain onset, absence of telemetry
- **Limitations:** No fault injected; missing radio metrics cannot be treated as healthy or used to prove false-positive rates
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment G — Layer3 decision and recovery
- **Independent variables:** Proposal/recovery policy on approved disposable target
- **Dependent variables:** Alarm->proposal time; approval->verified restoration; downtime; success rate
- **Controlled variables:** Same initial service state; ownership/locks enforced; protected services excluded; P4 operator owns recovery
- **Equipment and environment:** L3 audit/incident records + P4 outcomes + independent service probe
- **Measurement tools:** L3 audit/incident records + P4 outcomes + independent service probe
- **Experimental procedure:** Observe proposals without executor; for a separately approved recovery test record approval, action boundaries and independent restored service; preserve UNKNOWN outcomes, never retry from benchmark tool
- **Number of repetitions:** Five trials per failure mode; ≥20 with success uncertainty and all failures
- **Expected outputs:** Correlated duration/outcome records; unknown outcomes reported separately; no survivor-only averages
- **Measurement errors:** Operator delay, lost requests, partial readiness, asynchronous recovery, observer sampling
- **Limitations:** No production corrective action/recovery executed; simulations never prove recovery success
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

## Experiment H — CI/build/release-verification performance
- **Independent variables:** Exact commit, workflow stage, cache condition
- **Dependent variables:** Named validation/build/package/readback stage durations
- **Controlled variables:** Same runner class/toolchain/artifact bytes/cache policy; isolate shared-host interference
- **Equipment and environment:** GitHub public read API + approved stage-boundary logs
- **Measurement tools:** GitHub public read API + approved stage-boundary logs
- **Experimental procedure:** Read completed exact-SHA runs; separate queue/job/step time; record independent build/package/readback markers if existing approved jobs emit them; never dispatch jobs from this collector
- **Number of repetitions:** Five completed comparable runs per condition; ≥20 with cache stratification
- **Expected outputs:** Historical CI records and stage-specific samples; censored/failed stages retained
- **Measurement errors:** Runner variability, queue latency, coarse API timestamps, combined step scope
- **Limitations:** Only one150s validation step measured now; no separate image-build/catalog-publication timing
- **Execution status:** NOT_EXECUTED; existing passive B/H observations only

FPR is FP/(FP+TN), not FP/(TP+FP). The latter is false discovery fraction. Recovery/deployment success includes all eligible attempts; unknown outcomes are not silently successful or failed. `benchmarks.reliability` returns null rate and explicit bounds when outcomes are unknown. Service downtime needs independent service probes; Ready/SCTP alone are not user-plane success.
