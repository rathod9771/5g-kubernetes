# Experimental OAI NGAP 2026.w13 CI handoff

This is an isolated **review and compile** experiment. Do not merge or use it as a
deployment or production-readiness signal. Its base branch is
`p5/dell-cd-safety-foundation`; the user's uncommitted local
`feature/hp-p4-p5-cicd-integration` work has not been changed.

## Contents

- `experiments/oai-ngap-2026w13/ngap-retry-v2.patch` — reproducible V2
  candidate generated against the upstream `2026.w13` source.
- `.github/workflows/oai-ngap-2026w13-build.yml` — hosted-runner patch check,
  dependency install and RF-simulator gNB compilation.
- This document — gate and artifact interpretation.

## Trigger and scope

The workflow has a **push** trigger restricted to
`experiment/oai-ngap-2026w13-actions` and only when its workflow or experiment
files change. Thus, the workflow commit triggers one hosted run if Actions is
enabled and policy permits it. The `workflow_dispatch` trigger is included for
future use, but GitHub often requires a workflow to exist on the repository
default branch for manual dispatch. Until then, use the push-triggered run
or the Actions UI's **Re-run jobs** option on that run.

The runner is `ubuntu-24.04`, job timeout 90 minutes, build concurrency `-j2`.
It checks out `openairinterface5g` at `2026.w13`, checks/applies the patch,
runs upstream `cmake_targets/build_oai -I -w SIMU`, then
`build_oai --gNB -w SIMU --ninja --build-tool-opt "-j2"`.
This may fail if GitHub-hosted runner CPU, memory, disk, package installation,
GitHub Actions allowance, or upstream dependency availability is insufficient.
Never interpret a source-only patch check as a successful build.

Logs and provenance are uploaded as a 7-day Actions artifact even after failed
steps (when the upload step executes). The compiled binary is only hashed;
**no image is built/pushed, no deployment is performed, and no secrets are
required**. No code is pushed from CI (token has read-only contents permission).

## Acceptance gate (still required)

1. CI patch check succeeds against the exact upstream tag.
2. Full compile succeeds (not merely dependency installation).
3. Review retry-timer lifecycle, pending/associated AMF accounting, and
   duplicate/re-entrant retry behavior.
4. In a separate controlled environment test: unreachable initial SCTP,
   repeated failures, recovery after AMF becomes available, subsequent AMF
   restart, NG Setup response, and preservation of CU-DU F1-C.
5. Validate UE NAS/PDU sessions and F1-U separately.
6. Only then consider a separately versioned *experimental* image and
   controlled rollout with rollback. Do not replace the currently running
   `hcran-oai-cu` or modify the live dashboard as part of this workflow.

Do not combine the current candidate with unreviewed local P4/P5 changes or
push to `main` / `reproducible-installer`.
