# Performance evaluation — isolated, read-only by default

Branch: feature/performance-evaluation. Base: origin/reproducible-installer
`e0602ec8067ac20452064fc728f77996f30a691a`. Publication is restricted to this feature branch.
Claude P4: `/home/rclab/5g-kubernetes-integration`, branch
`p4/kdu-upgrade-experiment`: no edits, merges, resets or lifecycle tests by this work.
The production checkout `/home/rclab/5g-kubernetes` was not edited. Worktree creation
and fetching the requested baseline update shared Git metadata only.

Read [the mentor report](reports/mentor-report.md), [monitoring audit](reports/monitoring-audit.md),
[availability matrix CSV](reports/metric-availability.csv), [JSON matrix](reports/metric-availability.json),
[experiment methodology](experiments/methodology.md) and [collection instructions](experiments/collection.md).

## Layout and executable commands

Run from the **performance checkout**, with `PYTHONDONTWRITEBYTECODE=1`.
All framework/parser/exporter tests use stdlib; nothing was installed.

```sh
python3 -m unittest discover -s research/performance/tests -v
python3 -m research.performance.collectors.passive --help
python3 -m research.performance.collectors.radio --help
python3 -m research.performance.collectors.osm_records --help
python3 -m research.performance.collectors.github_ci --help
python3 -m research.performance.parsers.events --help
python3 -m research.performance.parsers.traffic --help
python3 -m research.performance.benchmarks.summarize --help
python3 -m research.performance.benchmarks.plot --help
```

- `collectors/`: one-shot Prometheus/Kubernetes, existing read-only OSM adapter,
  public GitHub CI step timestamps; a future radio reader that requires source
  health/metric timestamps and observed pod UID ownership. No automatic scheduler or workload lifecycle.
- `parsers/`: already-recorded event pairs, receiver iperf3 JSON and ping summaries.
  Importers never send traffic, exec pods, deploy, terminate or recover services.
- `benchmarks/`: independent-trial statistics, real-data-only plotting,
  ground-truth confusion/outcome calculations and a separate offline engineering
  microbenchmark. No network fault injector or production deployment engine.
- `results/record.schema.json` + `schema.py`: version1 records, explicit statuses,
  provenance, source timestamps, unit and scenario. JSON/CSV exports have SHA256
  manifests. Hashes detect accidental edits, not malicious re-signing: source
  provenance declarations still require researcher/operator review.
- `results/live-2026-10-22/`: genuine passive evidence and historical CI data only.
- `reports/`: availability/audit/research/statistics and explicitly synthetic
  engineering test results. Synthetic fixtures never become research comparisons.

## Status and evidence rules

`MEASURED`, `AVAILABLE_BUT_NOT_COLLECTED`, `REQUIRES_ACTIVE_UE`,
`REQUIRES_CONTROLLED_TEST`, `UNAVAILABLE`. Unmeasured value is JSON null / empty
CSV cell, never invented0. A genuine measured counter0 is retained as0.

Freshness is LIVE/STALE/UNAVAILABLE **at observation time**. Historical duration
records remain valid historical observations, not assertions of current health.
LIVE/RECORDED_LIVE/SYNTHETIC_TEST provenance is separate. The plot/statistics path
excludes synthetic/unavailable/stale observations. Files cannot authenticate
someone's manually claimed LIVE provenance: verify references and archive hashes.

The two passive resource snapshots share the same exploratory trial ID. They
are not two independent trials. Five separate controlled trial blocks are the
minimum exploratory plan; no architecture comparison has been executed.
CPU is cores (1 core=100% of one logical CPU), not percentage of this four-core
host. Memory is cAdvisor working set, not RSS. CFS throttling is fraction of
scheduler periods throttled, not percent of CPU time or packet loss.

## Limits

Passive collection: one Kubernetes pod-list attempt through the existing
`assurance.kubernetes.KubernetesReader`, 8s timeout,2MiB output,256MiB sampled
child-tree RSS; no RLIMIT_AS cap. Prometheus: at most8 GET attempts,40s HTTP
collection deadline,2MiB response each,2s socket/6s per request body deadline,
no redirects/retries,3s query timeout. Current collection uses6 HTTP requests.
TLS certificate verification is enabled; current HP endpoint is internal HTTP.
Use approved TLS/auth integration before remote exposure; do not put tokens in URLs.
Kubernetes attempt count is logical list calls, not a packet-level count of
kubectl's discovery/cache HTTP requests. CPU includes reaped child CPU; child
RSS comes from sampled process-tree monitoring. `ru_maxrss` fields are lifetime
high-water values and must not be summed or mistaken for current process memory.

The RAN exporter candidate is separate: namespace-only GETs,128-pod inventory,
8 selected source containers by default,200 lines/64KiB per log poll,6s read
budget,15s normal polling,30–60s error backoff,120s per-metric TTL,2048 series,
512 remembered line hashes/source. Clock tolerance is5s; future samples beyond5s
are rejected. Log-window saturation is exported and marks collection incomplete.
No raw logs/subscriber keys persist. HTTP exposition has at most4 request threads.
Exporter activation and any ConfigMap/Deployment/RBAC change require separate approval.

Use reports/final-statistics.json for current analysis. The corrected summary
preserves job/instance/container dimensions. Superseded exploratory diagnostics
remain local and are excluded from publication. No architecture comparison
qualification follows merely from meeting the minimum trial count.

See [publication review](reports/publication-review.md) for evidence sanitization,
focused regression results and production-protection verification.
