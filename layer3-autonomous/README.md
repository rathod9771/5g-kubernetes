# Layer 3 -- Autonomous optimization

Detects SLA degradation from Layer 2's real metrics and automatically
takes action to protect a critical application, per the mentor's
Intelli5G brief: "detecting SLA degradation and automatically changing
policies/resources... e.g. an autonomous robot".

## How it works

`watcher.py` runs continuously as a systemd service (`layer3-watcher.service`):

1. Every `CHECK_INTERVAL_SECONDS` (default 10s), queries Prometheus for
   the current max BLER (Block Error Rate) across the RAN, from the
   real metrics the `monitoring/ran-exporter` publishes.
2. If BLER exceeds `BLER_THRESHOLD` (default 0.05 = 5%), treats it as
   SLA degradation and calls the RAN selector dashboard's own
   `POST /api/deploy` endpoint -- the exact same path a human clicking
   the dashboard uses -- to fail over to `FAILOVER_SCENARIO` (default
   `hcran-oai`, which adds a second independent small cell alongside
   the macro cell: a genuine resiliency improvement, not an arbitrary
   swap).
3. Every action taken (or attempted-and-failed) is appended to
   `actions.log` as a JSON line, for the dashboard's Layer 3 panel to
   read and display.
4. A cooldown (`ACTION_COOLDOWN_SECONDS`, default 120s) prevents
   re-triggering a fresh failover while one is still settling.

## Why /api/deploy and not a new action path

The dashboard's `/api/deploy` is already proven end-to-end (real OSM
terminate+instantiate, verified working via the live UI). Reusing it
for autonomous actions means Layer 3's "action" side inherits that
same reliability, instead of building and separately proving a new
mechanism.

## Deploy

Requires `monitoring/prometheus-nodeport.yaml` applied first (exposes
Prometheus on NodePort 30990, since the watcher runs on the host
alongside `ran-selector`, not inside Kubernetes).

## Verified

End-to-end test (threshold forced below zero to guarantee a trigger):
watcher detected degradation, called `/api/deploy`, dashboard
performed a real OSM NS terminate+instantiate to `hcran-oai`, returned
`{"status": "success", "ns_instance_id": "..."}` -- genuine automated
action, not a simulated one.

## Known limitation

BLER is currently 0 at all times because the lab runs `rfsim`, a
lossless simulated radio link -- there's no real-world impairment to
push BLER above the threshold. The detection logic is real and
verified (see above), but triggering it "naturally" isn't possible on
this hardware; demoed instead via a forced-threshold test.
