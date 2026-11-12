# Stage 4J: Synthetic UE prerequisite verification for H-CRAN OAI

This CI job renders BOTH canonical UE variants against the H-CRAN macro and
small DU profiles using **synthetic in-memory credentials** and intentionally
discards the rendered Secret. No IMSI, key, OPC, or rendered private manifest
is uploaded. UE replica counts remain zero; no UE is started.

It checks the H-CRAN DU selectors, RF simulator frequency and channel
parameters, scoped deployment names, subscriber fields accepted by the
renderer, and the Open5GS components required by an eventual PDU test.

This does **not** establish a working end-to-end 5GC/UE or data plane. The
production decision remains **NO-GO** until a disposable synthetic subscriber
and real RFsim UE register, negotiate PDU, send/receive GTP-U traffic across
F1-U and NG-U, and exercise KPM/RC appropriately. Actual OSM-managed
rollout/rollback still needs a separately authorized staging cluster.

No K8s, OSM, Docker, subscriber DB, dashboard, or HP runtime access is used.
