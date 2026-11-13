# Stage 4V — effective E2SM-RC action: evidence contract (NO DEPLOY)

## Result scope

The Stage 4S KPM experiment proved actual E2SM-KPM indications from two live
synthetic OAI UEs on macro DU 100 and small DU 101 (run 43223244340).
Stage 4I exercised only KPM and RC **monitoring** xApps. Neither shows that
an RC request was transmitted, acknowledged, executed, independently measured
or reverted. RC runtime acceptance therefore remains **NOT_VERIFIED**.

Stage 4V adds a *read-only structural evidence validator* and adversarial CI
tests. The validator never contacts RIC, E2, Kubernetes, OSM or Open5GS and
never authorizes a production deployment. The successful CI test for its
contract must never be interpreted as successful runtime RC control.

## Required follow-on isolated runtime experiment

1. Establish an explicit supported RC control action from the **pinned** OAI
   2026.w13 CU agent and matching FlexRIC E2SM-RC implementation. Determine
   RAN function ID, control style ID, action ID, target and ASN.1 definition
   by inspecting source. Do not manufacture a successful result by assuming
   the monitoring xApp or agent supports a requested action.
2. Use two **disposable synthetic** subscribers and the proven Stage 4S
   dual-DU/dual-UE topology. Keep all HP kubeconfigs, OSM tokens and real
   subscriber credentials absent from the runner. The control target must
   be isolated to the disposable test environment.
3. Record pre-action baseline with a **separate read-only observer**,
   monotonically ordered time stamps, sufficient samples and a specific
   measurable RAN state/traffic metric that the action should affect.
4. Capture real E2AP SCTP traffic and decode the matching
   RICcontrolRequest -> RICcontrolAcknowledge (or explicit Failure)
   with correlated RIC request identifiers. A successful subscription,
   service-model advertisement or a log string does NOT count.
5. Check post-control readback: direction, minimum measurable delta,
   sample adequacy and continued macro/small F1-C, both UE PDU sessions,
   and both-direction user-plane traffic.
6. Explicitly reverse the action and verify a second correlated
   RICcontrolRequest / RICcontrolAcknowledge plus return of the independent
   metric to its specified tolerance. Abort/cleanup even if an intermediate
   step fails. Preserve checksums for E2AP pcap, independent readback and
   UE traffic capture; keep SIM credentials out of uploaded artifacts.
7. Execute multiple bounded cycles, including rejected/unsupported control
   attempts, and record safe failure handling. Validate real packet bytes
   and independent-observer raw measurements before updating any readiness
   report. Human reviewer approval and staging gates remain separate.

## Contract behavior

The validator in `experiments/oai-ngap-2026w13/stage4v/rc_effect_contract.py`
accepts a JSON evidence manifest and checks for all required fields,
correlated request IDs, ordered wire frames, an independent RAN readback,
nonzero effect, successfully observed reversal, live UE continuity and
safety boundaries.

Example invocation when actual separately reviewed evidence exists:

```sh
python3 experiments/oai-ngap-2026w13/stage4v/rc_effect_contract.py \
  /path/to/rc-runtime-evidence-manifest.json \
  --report /tmp/stage4v-contract-report.json
```

Even when all values are internally consistent, output is
`STRUCTURE_VALID_ONLY` and `real_rc_effect_verified=false`.
A JSON manifest by itself is self-reported metadata: the validator does
not decode a pcap or independently authenticate recorded measurements.
The runtime gate therefore remains NOT_VERIFIED.

CI uses fake data strictly inside adversarial unit tests; it tests rejection
of monitoring-only logs, unmatched acknowledgements, missing control effect,
missing reversal, missing independent observation, missing UE service and
accidental contact with the live HP environment. Fake evidence must never be
submitted as successful runtime proof.

## Release status

- E2SM-KPM actual dual-UE indications: PASS (Stage 4S).
- Stage 4V offline RC evidence contract: contract tests pending CI results;
  never an E2SM-RC action proof.
- Effective, reversible RC behavior in isolated runtime: NOT_VERIFIED.
- Native UE self-recovery after AMF outage: NOT_VERIFIED (Stage 4U only
  demonstrated supervisor-assisted disposable UE restarts).
- Real staging OSM upgrade/rollback: NOT_VERIFIED.
- Production authorization: **NO-GO**. Live HP H-CRAN remains unchanged.
