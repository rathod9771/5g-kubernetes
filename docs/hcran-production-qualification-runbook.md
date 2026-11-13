# H-CRAN 3GPP production qualification — operational runbook (NO-GO)

**Status (2026-11-13): NO-GO.** This document does **not** authorize a production
deployment, live fault injection, E2/RIC reconfiguration, release promotion or
subscriber modification. The target is **3GPP-based H-CRAN without Near-RT RIC
or E2SM-KPM**. Monitoring is provided by Prometheus/Grafana.

## 1. What is actually demonstrated

The existing isolated GitHub-hosted tests have established dual-DU F1-C,
NGAP, two simultaneous disposable synthetic UE registrations, two independent
PDU sessions, and bidirectional F1-U/NG-U GTP-U.

**Stage 4X** (`43378042762`): the CU used an explicit NEA2/NIA2-only
configuration. Two synthetic UEs registered and transferred bidirectional
user-plane traffic successfully: 6/6 ICMP replies each, F1-U macro 14/12,
small 14/12, NG-U 28/24. These results establish **functional compatibility
of the configured profile** on the isolated runner, not independently decoded
NAS/AS algorithm negotiation, formal 3GPP conformance, physical RF test or
production-cluster compatibility. **Stage 4Y** (`43379180419`): under that same NEA2/NIA2-only CU
configuration both simulated UEs passed six traffic rounds (60/60 ICMP
replies each) over 97.18 seconds, with all three CU SCTP peers maintained,
zero detected container restarts/OOM, macro/small F1-U 126/120 each and
NG-U 252/240. This is a bounded functional stability smoke, **not a
multi-hour capacity or production SLO test**.

The tests retained the previously functioning privileged image runtime.
They did NOT validate the experimental unprivileged Kubernetes Pod security
overlays or an independently decoded AS/NAS negotiated security algorithm.

The upstream pinned OAI `2026.w13` has F1 intra-CU handover code and a
manual trigger. Its documentation also specifies a special UE-side RFsim
server arrangement and `--build-lib telnetsrv`; the existing NGAP-patched
CU portable image does not establish that the telnet module was built.
**Stage 4W mobility remains BLOCKED, not PASS.**

The historical GitHub evidence inventory is **not** a compliance percentage.

## 2. Scope and architecture

```text
            Open5GS AMF / SMF / UPF
                     N2 / N3
                        |
                    OAI gNB-CU
                   /         \
                 F1-C/U     F1-C/U
                   |           |
              macro DU 100  small DU 101
                   |           |
                 NR UEs (disposable tests)

      Prometheus / Grafana: monitoring only
      NO Near-RT RIC, FlexRIC, E2SM-KPM, E2SM-RC
```

The name H-CRAN describes the research macro/small-cell deployment profile,
not a separate conformance category defined by 3GPP.

## 3. Stage 0 — release inputs and host qualification

Before starting real staging tests, obtain explicit staging change approval,
a *separate disposable* namespace, pinned OCI digests for all RAN/core
workloads, proof of available CPU/disk IOPS/RAM, core subscriber isolation,
read-only dashboard access, a rollback owner, and an independent
timestamped observer. Never use the high-load HP production-like single-node
as a disposable RAN fault-injection target without separately assessing its
current resource health.

**Security blockers:** the original OAI profile allowed `nea0` and `nia0`;
the strict NEA2/NIA2 candidate has passed a bounded synthetic runtime
test, but negotiated security has not been decoded independently.
Default CU/DU containers remain privileged. The separate staging overlays
for `privileged: false`, `allowPrivilegeEscalation: false`, dropped
capabilities and `RuntimeDefault` seccomp have **no runtime proof**.

`kubectl` inventory snapshots and pressure files can reveal addresses
and operational details. Store privately, use `umask 077`, never upload
unencrypted snapshots or subscriber/Key/OPc contents to public Actions.

## 4. Stage 1 — read-only staging inventory (no cluster changes)

Only after specifying the **correct staging context and namespace**:

```bash
# Execute from the repository's experimental qualification checkout.
# These commands read only. They MUST NOT be used to infer production GO.
CTX='<explicit-staging-kubecontext>'
NS='<approved-disposable-staging-namespace>'
EVIDENCE="$HOME/hcran-staging-readonly-evidence"
umask 077
mkdir -p "$EVIDENCE"

# Verify before collecting; do not rely on the current default kubecontext.
kubectl config get-contexts "$CTX"

# On EACH actual Kubernetes node hosting a H-CRAN CU/DU Pod, collect
# the local /proc pressure snapshot (the script never accesses Kubernetes):
# python3 scripts/hcran_node_pressure.py --node '<exact nodeName>' \
#   --output "$EVIDENCE/pressure-<nodeName>.json"
# Transfer the sanitized pressure JSON files securely to this workstation,
# then merge all node samples; this does NOT run any fault injection.
jq -s '{nodes: [.[].nodes[]]}' "$EVIDENCE"/pressure-*.json \
  > "$EVIDENCE/pressure.json"

# The preflight uses ONLY 'kubectl get' on an explicitly specified context.
python3 scripts/hcran_staging_inventory.py \
  --live-read-only --context "$CTX" --namespace "$NS" \
  --pressure "$EVIDENCE/pressure.json" \
  --report "$EVIDENCE/hcran-staging-inventory.json"
```

