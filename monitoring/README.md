# Layer 2 — Real-time network intelligence (Prometheus + Grafana)

## What's here
- `kube-prometheus-stack-values.yaml` — the exact Helm values used to install
  the monitoring stack (grafana persistence, probe delays, retention, and the
  scrapeClasses fallback fix below)
- `open5gs-podmonitor.yaml` — PodMonitor that tells Prometheus to scrape the
  AMF, SMF, UPF, and PCF pods on their native `metrics` port (9090)
- `grafana-ingress.yaml` — Ingress exposing Grafana at
  `grafana-metrics.172.30.18.32.nip.io`

## How this was built (reproduce from scratch)

1. Install cert-manager, ingress-nginx and Longhorn on a fresh cluster first
   (see main repo docs) — these are prerequisites for OSM itself, not just
   monitoring.

2. Add the repo and install:

3. Apply the PodMonitor and Ingress:
4. Enable open5gs's own native Prometheus metrics (already committed in
   `osm-packages/open5gs_knf/helm-chart-v3s/open5gs/values.yaml` —
   `metrics.enabled: true` for amf/smf/upf/pcf). This ships in the chart
   already; it was just off by default. No custom exporter needed — open5gs
   exposes real 3GPP TS 28.552-style metrics (`fivegs_amffunction_*`,
   `fivegs_ep_n3_gtp_*`, etc.) on port 9090 once enabled.

## Gotchas hit building this (so you don't have to rediscover them)

- **Grafana without persistence re-runs its full DB migration on every pod
  restart**, which is slow enough that the default liveness/readiness probes
  kill it mid-startup, looping forever. Fixed by enabling
  `grafana.persistence` (Longhorn-backed) and raising the probe
  `initialDelaySeconds`.
- **open5gs's metrics port is declared in the chart's Service/Pod spec but not
  actually enabled by default** — you must set `metrics.enabled: true` per
  component (amf/smf/upf/pcf) in the parent chart's values.yaml.
- **open5gs's metrics server binds to the pod's own IP, not localhost** —
  `kubectl port-forward` (which connects via localhost inside the pod) can't
  reach it; test with a temporary curl pod hitting the pod IP directly
  instead.
- **PodMonitor label selectors**: the AMF/SMF/UPF/PCF pods use
  `app.kubernetes.io/name: amf|smf|upf|pcf` directly — there's no shared
  `open5gs` name + separate `component` label on these specific pods (that
  pattern only exists on the `populate` job pod).
- **"non-compliant scrape target sending blank Content-Type"**: open5gs's
  metrics endpoint doesn't set a `Content-Type` header, which newer
  Prometheus versions (3.x) reject by default. Fixed via a `scrapeClasses`
  entry on the `Prometheus` CR (`fallbackScrapeProtocol: PrometheusText0.0.4`)
  — this field isn't available per-PodMonitor-endpoint in this CRD version,
  only at the scrapeClasses level.

## Access

- Grafana: `http://grafana-metrics.172.30.18.32.nip.io:31998` (admin/admin)
- Default dashboards ship with the chart: look for "Kubernetes / Compute
  Resources / Pod" for per-pod CPU/memory, and the `open5gs-metrics`
  PodMonitor's job for the native 5G KPIs above.
