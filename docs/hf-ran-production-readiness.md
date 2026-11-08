# H-CRAN and Fog-RAN: production qualification (3GPP Rel-15/16 interface baseline)

**Status: EXPERIMENTAL — not a production release, 3GPP certification,
or authorization to modify the running cluster.**

Supported registry keys: `hcran-oai`, `hcran-srsran`, `fran-oai`,
`fran-srsran`. Their standardized foundation is 3GPP TS 38.401 (CU/DU),
38.470–38.474 (F1), 38.413 (NGAP), and 23.501/23.502 (5GC).
H-CRAN and F-RAN are research/deployment architectures rather than separate
Release 15/16 standard RAN modes.

## Implemented and demonstrated

| Scope | H-CRAN | F-RAN |
|---|---|---|
| Canonical Helm/OSM | One CU, macro DU, small DU | One CU, DU, fog compute |
| Configuration | Distinct DU/cell IDs, F1/NG dependency discovery | F1/NG discovery, bounded fog dependency lookup |
| Application | Two DU roles; no proprietary coordination algorithm | Real bounded processing via POST /process and metrics at /metrics |
| OAI RFSIM UE | Optional separate macro/small manifests, replicas 0 | Optional manifest, replicas 0 |
| srsRAN ZMQ UE | Compatible UE RF peer not supplied | Compatible UE RF peer not supplied |
| Recent HP lab evidence | Two F1-connected DUs/cells and NGAP observed; end-to-end on each cell not established | OAI registration, PDU and ping observed in one experiment |

Use `config/scenarios.json` and `docs/hcran-and-fog-ran.md` as source of
truth. Legacy `helm/hcran-*` and additive `helm/fran-edge` are not the
canonical profiles. An HTML statement in additive `fran-edge` about edge
UPF does **not** prove local user-plane breakout.

## Remaining acceptance requirements

1. **Multi-DU and transport:** time-correlated NGAP; unique macro/small F1
   setup identities; F1-U/NG-U GTP-U flow, and simultaneous service in H-CRAN.
   One shared log line is not independent proof for two DUs.
2. **End-to-end:** measured UE registration, PDU sessions, UE addresses,
   sustained UL/DL traffic and loss/latency on each applicable cell. Simulated
   SSB-power differences do not establish physical heterogeneous coverage.
3. **Mobility/coordination:** test inter-cell mobility and specify whether
   handover and interference coordination are implemented or explicitly out
   of the supported product scope. Do not claim either without acceptance.
4. **Fog:** verify actual compute response and scrape, physical edge placement,
   isolation and UE-to-fog latency. Real local breakout additionally requires
   an actual edge UPF with N6/DN routing and packet-path proof.
5. **Operations and security:** representative load and extended soak,
   multi-node SCTP/UDP reachability and MTU, capacity headroom, alert
   evaluation, controlled fault-recovery/MTTR, exact OSM rollback, image
   provenance, vulnerability/RBAC/secret/TLS review, and independent sign-off.
6. **Test governance:** predefine performance thresholds, capture dates and
   environment, preserve checksummed original captures, exclude private
   subscriber keys/identities, and review all four profiles separately.

## Read-only inventory (no OSM or Kubernetes mutation)

List scenario-specific evidence categories:

```bash
python3 scripts/hf_ran_readiness.py --scenario hcran-oai --list-checks
```

Keep evidence outside the Git checkout. Example `/private/hf-trial/manifest.json`
(a partial sample — missing checks deliberately block the result):

```json
{
  "schema": "5g-kubernetes/hf-ran-evidence-v1",
  "scenario": "hcran-oai",
  "source_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "artifacts": {
    "ngap": {
      "path": "captures/ngap.txt",
      "sha256": "replace_with_actual_lowercase_64_character_sha256"
    }
  },
  "checks": {
    "ngap_amf": ["ngap"]
  }
}
```

Capture real evidence for every `--list-checks` item and use
`sha256sum` to bind each file. Source commit must match the exact tested
checkout, not a moving branch name.

```bash
python3 scripts/hf_ran_readiness.py --scenario hcran-oai \
  --manifest /private/hf-trial/manifest.json \
  --evidence-root /private/hf-trial \
  --expected-commit "$(git rev-parse HEAD)"
```

Exit 1: `BLOCKED`. Exit 0: `EVIDENCE_INVENTORY_COMPLETE` **only**.
The tool checks manifest scope, expected Git SHA, paths and SHA-256 file
integrity. It **does not interpret packet captures, validate KPI claims,
prove 3GPP conformance or authorize production**. Reusing one irrelevant
artifact for all checks can pass inventory counting but must fail substantive
manual review. Its `production_authorized` field is always `false`.

Do not change `experimental: true`, `STATIC VALIDATED`, the installer
exclusions, automatic watchers or existing OSM deployments on the strength of
this inventory. Production qualification requires separately reviewed live
tests, resolved feature gaps, formal risk acceptance and a controlled rollout.
