# Stage 4B — One H-CRAN macro DU F1-C compatibility test

This is an **experimental, disposable GitHub-hosted Docker test**, not an
approval to modify the current HP Kubernetes or OSM workloads.

## Fixed inputs

- CU: OAI 2026.w13 NGAP V2 patch, AVX2/AVX-512-disabled image from successful
  Stage 3 run `43157479388`; exact GitHub Actions artifact
  `oai-ngap-stage3-portable-4`.
- DU: reference container `oaisoftwarealliance/oai-gnb:2026.w25`
  (pulled and its resolved image ID recorded by the test runner).
- Core: isolated `gradiant/open5gs:2.7.6` AMF + NRF.
- Configuration: **rendered from canonical** `helm/cran-oai/cu` and
  `helm/cran-oai/du` with the H-CRAN CU and macro profiles. Runtime IP
  placeholders are replaced only in disposable files, not the tracked charts.
- Topology: one CU, **macro DU ID 100 only**. Small DU 101 is not started.
  No UE, subscriber secrets, FlexRIC/E2 apps, USRP, radio devices or PDU sessions.

## Observability

The test waits for the CU to receive a real Open5GS NG Setup Response, then
starts one macro DU using `--rfsim` on the isolated Docker bridge. Its
core control-plane acceptance is **two** source-derived OAI CU log markers:

1. `Received F1 Setup Request from gNB_DU 100`
2. `DU 100 (...): sending F1 Setup Response`

A missing marker, CU/DU crash, or timeout fails the job and saves the CU,
DU, AMF, NRF, SCTP and rendered-config evidence. A successful test is
only **one-DU F1-C** validation. It does not prove actual F1-U traffic,
two-DU coordination, simulated UE registration, E2 KPM/RC, mobility or
production resilience.

## Resource and safety boundary

The disposable CU and macro DU containers have explicit CPU/memory limits.
No second DU is started; the test waits for NGAP before starting DU.
The script removes only explicitly named disposable test containers/network.
GitHub Actions job permissions are read-only and never include
`kubectl`, `helm upgrade/install`, `docker push`, OSM,
production registry credentials, or access to the user's HP cluster.

**Production promotion remains NO-GO** even if Stage 4B passes.

The next acceptance gate, if this succeeds, would be the small DU alone,
followed by a separately capacity-reviewed simultaneous dual-DU experiment.
E2 remains a separate build and runtime-validation blocker.
