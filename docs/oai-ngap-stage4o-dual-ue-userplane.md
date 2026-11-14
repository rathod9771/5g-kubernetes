# H-CRAN OAI Stage 4O — two simultaneous synthetic UEs

A disposable GitHub-hosted 5GC + RFsim test using:
- Patched OAI 2026.w13 CU and OAI 2026.w25 DUs (macro ID 100, small ID 101).
- Two separate **synthetic** identities provisioned into disposable MongoDB.
- One active UE per DU, with separate OAI NR-UE containers.
- Full real NG Setup, F1 Setup, NAS Registration Accept, PDU Session
  Establishment Accept and unique UE PDU IPv4 addresses.
- Two concurrent 6-packet ICMP echo trains from separate UE TUN interfaces
  to the isolated Open5GS gateway (10.45.0.1).
- Packet-level GTPv1-U G-PDU evidence for macro F1-U UL/DL, small F1-U
  UL/DL and two distinct NG-U TEIDs in both directions.
- No RAN restart or lost SCTP association at the end of the experiment.

The test is **fail closed** and uploads sanitized evidence even if it
fails. Traces contain only artificial test traffic; private synthetic key
files are destroyed before artifact upload. No RF hardware, production
subscriber database, dashboard, HP Kubernetes or OSM write operation
is involved.

This is a functional dual-UE **smoke test**, not a performance or
3GPP-conformance certification. Explicit production blockers include:
long-term stability under high lab load; xApp KPM *indications* (not merely
subscriptions); substantive RC control response and demonstrable effect;
fault injection with both UEs under sustained traffic; approved staging
OSM image upgrade and rollback; signed immutable image provenance.
Production decision remains NO_GO.
