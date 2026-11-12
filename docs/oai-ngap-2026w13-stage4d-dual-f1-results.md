# Experimental NGAP V2 — Stage 4D dual-DU F1-C acceptance (GitHub-only)

**Result: PASS (bounded isolated control-plane test, NOT production acceptance).**

- **Successful run:** https://github.com/rathod9771/5g-kubernetes/actions/runs/43162695243
- **Git revision tested:** `a0e3f8d1b438df6802d32878266525d4833a4f51`
- **Evidence artifact:** `oai-ngap-stage4d-dual-du-f1-1`, artifact ID `13918517457`, expires November 19, 2026.
- **CU:** NGAP V2 patched OAI `2026.w13`, CPU-portable Stage 3 image from successful run `43157479388`.
- **DUs:** Official OAI `2026.w25`, rendered from the canonical H-CRAN macro ID 100 and small ID 101 profiles.
- **Core:** Separate Open5GS `2.7.6` AMF/NRF, aligned to PLMN 20893 and S-NSSAI SST 1/SD 010203.
- **Location:** Disposable GitHub-hosted Docker network only. **No HP/Kubernetes/OSM/Longhorn/dashboard changes.**

## Observed checkpoints

| Gate | Evidence | Outcome |
|---|---|---|
| NGAP | CU received real `NGSetupResponse` from Open5GS | PASS |
| Macro F1 | CU received F1 Setup Request from DU 100 | PASS |
| Macro F1 | CU sent F1 Setup Response to DU 100 | PASS |
| Small F1 | CU received F1 Setup Request from DU 101 | PASS |
| Small F1 | CU sent F1 Setup Response to DU 101 | PASS |
| Concurrent peers | CU kernel SCTP table included AMF, macro and small peer addresses | PASS |
| Bounded stability | All CU, macro DU, small DU, AMF, NRF processes remained up for an additional 15-second hold | PASS |

The CU kernel SCTP association table captured in `final-cu-sctp-associations.txt`
shows all three peers simultaneously:

- AMF `172.29.74.10:38412`, established association
- Macro DU `172.29.74.30` (CU F1-C port 38472), established association
- Small DU `172.29.74.31` (CU F1-C port 38472), established association

At final bounded sample time, `docker stats` reported:

- Macro DU: ~577.5 MiB / 2 GiB, 18.22% CPU
- Small DU: ~576.9 MiB / 2 GiB, 18.27% CPU
- CU: ~10.0 MiB / 2 GiB, 2.29% CPU

These figures are one instantaneous snapshot and must **not** be generalized
as throughput, latency, or sustained production resource consumption.

## Acceptance limitations and unresolved gates

1. This is only **F1-C control-plane** setup and a brief simultaneous SCTP hold.
   No UE, F1-U tunnels/traffic, NG-U, QoS or mobility were exercised.
2. There is no authorized-subscriber NAS registration, authentication, or PDU
   session test.
3. The Stage 3 CU used `./build_oai --gNB -w SIMU --noavx512 --noavx2`
   without `--build-e2`; FlexRIC E2 Setup, KPM/RC telemetry/control,
   and xApp behavior are **not verified**.
4. No failure injection was applied with both DUs connected. The AMF
   restart/recovery experiment in Stage 3 involved **no DU**.
5. Container archive provenance and OSM-controlled image rollout/rollback
   must be validated before any migration to real Kubernetes.
6. This result does not establish production readiness, 3GPP conformance
   or live H-CRAN UE support.

**Decision remains NO-GO for production promotion.**

Next safe work: a separate, E2-enabled experimental CU build and
nonintrusive FlexRIC KPM/RC test; subsequent UE/PDU and F1-U acceptance
must be staged and authorized independently. Do not change the live HP
CU or start multiple UE simulators as part of these CI tasks.
