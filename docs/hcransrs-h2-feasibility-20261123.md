# H-CRAN protocol qualification boundary — 23 November 2026

## Evidence-based answer
- **OAI H-CRAN:** Significant bounded end-to-end successes, **NOT** complete protocol qualification. Two synthetic subscribers have performed registration, PDU session setup, bidirectional F1-U/NG-U and bounded traffic; Stage 4Z achieved one real RFsim macro→small→macro transition with PDU continuity. Stages 5Y/5ZF show **non-repeatable native post-AMF restart recovery**; no production promotion.
- **srsRAN H-CRAN:** [H1 #45113602402](https://github.com/rathod9771/5g-kubernetes/actions/runs/45113602402) PASS at SHA `4205b8747f0db84ad2eb387397917ee2608a146d`. One CU, macro DU100, small DU101, NG Setup and two independently captured F1 Setup request/response pairs, three CU SCTP peers, 103.73 s observation. **No UE or user-plane traffic in H1.**
- Keep the H1 success SHA, OAI Stage5ZG frozen SHA `7fa911ece3b510b09351bf19c722007365ba47bd`, `main`, the other C-RAN experiments, and all HP workloads untouched.

## H2 evidence barrier
The current canonical srsRAN H-CRAN DUs are **band n78 / TDD / 30 kHz**, macro 20 MHz and small 10 MHz, defined by `helm/cran-srsran/du/templates/du.yaml` and values-hcran profiles.

The documented prototype **5G SA** srsUE is limited to **15 kHz SCS / FDD / 5–20 MHz** and explicitly **does not support handover**:
https://docs.srsran.com/projects/project/en/latest/tutorials/source/srsUE/source/index.html

Consequently:
- Existing canonical 30-kHz n78 srsRAN: **H2 RRC/SA UE BLOCKED**, not tested.
- NAS security/Registration Accept and PDU Session Accept (H3): **NOT RUN**.
- Nonzero-TEID bidirectional F1-U/N3 GTP-U (H4): **NOT RUN**.
- Macro↔small UE handover (H5): **BLOCKED** with prototype srsUE. It is not valid to substitute PDU reconnect, RRC idle/reselection, or static F1 Setup.
- Lifecycle, QFI/5QI, recovery and independent fault repeats (H6–H8): **NOT RUN**.
- Current H1 loopback NG-U address `127.0.3.1` is **not suitable for N3 user-plane**. A future test must independently supply a routable N3 interface before H3/H4.

## Implemented now: offline H2 capability gate
`scripts/experiments/hcransrs_h2_feasibility.py` reads actual repository values and verifies the pinned n78/30-kHz template, DU IDs, and known srsUE limitations. It reports `BLOCKED_INCOMPATIBLE_UE`, `NOT_VALIDATED`, and `NO_GO` intentionally. Negative tests reject identity/template drift. The GitHub-only workflow `.github/workflows/hcransrs-h2-feasibility.yml` executes these checks with no RF peer, UE, subscriber creation, network trial, Kubernetes, or HP access.

**The code and a green CI are not evidence that any UE was built or connected.**

## Next scientifically valid executable stages
1. **H2 candidate review**: choose an independently pinned NR-SA TDD/30-kHz-capable UE with documented RF/ZMQ/IQ interoperability (or separately identify a 15-kHz FDD overlay). Check pinned source, licensing, build dependencies, RF sample-rate and port compatibility. Do **not** silently convert canonical n78 to FDD.
2. **H2 actual isolated RF test**: two independently addressed UE RF endpoints. Capture authentic MIB/SIB1, RRCSetup, RRCSetupComplete, AS security and F1 UE context, with exact per-cell timing; fail if unsupported or absent.
3. **H3**: disposable 5GC with two distinct synthetic subscribers; separate Registration Accept and PDU Session Establishment Accept, independent UE PDU IPs, no authentication secrets in artifacts.
4. **H4**: independent F1-U and N3 binary GTP-U UL/DL counts, actual nonzero TEIDs and ICMP/throughput receipts per UE. Separate N3 address mandatory.
5. **H5**: only with explicitly supported UE + pinned gNB mobility path, test true macro→small→macro RRCReconfigurationComplete and F1 context migration. If unsupported, mark BLOCKED; never claim a handover PASS.
6. **H6**: bounded, repeatable AMF and CU/DU faults with same UE processes, observed re-registration, fresh PDU, resumed traffic, rollback, positive/negative controls. Report failures.
7. Independent protocol/security review, resource limits and operational staging are separate from RFsim and CI and remain **NO-GO**.

### OAI recovery boundary
Evidence: `docs/hcran-production-qualification-runbook.md`, `docs/hcran-stage5ya-failed-recovery-evidence-audit-20261118.md`, and `docs/hcran-stage5zg-sib1-message-handoff.md` on frozen OAI branch. Initial registration/PDU/user-plane and one isolated mobility direction pair passed, but repeatable native AMF recovery has not. Stage 5ZG is a diagnostic investigation, not a recovery PASS.

No 3GPP certification, no production deployment and no HP system changes are asserted by this document.
