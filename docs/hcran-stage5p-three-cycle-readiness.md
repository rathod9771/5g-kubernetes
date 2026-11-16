# Stage 5P — three consecutive AMF-only failures with unchanged two UEs

**Experimental. NO DEPLOY. Production decision: NO-GO.**

Stage 5N passed three *independent* one-fault runs, which did not prove that
the same two UE processes could survive multiple sequential AMF restarts.
Stage 5P builds on the same frozen source-pinned Stage 5M UE image and
Stage 4P/5I test harness, extending it to **three consecutive AMF-only
stop/restart cycles within the same disposable 5GC/RFsim session**.

All three cycles must independently prove:
- Same UE container IDs, start times, PIDs and zero restart count.
- CU NGAP reconnect and both F1-C DU associations.
- Ordered post-fault Service Request retry, NAS cause #9 handling,
  SUCI registration, PDU Establishment Accept.
- A *different* allocated PDU IPv4 per UE, refreshed live TUN and distinct
  macro/small addresses.
- Two simultaneous 6/6 UE ICMP streams and real bidirectional per-DU F1-U,
  N3 NG-U G-PDUs with two NG-U TEIDs, packet capture from **that cycle only**.
- A matching per-cycle Stage 5I and Stage 5K verification, a unique
  SHA-256 of its packet capture, and continuity of PDU IP between cycles.

If any cycle fails, **entire test fails**; no historical Stage 5N PASS is
reused for missing evidence. All manifests are non-deployment evidence.
Artifacts contain synthetic subscribers only; the private input directory
is removed before upload. This experiment uses GitHub-hosted Ubuntu runners,
not the HP self-hosted lab. Nothing calls Helm install/upgrade, OSM, Rancher
or real subscriber services.

The first successful multi-cycle result qualifies *research-staging
recovery repeatability*, not an SLO, commercial 3GPP conformance, or release.
OSM rollback, security negative cases, heavy-load soak, physical RF tests
and protected-cluster approval remain open.
