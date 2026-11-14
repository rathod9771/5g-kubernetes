# L3-4B preflight — trial not started

The approved periodic trial has not run. Counts: zero attempted, completed,
interrupted or failed collection cycles; all ten authorized slots remain unused.
No restart, retry, live collection, protected modification, commit or push occurred.

Protected-service preflight passed: Fog-RAN NS
2c128db3-e27e-4dbe-8854-17751ba17680 and Open5GS NS
1b1cb300-b571-43f2-a567-6540895d7d0f are READY/running/IDLE, no pending OSM
operation; CU/DU/fog and all 13 Open5GS pods Ready; F1-C and NGAP SCTP established.
The reviewed Alertmanager endpoint is unchanged. Local backup/evidence is preserved
in `.runtime/l3-4a-preflight/backup-1791977501` and `preflight.json`.

SQLite integrity_check=ok, audit chain valid, 100 audit records, all four incident
IDs retained, DELETE mode unchanged, production database SHA256 unchanged.
Both assurance execution locks were free, no previous trial/collector process was
running, and the trial directory/guard-start marker were unused. Legacy watcher
inactive; LIVE_AUTO false and production executor DISABLED.

Blocking prerequisite: activation.md step 3 requires the corrected dashboard API/
Store code to be loaded. ran-selector.service remains PID 578791, NRestarts=0,
started 2026-10-14 11:12:24 IST. SQLite source corrections were written around
16:29–16:31 IST. backend.py imports assurance.api during startup and runs with
debug=False/no automatic reloader. The current dashboard therefore retains the
previous implementation with writer-reserving read transactions. Starting now
would bypass the reviewed SQLite-correction deployment prerequisite.

The latest user authorization explicitly prohibits service restarts, so the agent
did not execute the plan's dashboard restart command. The precise prerequisite is:

```sh
sudo systemctl restart ran-selector.service
```

This requires a manual dashboard reload or separate authorization for that service
only. No sudo password was requested and no sudo command was attempted. The existing
one-trial approval remains unused; after the prerequisite is satisfied, fresh
preflight verification is required before starting it.

Trial CPU/RSS/PSI, per-cycle lock waits and sustained correlation were not measured
because no trial ran. Evidence: `preflight-results.json` beside this report.
