# Stage 3: Real SCTP/NGAP recovery against Open5GS (isolated GitHub runner)

**Inputs:** GitHub Actions artifact `oai-ngap-2026w13-v2-image-2` from run
`43151733655`, serving as the verified base runtime image. The first hosted
runtime attempt revealed `SIGILL` (illegal instruction) before the CU reached
NGAP. To control CPU-instruction compatibility, the workflow **rebuilds the
same patched OAI `2026.w13` source on its own hosted runner** with
`--noavx2 --noavx512`, and overlays the rebuilt executable onto the isolated
experimental container. A successful run will therefore produce a **new
portable test image**, not certify the original Stage 2 binary as portable.
Third-party Open5GS image `gradiant/open5gs:2.7.6` is downloaded **on the
runner only**.

**Networking:** dedicated disposable Docker bridge `172.29.73.0/24`:
OAI CU `172.29.73.20`, AMF `172.29.73.10`, NRF `172.29.73.11`.
No DU or UE starts, and there is no cluster login, SSH, Docker push, OSM,
Longhorn, F1 traffic to the user's deployments, or production dashboard action.

Open5GS AMF container is initially running but the AMF daemon is gated off.
This preserves AMF IP/ARP reachability to reproduce the initial SCTP failure.
The test enables the real AMF daemon only after observing two redials. Then
it checks actual OAI `NGSetupResponse` logs, stops and restarts AMF in
a gated state, and verifies a *new* NGSetupResponse following recovery.

| Gate | Must observe |
| --- | --- |
| T1 | Initial SCTP unsuccessful response (OAI enum 3/0) |
| T2 | >=2 redials and >=2 unsuccessful association responses while AMF daemon absent |
| T3 | Open5GS AMF enabled on the isolated bridge |
| T4 | Real `Received NGSetupResponse from AMF` in OAI CU log |
| T5 | AMF stopped/restarted without daemon; CU redials again |
| T6 | A second real NGSetupResponse after AMF daemon is restored |
| T7 | Kernel SCTP association includes AMF IP, CU had no container restart, no obvious retry storm |

**Important limitations:** This is one controlled no-UE test of NGAP and
not a validated Rel-15/16 conformance test. No real NGAP counter export,
timer-queue inspection, F1-C/F1-U, E2 KPM/RC, NAS, UE registration, PDU,
or concurrent dual-cell behavior. No production readiness claim is authorized.

The test fails closed on any missing evidence, and always uploads CU/AMF/NRF
logs and stage results for debugging. Logs are ephemeral; they do not contain
IMSI/UE credentials because no UE is started. Artifact retention is 7 days.

The originating Stage 2 artifact expires Nov 19, 2026; reruns that attempt
to download it after expiration will fail before starting any container.