The preflight checks three H-CRAN deployment identities, synchronized
Deployment generation, Pod readiness and image agreement, immutable
digests, internal Service selectors/ports, NEA2/NIA2 CU configuration,
absence of RIC/E2 agent directives, explicit non-privileged security
configuration, **actual Pod versus template security-context identity**,
disabled Kubernetes service-account token automount, no injected projected
API tokens/host-network/host-PID privileges, and recent per-node CPU/IO
pressure readings.

Current **advisory** 10-second pressure gates are CPU PSI some <25%,
I/O PSI some <10%, load-per-logical-CPU <1.5, with a pressure timestamp
no more than five minutes old. These are conservative *engineering
preflight thresholds*, **not normative 3GPP limits or production SLOs**.
A single sample is insufficient to infer sustained SLO health.

**Exit:** `BLOCKED` means stop and inspect the listed reasons. A
`READY_FOR_ISOLATED_STAGING_TEST_ONLY` result is only permission to
consider a **separately approved staged test**. This checker always outputs
`production_authorized=false` and `production_decision=NO_GO`.

No real HP Kubernetes observation has been made through this development
branch. The static unit fixtures and CI cannot substitute for it.

## 5. Stage 2 — gated protocol and user-plane experiments

Execute on disposable, isolated hardware/runner resources and save
independent timestamped captures. Each gate needs *raw evidence*, a
reproducible procedure, positive and negative cases, a predeclared
metric/threshold and an operator-reviewed outcome.

| Gate | Standard foundation | Runtime evidence required |
| --- | --- | --- |
| Basic F1/NG | TS 38.401, 38.473, 38.413 | Both DUs, NG Setup, correlated F1 Setup and SCTP sessions |
| Dual UE / PDU | TS 24.501, 23.502 | Two unique synthetic subscribers, Registration Accept, PDU IP and N3 data |
| F1 inter-DU handover | TS 38.331, 38.473 | Macro → small → macro transition, source/target UE context, retained PDU service, measured interruption |
| Security | TS 33.501, 38.331, 24.501 | Independent negotiation proof NEA2/NIA2, reject/retry paths and no secrets in logs |
| QoS | TS 23.501, 23.502 | PDU Session Modification/Release, QFI/5QI/DRB treatment and per-UE data-plane continuity |
| Native recovery | TS 24.501, 38.331, 23.502 | AMF-only and full-5GC restart; UE self-restores without process restart |
| Performance | 38.2xx + accepted service objectives | Multiple runs and a sustained observation window, packet loss/RTT/throughput, resource pressure and unplanned restarts |

Specific failures remain: prior isolated tests showed NGAP reconnecting while
unassisted UE PDU service did **not** recover within 150 seconds. Stage 4U
only proved supervisor-triggered synthetic UE process restarts. Such restarts
must not be relabeled as native UE self-recovery.

## Stage 4Z — reproducible F1 mobility experiment, NOT a release gate pass

**Stage 4Z real isolated success (13 November 2026):** run `43391519669`,
artifact `14021245591`. One synthetic OAI UE completed macro DU 100 →
small DU 101 → macro DU 100 handover. For **each** transition, the harness
observed post-command UE RRCReconfigurationComplete, target F1 association
update and source-context release, retained UE PDU IPv4, 6/6 successful
ICMP replies, and actual target F1-U **12 UL / 12 DL** and NG-U
**12 UL / 12 DL** G-PDUs. The baseline macro leg had F1-U 14 UL / 12 DL
and NG-U 14 UL / 12 DL. Raw pcaps were checksummed and the independent
structural evidence validator returned PASS.

This **qualifies the isolated RFsim two-way mobility use case**, not
radio/RF handover performance, repeated or stressed mobility, formal
3GPP conformance, the running HP cluster, or production availability.
The build's CLI telnet endpoint is unauthenticated and must never be enabled
in a production deployment. The production release gate now requires
separate staging mobility, controlled interface security, and operator
approval. **NO-GO** remains in force.


The actual patched CU image used by the earlier successful dual-UE trials
**lacks both** `libtelnetsrv.so` and `libtelnetsrv_ci.so` (GitHub-hosted
binary audit run `43388023099`). Consequently the upstream OAI manual
F1 handover command cannot be assumed available in that image.

