# Dell Precision-3680 P5 current OSM observer — observe only

## Scope

This is a separate observer for the **current** Dell research cluster. The historical
P4 observer (`p5_target.py`, `p5_osm_live.py`, `p5_osm_identity.py`) is unchanged.
The new script never publishes OSM packages, deploys or upgrades Kubernetes
workloads, enables remediation, opens the P4 gate, or changes HP release policy.

### Pinned, previously reported target

- Host: `Precision-3680`; Kubernetes node: `precision-3680`.
- Context: `kubernetes-admin@kubernetes`.
- API server: `https://172.30.0.79:6443`.
- `kube-system` namespace UID: `6087b451-69c5-4ea2-b657-372d2d87787c`.
- OSM namespace: `osm`; NBI ingress hostname: `nbi.172.30.0.79.nip.io`.
- NBI Kubernetes Service must be `ClusterIP` port `9999`, have a ready
  EndpointSlice endpoint, and match exactly one ingress backend rule.
- Preflight requires OSM pods to be ready and the existing P5 HP/P4 policy
  gates to remain locked.

If the cluster is rebuilt, any pinned value may legitimately change. Do **not**
change the pins without a fresh independent operator review of the target.

## Offline tests

```sh
python3 -m unittest discover -s tests -p 'test_p5_dell_current_osm.py' -v
python3 scripts/experiments/p5/p5_readiness.py --assert-locked
```

## Run preflight on Dell (no NBI credentials)

Run from this repository branch's root, on the Dell system only. This issues
bounded read-only `kubectl get` commands after checking host/context/server:

```sh
mkdir -p "$HOME/rajdeep/evidence"
umask 077
python3 scripts/experiments/p5/p5_dell_current_osm.py --preflight \
  --output "$HOME/rajdeep/evidence/dell-current-osm-preflight-$(date +%Y%m%dT%H%M%S).json"
```

`status=OBSERVED` means routing/Kubernetes readiness was observed. It **never**
means a signed release, authenticated OSM NS/VIM binding, or permission to deploy.
If the observation is rejected, check only the displayed fixed `reason_code`;
do not bypass the pinned identity or policy gates.

## Optional authenticated, read-only OSM inventory

**Only after** an approved, existing, least-privilege NBI token is available as
an owner-only regular file (mode `0600`, not a symlink):

```sh
umask 077
python3 scripts/experiments/p5/p5_dell_current_osm.py --inventory \
  --token-file "$HOME/.local/share/osm/nbi-readonly.token" \
  --output "$HOME/rajdeep/evidence/dell-current-osm-inventory-$(date +%Y%m%dT%H%M%S).json"
```

The script verifies the exact Dell baseline **before opening the token**. It
establishes a short-lived `kubectl port-forward` bound to `127.0.0.1` only, and
only issues GET requests to four fixed OSM inventory endpoints. The tunnel is
closed afterward; arbitrary hostnames, credentials in URLs, redirections,
proxy forwarding, and mutation endpoints are not supported. Raw NBI records,
including any embedded credentials, are never written to evidence files or
stdout. Output includes only UUIDs, constrained names, NS states and counts.

Successful inventory access is **discovery only**: it does not prove which
NS/VIM registration is correct for this Dell cluster. The operator must
select a specific NS instance and independently verify its VIM/cluster,
instantiate-operation and package/revision bindings in a later observe-only
qualification. No NS UUID from the former P4 test is assumed.

## Evidence and limitations

The `--output` path must not already exist; evidence is created exclusively as
mode `0600`. Exit status 0 means the requested observation succeeded, not
that release/deployment gates are open. Refused observations exit 2 and output
only fixed reason codes. Missing NBI credentials remain a hard blocker; do not
create or use an admin token solely to pass this observer. This is a research
validation tool, **not** a production controller.
