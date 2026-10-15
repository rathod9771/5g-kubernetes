# Approved L3-2 activation attempt — blocked before restart

The user approved dashboard activation and exactly one observe-only collection.
The verified command `sudo -n systemctl restart ran-selector.service` was refused:
`sudo: a password is required`. No dashboard restart or monitoring collection was
performed. The running dashboard remains active with unchanged PID 1492967.
The agent stopped without retrying, running a collection, changing services,
changing Kubernetes resources or executing remediation.

Preflight passed: both protected NSs READY/IDLE; no pending OSM lifecycle operation;
CU/DU/fog Ready; 13 Open5GS pods Ready; CU/DU F1 SCTP and CU NGAP SCTP established;
no E2 SCTP observed. Full protocol setup/session/user-plane state was not inferred
from readiness or SCTP. The watcher was verified inactive and production execution
is disabled. The 44 policy classifications remain 8 supported, 27 partial and
9 unavailable. Current KPM remains unavailable.

Dashboard/configuration backup: /home/rclab/5g-kubernetes/.runtime/l3-activation/backup-1791954483

A dashboard-only rollback was prepared but not executed. It preserves the served
pre-activation HTML, reviewed source/private configuration, service unit and a
backend reconstruction with only assurance registration removed. No claim is made
that the old loaded Python process can be recovered byte-for-byte from source.
Private failure and preflight evidence are in `.runtime/l3-activation/`.

Collection results, generated live alarms, fresh provenance and actual collection
CPU/memory consumption are **not available for this activation attempt**, because
zero collection cycles ran. The earlier read-only investigation established four
loopback/node-IP scrape mismatches and current E2/KPM unavailability; no repair was
attempted. Post-activation service/workload validation could not be performed because
activation did not occur. Protected health figures above are the fresh preflight,
not an assertion of a completed post-activation check.

An authenticated administrator session is required to restart only the dashboard.
The existing user authorization remains recorded; another approval request is not
needed. No unattended retry or collection has been scheduled.
