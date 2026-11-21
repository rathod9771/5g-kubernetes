# Phase 1B2 — C-RAN srsRAN real N2/F1-C signaling (research)

Predecessor: [Phase 1B1 C-RAN OAI](https://github.com/rathod9771/5g-kubernetes/actions/runs/44883328203) passed one NR UE, NAS/PDU and bidirectional G-PDUs. Preserve its source d9596ad755b4cb6d81bdd4355a63b8164a740786. This separate phase qualifies only srsRAN signaling.

## Build and execution

- Source: repository's approved fork herlesupreeth/srsRAN_Project, exact commit 11c9bbabb69873752500d676f55e0034f6caa5c5, v25.04.0.
- Docker-only source build of srscu and srsdu, 2 compile workers, ZeroMQ ON, UHD/DPDK OFF; Ubuntu 24.04 base digest resolved and recorded; package versions and actual binary/image hashes recorded. A research build, not a byte-identical reproduction of a prior production image or an immutable package lock.
- This intentionally does NOT call scripts/build_srsran_from_source.sh, which changes host Docker services and Kubernetes containerd. Does not use the unrelated guarded native NGAP build workflow.
- Actual repository C-RAN CU/DU Helm charts with test-only explicit radio identifiers; PLMN 20893, TAC 1, slice SST1/SD010203. Optional F1-U socket overlay disabled for this N2/F1-C-only test; no inference about default user-plane paths. DU's ZeroMQ driver is configured but NO virtual UE peer is started.
- Frozen isolated Open5GS NRF/AMF test configs, no subscriber DB, no UE, no physical radio. Test runs on GitHub-hosted Ubuntu 24.04 with internal Docker bridge. No HP, OSM, Kubernetes deployment, image publication or default-branch changes.
- Observe actual SCTP packets on the dedicated bridge; Wireshark tshark filters require NGSetupRequest and NGSetupResponse on CU/AMF directions, F1SetupRequest and F1SetupResponse on DU/CU directions, valid frame order, no decoded setup failure. Require live CU/DU/AMF/NRF at end and both CU SCTP peers. Retain capture hash, counts, first frame numbers, provenance, and no raw PCAP/configs. This is one runtime smoke, not formal 3GPP conformance.

## Acceptance

Only a successful manually dispatched runtime job with receipt `NGAP_F1AP_WIRE_PASS_UE_NOT_TESTED` qualifies first signaling. Push CI checks config/schema and rejection behavior, never builds or starts real processes. Source build is manual only, after same-branch, same-SHA full offline success. Missing compiled binaries, missing decoded setup messages, peer/liveness failures, or capture errors mean FAIL. No UE registration, PDU establishment, F1-U/NG-U traffic, handover or recovery claims even if this gate passes.

A separate NR virtual-radio compatibility study is still needed: srsRAN Project uses ZMQ IQ transport, which is not OAI RFsim. A legacy srsUE version cannot be assumed to support this 5G SA band/numerology/configuration until tested.

## Manual dispatch

After exact-HEAD full Offline Project Validation and focused contracts are SUCCESS:

```bash
gh workflow run phase1b2-cran-srsran-ngf1-manual.yml \
  --repo rathod9771/5g-kubernetes \
  --ref experiment/phase1b2-cran-srsran-ngf1-20261121 \
  -f offline_run_id='<successful exact-branch full offline run ID>' \
  -f confirm_isolated_no_deploy=ISOLATED_SRSCU_SRSDU_NGF1_NO_DEPLOY
```

No duplicate manual executions. All independent study source/evidence stays separate from main and HP. Tracking: issue #16. 
