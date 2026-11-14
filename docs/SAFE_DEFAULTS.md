# Safe installation and recovery defaults

Privileged OSM, Grafana and Rancher passwords have no built-in fallback. Supply
owner-only private configuration before installation; missing or common demo
credentials fail closed. The upstream OSM bootstrap password, when needed, must
also be supplied explicitly. Public example files contain empty secret fields.

The legacy BLER watcher is retired: the installer does not install/start it,
unit rendering and service launch refuse it, and its old failover function cannot
call the deployment API. If it is already running, installer preflight stops and
requires a separate operator-reviewed shutdown. Installation never silently stops
an existing service. None of these source corrections modifies a running system.

Layer 3 remains observe-only, LIVE_AUTO is rejected and its production executor
remains disabled. A nonempty failover name is not recovery authorization. Future
production recovery requires a separately reviewed implementation and explicit
operator authorization, ownership/protection checks and lifecycle locking.
