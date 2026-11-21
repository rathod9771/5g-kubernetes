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

## Packet-observer startup incident and safe diagnostic

First manually dispatched experiment [#44890511873](https://github.com/rathod9771/5g-kubernetes/actions/runs/44890511873) **failed, at `START_CAPTURE`**, because the TShark observer exited within three seconds **before any AMF, CU or DU began**. Both `srscu` and `srsdu` had already built successfully from pinned source. This is *not* evidence of an srsRAN NGAP/F1AP protocol failure. The exact TShark stderr was private to the disposable runner and was not uploaded, so the root cause could not be proven from that run.

The correction adds `phase1b2_capture_preflight.py`: an **8-second capture-startup check** on a temporary Docker `--internal` bridge (same interface name, SCTP filter, capture flags and short duration), with no subscribers, 5GC, CU, DU, UE or traffic. It checks Linux bridge existence, process survival and a readable PCAP, then removes that one disposable bridge. All raw logs and PCAPs stay runner-local. Only `observer-preflight-receipt.json` is retained, with fixed failure categories rather than raw stdout/stderr. Successful preflight means `CAPTURE_READY_NO_PROTOCOL_TESTED`, *never* `NGAP_F1AP_WIRE_PASS`.

There are **two gates**: a cheap push-triggered `observer-preflight` job, and the exact same preflight at the beginning of any manually approved runtime job **before the 100-minute srsRAN Docker compile**. If the observer fails, the build never runs. A failed preflight should be diagnosed through its categorical receipt; do not retry the prior expensive build blindly or broaden capture permissions beyond the isolated GitHub runner. A fresh full offline success for the new exact HEAD is required before another manual runtime dispatch.

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


## Capture-engine correction after bounded probe

The low-cost GitHub-hosted observer probe [#44893007084](https://github.com/rathod9771/5g-kubernetes/actions/runs/44893007084) **reproduced** the pre-workload exit with `error_category=PERMISSION_DENIED`, `bridge_present=true`, `observer_returncode=1`. This does not yet distinguish capture-device permission from capture-output-file permission.

The next experiment uses **`dumpcap` as the privileged *capture-only* process** instead of launching the full TShark analyzer as root. It retains unprivileged `tshark -r` strictly for decoding the saved PCAP. The identical `dumpcap` command is checked on a disposable bridge before any srsRAN source build; a failed probe blocks the expensive runtime. Both processes use the runner's short-lived local filesystem, no live UE or 5G network on push, and no upload of raw captures. A second `PERMISSION_DENIED` is a **BLOCKED** result requiring further investigation, not a reason to bypass the observer.


## Capturing through a caller-owned file descriptor (second diagnostic correction)

The short observer [run #44893245512](https://github.com/rathod9771/5g-kubernetes/actions/runs/44893245512) reported an *initially misclassified* `CAPTURE_OUTPUT_DENIED` using `sudo -n dumpcap -w <private-output-file>`. The bridge was present but dumpcap returned exit 1 before the RAN workloads could start. The classifier incorrectly matched the substring `pcap` inside the tool name `dumpcap`; the directly supported finding is only `PERMISSION_DENIED` while the bridge was present. Capture output access remains a hypothesis, not a proven root cause.

The next small patch makes the **unprivileged GitHub runner open the output PCAP itself** and passes that already-open file descriptor as `dumpcap`'s stdout with `-w -`. Wireshark's current dumpcap manual supports `-w -` for capture to stdout. The packet filter, fixed-duration autostop and capture-file size condition remain enforced. The dedicated capture binary no longer needs direct write access to a runner-private directory. TShark only reads the resulting file after capture. The exact same mechanism is exercised by the cheap bridge preflight and the later live NGAP/F1AP trial. Until probe and exact-source offline validation pass, source-build retry remains **NO-GO**.


## Diagnostic classification regression (corrective note)

The diagnostic categorizer was corrected to match **word-bounded** output-file and interface terms, not substrings inside `dumpcap`. The additional negative unit test enforces `dumpcap: Permission denied → PERMISSION_DENIED`; only a message explicitly mentioning a file path/write/output is `CAPTURE_OUTPUT_DENIED`. Thus the stdout-capture adjustment is an isolated test of a plausible cause, not a statement that the original root cause was established. A further failed capture probe remains BLOCKED even if source-build unit tests pass.
