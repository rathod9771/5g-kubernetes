# Stage 4A: H-CRAN NGAP V2 offline compatibility assessment

## Proven previously

Stage 3 GitHub-hosted run 43157479388 **passed** real NGAP recovery against a separate Open5GS AMF: initial SCTP failure, approximately 30-second redials, an actual NG Setup Response, and a second NG Setup Response after stopping/restarting AMF. The test used a rebuilt AVX2/AVX-512-disabled gNB to avoid SIGILL on the runner. It did **not** run a DU, UE, FlexRIC or OSM.

## What Stage 4A actually checks

The CI-only audit tests the canonical H-CRAN profiles:

- `helm/cran-oai/cu/values-hcran.yaml` (one CU);
- `helm/cran-oai/du/values-hcran-macro.yaml` (macro DU ID 100);
- `helm/cran-oai/du/values-hcran-small.yaml` (small DU ID 101).

On GitHub's disposable runner it lints and renders the three Helm components, validates the distinct cell IDs/PCI, F1-C SCTP and F1-U UDP ports, CU and DU addressing templates, stable AMF DNS, OAI image bindings, and bounded Pod memory limits. It loads the successful Stage 3 portable image as an offline archive, records its digest and verifies its direct dynamic-library dependencies.

**This workflow never runs `helm install`, `kubectl`, `docker push`, `osm`, SSH, or any dashboard or live-cluster action.**

## Promotion decision: NO-GO

A **green** Stage 4A audit is **not** runtime H-CRAN acceptance. Blocking evidence still required:

1. **E2/KPM/RC:** Stage 3 compiled OAI without an explicit `--build-e2` option. E2 setup and FlexRIC KPM/RC were not exercised; base-container libraries do not prove the CU's E2 behavior.
2. **Two DUs:** Neither macro nor small DU participated in Stage 3. Test F1 Setup with each DU independently, then simultaneous F1-C/F1-U and cell identity stability.
3. **Cross-version compatibility:** Canonical CU uses OAI `2026.w13` but both DUs use OAI `2026.w25`. Runtime interoperability must be observed, not inferred from chart rendering.
4. **UE registration and data:** NAS, authentication, registration, PDU session, and user-plane connectivity untested by this patch validation.
5. **Image and rollback:** The portable candidate exists only as a temporary GitHub Actions artifact. A content-addressed approved distribution route and OSM-controlled rollback are needed before any replacement.
6. **Live environment:** No current health/pressure checks or change approval for your HP Kubernetes, Open5GS, OSM, dashboard, or Longhorn.

Stage 4A emits `readiness.json`, Helm lint outputs, rendered YAMLs, image provenance and `ldd` evidence. Its `production_decision` is deliberately `NO_GO` even when every *static* check passes.

## Safe next experiment (not yet run)

On a **separate** GitHub-hosted test bridge, obtain the reference OAI `2026.w25` DU image, test **one** DU against the Stage 3 CU and Open5GS first. Require an identifiable F1 Setup Request and Response. Only then consider the other DU, testing bounded resource use. Keep all UE replicas at zero; no subscriber secrets or RF hardware should be required for initial F1-C. E2 feature compilation and live E2 setup must be isolated and validated independently.

Do not directly substitute this experimental image for `hcran-oai-cu` in the existing HP cluster.