An isolated source-pinned rebuild is specified in
`scripts/hcran_build_pinned_f1_cu.sh` and
`.github/workflows/hcran-f1-cu-build-v2.yml`, compiling OAI upstream commit
`4941f44043e6dcec81b2041e83dbae4bee6f737d` plus the reviewed NGAP
retry patch and its telnetsrv/CI modules. Build run `43388901151`
**PASSED compilation, linkage, image packaging, and artifact upload**.
This produced a disposable image archive artifact `14019769912`;
it is neither a hardened production image nor a tested handover result.

The disposable runtime harness
`experiments/oai-ngap-2026w13/stage4z/f1_handover_runtime.py` and the
associated opt-in GitHub workflow
`.github/workflows/hcran-stage4z-real-f1-handover.yml`
use a **single test UE as RFsim server** and two independently
configured DU clients (SSB position bitmaps 1 and 2), as required by
the pinned OAI handover tutorial. Only an explicitly approved synthetic
experiment may expose the unauthenticated telnet CI interface on the
runner's disposable Docker bridge; **never enable this telnet interface
on a production CU**.

Stage 4Z requires both macro→small and small→macro transitions, a
post-command RRCReconfigurationComplete log, updated F1 UE context,
source DU release, unchanged UE PDU IP, 6/6 post-handover ICMP replies
and nonzero bidirectional GTP-U F1-U and NG-U packet counts in
the *target DU* for each transition. An ACK or sent telnet command
alone is explicitly insufficient. The independently unit-tested
`stage4z/evidence_contract.py` rejects fabricated completeness,
but its synthetic unit tests do not prove a live handover.

The completed Stage 4Z test is **disposable RFsim integration evidence**,
not 3GPP conformance, physical radio mobility, or production permission.

## Stage 5A — AMF recovery without UE process restart (negative evidence)

