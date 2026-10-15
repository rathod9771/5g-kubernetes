# Authorized dashboard restart — authentication blocker

Fresh protected-service preflight passed: both protected NSs READY/running/IDLE,
13 Open5GS pods and CU/DU/fog Ready, F1-C/NGAP SCTP established, no pending OSM
operation. Existing 205-test regression evidence and diff whitespace check passed.
Journal integrity/audit verified; all 100 audit rows and four incident IDs preserved.
Execution locks were free and no trial gate existed.

Current dashboard source/configuration/runtime/journal backup:
`.runtime/l3-4a-preflight/backup-1791978205`.
Exact incident/audit comparison baseline:
`.runtime/l3-4a-preflight/restart-baseline.json` (owner-only).

One noninteractive attempt was made:

```sh
sudo -n systemctl restart ran-selector.service
```

It exited 1 with `sudo: a password is required`. No service restart occurred,
no authentication retry was made, and no password was requested or stored.
The user must execute the approved manual command:

```sh
sudo systemctl restart ran-selector.service
```

Trial counts for this continuation: zero attempted/completed/interrupted/failed
collections. No live trial was started. Existing restart/trial authorization remains
valid, but activation and fresh safety verification are required before resuming.
No unrelated service, protected workload, journal history, monitoring configuration,
sudoers, commit or push was changed.
