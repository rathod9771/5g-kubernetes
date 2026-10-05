# Bounded O-RAN OAI acceptance

`oran-oai` uses `helm/oran-oai/cu` and `helm/oran-oai/du`, with canonical
Deployments and Services `oran-oai-cu` and `oran-oai-du`. Registry `ready` means
local package generation is supported; it does not claim live acceptance.
Legacy `helm/oai` and `osm-packages` remain historical inputs, unchanged.

The approved image policy supplies CU `oai-gnb:2026.w13` and DU
`oai-gnb:2026.w25`. No image build or FlexRIC decoder change is needed for
this bounded attempt. Default packages use RFSIM, E2 disabled, stable AMF
discovery and the existing F1 configuration. Resources can be supplied as
ordinary chart values; defaults introduce no new CPU throttling limits.

Local package preparation and validation (no Kubernetes/OSM access):

```bash
python3 -B scripts/osm_packages.py prepare oran-oai
python3 -B scripts/osm_packages.py validate oran-oai
python3 -B scripts/osm_packages.py paths oran-oai
```

Only `oran_oai_knf` and `oran_oai_ns` are generated, under
`build/osm-packages`. Previously published scenarios are retained.
Deployment/activation must be a separately authorized isolated acceptance
step. Do not use dashboard replacement to test alongside an accepted RAN:
its existing lifecycle can terminate the active RAN. Check shared-node
capacity and the existing duplicate gNB/cell identifiers before co-running.

After authorized initial deployment, run the read-only acceptance helper with
the actual workload kubeconfig and namespace:

```bash
python3 -B scripts/validate_oran_oai.py \
  --kubeconfig "$ORAN_KUBECONFIG" --namespace "$ORAN_NAMESPACE"
```

If Open5GS is in another namespace, supply `--amf-namespace`. Only the
`amf-ngap-stable` Service lookup uses that namespace; CU/DU pods, the
`oran-oai-cu` Service and FlexRIC remain in `--namespace`. Omitting the option
retains the existing same-namespace behavior. For isolated Precision acceptance:

```bash
python3 -B scripts/validate_oran_oai.py \
  --kubeconfig "$ORAN_KUBECONFIG" --namespace oran-oai-acceptance \
  --amf-namespace 66b508bf-f267-4690-aa4d-8662a0251971
```

The validator discovers the AMF ClusterIP from that Service and checks the
existing NGAP association against it. No alias Service or cluster change is
needed. Keep this option when subsequently validating with `--e2`.

It requires canonical identities and approved images, both pods Running/Ready
with zero container restarts, NGAP SCTP established to the stable AMF Service
and NG Setup Response, both F1 SCTP associations and F1 Setup Request/Response,
RFSIM, and E2 disabled. It repeats these checks after a 30-second stability
window and requires the same pod UIDs. It does not require UE/PDU.

The optional `cu/values-e2.yaml` and `du/values-e2.yaml` profiles enable E2.
They intentionally contain no RIC address: discover the current Service IPv4
using this read-only command, then supply it as `e2.ricAddress` when rendering
or performing a separately authorized Helm upgrade:

```bash
python3 -B scripts/validate_oran_oai.py \
  --kubeconfig "$ORAN_KUBECONFIG" --namespace "$ORAN_NAMESPACE" --ric-address
```

Pass the corresponding `scripts/runtime_images.py values oran-oai cu` or
`du` output as a values file when rendering source charts. Generated charts
already contain approved image values. Apply `values-e2.yaml` before the
explicit `e2.ricAddress` override. DNS, placeholders and invalid IPv4 octets
fail rendering. Never reuse a historical hardcoded RIC address without
checking the actual FlexRIC Service.

Each E2-enabled pod copies only `libkpm_sm.so` and `librc_sm.so` from its
approved image into an `emptyDir`, mounted read-only at
`/tmp/flexric-sm-kpm-rc`. The agent uses that directory; it does not load the
full `/usr/local/lib/flexric` plugin set. Missing libraries fail the init
container rather than falling back to other service models.

After authorized E2 activation, run the same acceptance helper with `--e2`.
It additionally checks the actual Service IPv4 in effective config, exactly
the two plugin files, their loaded process memory mappings, and either an
established E2 association to that Service or OAI's explicit
`[E2-AGENT]: E2 SETUP-REQUEST tx` log from the current container. Generic setup
or attempt messages do not count. Setup Response is desirable, not required.
Raw container logs/config are not printed by the helper.

Historical packet captures prove request transmission but not a completed
FlexRIC handshake. FlexRIC's full-plugin decoder assertion remains unresolved;
this patch makes no claim about subscriptions, RC control, O1 or Open Fronthaul.
Stop if recovery requires rebuilding OAI/FlexRIC, repairing the decoder,
changing an accepted profile, Open5GS or subscriber data.
