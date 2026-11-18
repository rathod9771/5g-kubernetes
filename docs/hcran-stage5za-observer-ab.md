# Stage 5ZA — bounded observer-on/off RFsim A/B trial

**Scope:** disposable GitHub Actions OAI RFsim / Open5GS synthetic core, with the same
pinned Stage5T research UE and pinned Stage3 CU recovery implementation.
**Decision:** research-only / NO DEPLOY / production NO-GO.

## Why this comparison is necessary

Stage5Y-A run 44234327537 had two initial recovery failures: the macro UE kept an
old PDU address and could not return UPF ICMP despite successful NGSetup. On the
later Stage5Z run 44248005405, both independent three-cycle sessions passed,
including macro and small AMF cell-ingress gates. The underlying UE/CU recovery
code remained pinned, but Stage5Z added a CU-selection-category log observer.
This is evidence of an intermittent behavior, not proof the defect is fixed.

## Intervention and experimental design

Four separate GitHub-hosted RFsim sessions:
- two with `STAGE5ZA_CU_OBSERVER=on` (the Stage5Z selection-category observer);
- two with `STAGE5ZA_CU_OBSERVER=off` (only the optional CU selection category
  classifier is disabled; the baseline Stage5R collector, Stage5Y AMF-file
  ingress parser, NAS/PDU/ICMP/GTP-U checks, and the original negative controls
  remain enabled).

Each session attempts three consecutive **genuine** AMF stop/restart cycles
with **two unchanged synthetic UE processes** and both H-CRAN DUs. Four sessions
x three attempts = 12 planned cycles. GitHub matrix jobs use distinct artifact
names and fail-fast is disabled, so every result is retained even on failure.
`on/off` is an observer-mode comparison, not a protocol behavior modification.

The Stage5Z phase report explicitly labels `CONTROL_DIAGNOSTIC_ONLY` when
the optional CU selection observer is off; missing selection categories in that
arm are expected and must never be misread as failed UE signaling. Real Stage5P,
Stage5U and Stage5Y acceptance remains unchanged and fail-closed in both arms.

## Prespecified analysis and limitations

Report results **per independent session** (not each AMF cycle as an independent
sample). Include all four planned jobs, failures, incomplete jobs and successes.
For each job report 0-3 verified cycles; count negative controls and per-cell
AMF ingress only for genuine successful cycles. Compare how often recovery
passes per session under observer on/off; also compare per-cycle durations,
but no p-values or statistical significance claims with n=2 per arm.

A discrepancy does **not** by itself prove the observer caused or cured a race.
Both arms still use a log collector and can be affected by runner scheduling,
container startup timing or RFsim nondeterminism. Escalate to a targeted
timestamped UE/DU/CU/NGAP/AMF transition trace on repeatable failure,
rather than increasing retry budgets or replacing real evidence with synthetic
success markers.

## Safety boundaries

- No change to pinned UE/CU/RAN or Open5GS protocol behavior.
- No main-branch, reproducible installer, OSM, HP Kubernetes or production
  subscriber operation.
- No Near-RT RIC or E2SM-KPM in H-CRAN; Prometheus/Grafana monitoring unchanged.
- No production promotion. Staging needs separate operator authorization,
  negative cases, resource controls and rollback validation.

## Source of Stage5Z successful evidence

Live PASS: https://github.com/rathod9771/5g-kubernetes/actions/runs/44248005405

All 6/6 actual recovery cycles passed and all 18 negative controls rejected in
a separately replayed archive audit. Earlier Stage5Y-A failed jobs remain part
of reliability history, not excluded as inconvenient outliers.
