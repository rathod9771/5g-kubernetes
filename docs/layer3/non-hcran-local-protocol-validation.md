# Layer 3 protocol-validation transfer: CI to local Ubuntu (non-H-CRAN)

## Verified scope and exclusions

This transfer preserves the srsRAN/Open5GS six-profile **isolated**
NG Setup (NGAP TS 38.413) and F1 Setup (F1AP TS 38.473) CI harness.
The six previously successful CI receipts are for disposable, dummy-radio
tests only. They do **not** establish RRC, NAS registration, PDU sessions,
F1-U/NG-U traffic, TS 28.552 KPI conformance or production readiness.

An additional srsUE/ZeroMQ experiment remains **BLOCKED**: the UE reached
"Attaching UE", but no InitialUEMessage or RRC completion was captured.
Its source and workflow are retained for traceability only, not production
qualification. All original workflow trigger scopes remain experimental:
promoting their files to main does not trigger the expensive tests on main.

For **local Ubuntu** use this separate read-only observer. It includes
C-RAN, vC-RAN, F-RAN, O-RAN and Cloud-RAN OAI and srsRAN Pods plus
Open5GS. It excludes **both H-CRAN OAI and H-CRAN srsRAN**, including
macro and small DUs. If a Pod's labels identify it as H-CRAN, it is excluded
regardless of name. The observer does not use stored subscriber credentials.

## Reproducible user-only installation

This is not the project's full `install.sh`. NEVER run the full platform
installer on the live HP reference machine for this task.

```bash
git checkout main
git pull --ff-only
python3 scripts/layer3/install_local_protocol_observer.py --check
python3 scripts/layer3/install_local_protocol_observer.py --install
~/.local/bin/layer3-protocol-observer --help
```

The installer uses only Python 3.10+ standard library. It copies one
checked Python script to `~/.local/bin`, with a deterministic SHA-256
receipt at `~/.local/bin/layer3-protocol-observer.install.json`.
No `sudo`, `pip install`, `docker pull`, `helm install`, `kubectl apply`
or remote download occurs in the installer.

To verify reproducibility on a second Ubuntu host, first checkout the
**same audited Git commit** before running `--check` and `--install`.
Compare SHA-256 receipts between hosts. The target host must separately
have `kubectl`, a configured kubeconfig and **read-only Kubernetes RBAC**
for `get/list pods` and `get pods/log`. The installer does not create or
elevate RBAC permissions.

## Safe local stages

First perform inventory without fetching Pod logs:

```bash
mkdir -p "$HOME/layer3-protocol-results"
umask 077
~/.local/bin/layer3-protocol-observer \
  --output "$HOME/layer3-protocol-results/inventory-001.json"
```

Review the listed non-H-CRAN Pods and Open5GS instances and make sure the
scope matches the installed topology. If the correct target is a different
Kubernetes context, choose that context before running the script.

Only after confirming inventory, observe bounded recent signalling markers:

```bash
~/.local/bin/layer3-protocol-observer \
  --observe --since 15m --tail 120 --max-pods 35 \
  --output "$HOME/layer3-protocol-results/observation-001.json"
```

The script performs one `kubectl get pods -A -o json` (18-second limit,
8 MiB response cap). In observation mode it issues up to 32 bounded
`kubectl logs` GETs, 10 seconds each, a 90-second envelope and 130 KiB
per response, with no retry. It returns only aggregate message counts,
not raw NAS logs, subscriber identities, IMSIs, encryption keys, ConfigMaps,
or Secrets. Observations can be incomplete if the budget is exhausted.

A message in a log is an observation, **not** proof that a 3GPP procedure
completed. A missing message is **not** proof of failure. For protocol
qualification, collect independently decoded NGAP/F1AP packets plus
correlated timestamps and verify each request/response and UE outcome
in a separately approved, isolated test.

## Local test matrix

| Technology | Local observe-only inventory | Local runtime validation |
| --- | --- | --- |
| srsRAN C-RAN, vC-RAN, F-RAN, Cloud-RAN, O-RAN | Enabled | Pending approved local test |
| OAI C-RAN, vC-RAN, F-RAN, Cloud-RAN, O-RAN | Enabled | Pending approved local test |
| srsRAN H-CRAN (macro + small) | **Excluded** | **Excluded** |
| OAI H-CRAN (macro + small) | **Excluded** | **Excluded** |
| Open5GS AMF/SMF/UPF/NRF etc. | Enabled for observation | No mutation |
| srsUE/ZeroMQ (experimental CI) | Not started locally | Blocked, no runtime reuse |

The local observer does not launch srsRAN/OAI, start UEs, change subscribers,
inject faults, or implement self-healing. Those require independent safety
authorization and preflight on the HP system given prior disk/CPU pressure.

## Tests and rollback

```bash
python3 -m unittest discover -s tests -p 'test_layer3_local_protocol_observer.py' -v
python3 -m py_compile scripts/layer3/local_protocol_observer.py scripts/layer3/install_local_protocol_observer.py
python3 scripts/layer3/install_local_protocol_observer.py --check
```

Uninstall removes the user-owned command **only when it still matches the
installed SHA-256 receipt**:

```bash
python3 scripts/layer3/install_local_protocol_observer.py --uninstall
```

Nothing is installed in Kubernetes, OSM, Docker or systemd. Existing
monitoring, dashboard, production executor and subscriber configurations
remain unchanged.

### Preservation / promotion policy

- Preserve the exact experimental CI source and original experimental
  workflows under `ci/experimental/` and `.github/workflows/`.
- Keep CI receipts tied to their specific GitHub workflow run.
- Keep srsUE/ZeroMQ outcomes BLOCKED, not PASS.
- Leave `layer3-autonomous/watcher.py` and ProductionExecutor disabled.
- Do not merge runtime traces, tokens or subscribers into source control.
- Do not use this observer's aggregate marker counts as a 3GPP compliance
  certificate or production readiness gate.