[Isolated run 43395100149](https://github.com/rathod9771/5g-kubernetes/actions/runs/43395100149)
reproduced an AMF-only outage and patched-CU NGAP reconnect with both F1-C
DUs surviving. Immediately after reconnect, **both** OAI synthetic UEs
had no new NAS Registration Accept, no PDU Session Accept, and **no PDU
TUN interface**. We then attempted an *additional controlled macro DU
process restart*, not a UE restart. The experiment **FAILED** as RFsim
connectivity/RRC state was disrupted; it did not establish NAS/PDU recovery
with the same UE processes. A separate run's PDU restoration after explicit
UE process recreation is *assisted recovery only* and cannot close native
resilience acceptance. **AMF_ONLY_FAULT_UNASSISTED_UE_RECOVERY stays BLOCKED**.

The RFsim transport is an experimental software model. Failure on socket
loss is not a determination that physical commercial UE products fail 3GPP
standards; production target UEs and the actual AMF recovery/NG Reset
procedure still need qualified acceptance evidence. Do not try to resolve
this on the overloaded HP host with forced DU restarts.

## Stage 5B — actual RAN least-capability Docker runtime (positive evidence)

[Isolated run 43395469675](https://github.com/rathod9771/5g-kubernetes/actions/runs/43395469675),
artifact `14022567512`, **PASSED** while all three OAI RAN processes
(CU, macro DU 100, small DU 101) ran with Docker `--cap-drop ALL`,
`--security-opt no-new-privileges=true`, no privileged mode, and
`CapEff=0` verified in each process. Both UEs still registered and
received independent PDU IPv4 addresses (`10.45.0.2` and `10.45.0.3`),
with 6/6 ICMP each, macro F1-U 12 UL/12 DL, small F1-U 14 UL/12 DL,
and NG-U 26 UL/24 DL G-PDUs.

This is a **real Docker functional compatibility PASS** under NEA2/NIA2
CU configuration, not proof of Kubernetes admission, NetworkPolicy
enforcement, RBAC, Pod Security Standards or live production security.
The operational defaults and running HP deployments remain unchanged.
Use the opt-in Helm `values-hcran-pod-security-staging.yaml` overlays
only in separately approved disposable staging.

## Stage 5C — extended bounded two-UE stability

**Stage 5C PASSED**: [isolated GitHub Actions run 43396068709](https://github.com/rathod9771/5g-kubernetes/actions/runs/43396068709),
artifact `14023447801`. The NEA2/NIA2 CU and two synthetic UE PDU
services completed **24/24 rounds** with **240/240 ICMP replies per UE**
over **492.54 seconds**, while checking all three required CU SCTP peers
and detecting no container restarts/OOM. Real GTP-U pcap showed
macro F1-U UL/DL **488/480**, small F1-U **492/480**, and combined NG-U
UL/DL **980/960**. Sampling and packet evidence were saved with checksums.
This is a bounded **8-minute RFsim functional endurance PASS**, not a
multi-hour production SLO, physical radio qualification, network capacity
benchmark, independent Prometheus scrape, or staging release authorization.

## Stage 5D — AMF-only recovery plus manual F1 mobility: BLOCKED

[Run 43397277202](https://github.com/rathod9771/5g-kubernetes/actions/runs/43397277202),
artifact `14023696456` (read-only logs and pcaps), **FAILED** even with
one UE acting as RFsim server and a corrected core launcher that preserves
all non-AMF Open5GS services.

- Before the AMF fault, both F1 handovers in the disposable environment
  passed with 6/6 ICMP and genuine F1-U/NG-U packets.
- AMF termination generated SCTP shutdown at the CU. The pinned OAI
  NGAP handler released its gNB UE context, sent **RRC Release** and removed
  the CU UE context (log `cleanup-cu.log`, context UE 1). The UE
  consequently moved to **RRC_IDLE** and its PDU/TUN data-plane resources
  were released.
- After a new NGSetupResponse, no fresh NAS/PDU registration occurred.
  The lab-only `ci trigger_f1_ho` request reported
  `could not find any UE in RRC`; no subsequent F1 handover completed.
  A UE-less F1 handover request is not an appropriate recovery mechanism.
- This is a real runtime **negative result**; native AMF recovery remains
  `NO_GO`. No OAI process, HP Pod, real subscriber or production
  Kubernetes resource was restarted/modified.

Source-level context: pinned OAI `2026.w13` source
`openair3/NAS/NR_UE/nr_nas_msg.c` sets `fiveGMM_mode=FGS_IDLE`
on `NR_NAS_CONN_RELEASE_IND` but still carries a
`TODO handle connection release`; UE RRC code
`nr_rrc_going_to_IDLE` removes SDAP/TUN entities. This indicates an
incomplete synthetic-UE idle service-resumption path, but does not
establish that all physical 3GPP UEs share this behavior.

## Stage 5E — newer OAI synthetic UE with idle uplink-triggered Service Request

The **2026.w45 source** adds an idle TUN listener and
`NAS_MO_UL_DATA_IND` pathway that can initiate a 5GMM Service Request
on pending uplink data, and it handles NAS Service Accept. This is
a plausible *standards-based* fix for the synthetic UE idle recovery
behavior; not yet proven in this H-CRAN setup.

`stage5e/run_idle_service_recovery.py` and
`hcran-stage5e-native-idle-service-recovery.yml` are isolated trials
using **only a newer synthetic UE**. Frozen CU/DU/Open5GS are retained.
Both UE processes must remain unchanged through an AMF-only outage and
NGAP reconnect. The test actively generates UE application uplink traffic
to request NAS service, then requires **both** restored PDU interfaces,
6/6 ICMP replies each, fresh NAS resumption logs, and independent
F1-U/NG-U data-plane proof. It must fail closed on a missing UE or
missing GTP-U. **Pending actual workflow results:** no PASS can be claimed
from source inspection alone.

A 5G Service Request restoring existing PDU service need not produce
a *new Registration Accept or PDU Session Establishment Accept*.
The acceptance contract therefore focuses on real service-state
restoration and post-fault GTP-U, not merely fresh initial-attach logs.

## 6. Stage 3 — real OSM staging lifecycle

Require an approved immutable OCI digest, versioned Helm/OSM NSD+KDU,
tested namespace quota, independent service observer, target snapshot and
individually signed deployment/rollback authorization. Then, in a separately
approved staging-only change window:

- Establish and archive the pre-upgrade image digest, OSM NS status and
  NGAP/F1/PDU baseline.
- Upgrade via the **actual OSM** workflow, not Helm template rendering or a
  fabricated command log. Verify the new image digest and live UE sessions.
- Revert through the **actual OSM** rollback procedure to the exact saved
  digest. Re-measure registrations, UE service, F1 links and resource health.
- Repeat independent healthy/failure cycles, validate rollback trigger and
  terminate disposable synthetic UEs; review the captured evidence.
- Review security posture, NetworkPolicies, minimum RBAC and secret storage
  before any production cutover.

The repository has **no positive staging OSM rollback acceptance evidence**.
Do not delete or reinstall current HP OSM components simply to prove this.

## 7. Promotion decision

An offline CI run, a Helm render, source audit, read-only inventory pass or
GitHub artifact **never authorizes production deployment**. Deployment remains
`NO_GO` until: all approved mandatory scenarios have positive evidence,
the exact tested/pinned release candidate is available in a durable registry,
security and resource checks pass, staging rollback is proven, an independent
reviewer signs the evidence, and a separate operator authorizes a controlled
production rollout with its rollback plan.

The architecture is 3GPP-based; complete 3GPP Release 15/16 conformance
requires a separately selected formal conformance test matrix, not simply
passing these integration acceptance gates.
