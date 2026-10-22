# Release pipeline (P2): candidates, not deployments

The `Release Candidate (original orchestrator)` workflow turns a pushed commit into
an immutable release candidate for the original RAN/Core packages. It **never
deploys**: it has no OSM or Kubernetes credentials, uses GitHub-hosted runners only,
and never decides which RAN is active. The target-side agent (P3+) makes that
decision under the shared lifecycle lock from P1.

## Flow

```
push (reproducible-installer, feature/**) / pull_request / workflow_dispatch
  └─ build job (ubuntu-latest, contents: read)
       checkout exact ${{ github.sha }}; verify HEAD == GITHUB_SHA
       full offline regression suite
       scripts/build_release.py
         change impact (registry-resolved) → affected RAN scenarios / core components
         osm_packages.build() for affected, ready entries (deterministic bytes)
         image evidence (+ anonymous Docker Hub digest/platform verification)
         release-manifest.json + SHA256SUMS
       release_manifest.py publication-decision (fails closed)
         verifies the written candidate as a consumer would and recomputes
         eligibility from evidence; output publication_allowed=true only on exit 0
       upload artifact (not for pull requests)
  └─ attest job (push to reproducible-installer only, packages > 0,
     publication_allowed == 'true')
       id-token/attestations permissions only here; runs no repository code
       inline re-check of the downloaded manifest (unblocked decision, commit, branch)
       actions/attest-build-provenance over manifest, packages, provenance
```

A candidate with any blocker (blocked scenario, missing, local-only or unverified
image digests, failed CI, non-release channel) is uploaded for inspection but never
attested, so P3 cannot even find a signature for it.

Channels: `release` (push to `reproducible-installer`), `development` (other branch
pushes), `pull-request`, `manual` (workflow_dispatch), `local`. Only `release` can
ever be publication-eligible.

## Change impact

`scripts/release_impact.py` resolves each changed path (`git diff --no-renames`
between exact commits) through `config/scenarios.json`:

| Path class | Effect |
|---|---|
| chart source directory, value profile, reviewed legacy package | every registry entry using it (shared charts → all users) |
| `config/scenarios.json` | entry-level diff; any other top-level change → full rebuild |
| `config/reference-versions.json` | changed `ran_images` components → entries binding them; anything else → full rebuild |
| package generator (`osm_packages.py`, `scenario_registry.py`, `local_safety.py`, `runtime_images.py`) | full rebuild |
| srsRAN build definitions | every entry binding `srsran` |
| researcher CI/CD (`scripts/telecom/`, `tests/test_telecom_*`, …) | ignored, explicitly classified |
| dashboard, installer, lifecycle, docs, tests, workflows, unregistered Helm charts, legacy archives | no package impact, explicitly classified |
| anything else, or no comparable base commit | **full rebuild** |

A test requires every tracked file to have an explicit class, and another checks
that every input `osm_packages` hashes into package provenance maps back to its
entry, so new inputs cannot be silently omitted.

## Release manifest (`release-manifest.json`, schema `5g-kubernetes/release-manifest` v1)

| Field | Content |
|---|---|
| `schema`, `schema_version` | format identity |
| `repository` | `owner/repo` from the GitHub runtime (`local` outside Actions) |
| `source` | full `commit`, `ref`, `branch`, `event`, `base_commit`, `tree_clean` |
| `workflow` | `path`, `ref` (`owner/repo/path@ref`), `run_id`, `run_attempt`, `runner_environment` |
| `built_at` | UTC build time (the only field that differs between rebuilds) |
| `channel` | see above |
| `change_impact` | changed paths with classes, `ran_scenarios`, `core_components`, `blocked`, `buildable`, full-rebuild reasons |
| `ci_validation` | `passed` only when the workflow ran the suite before this step |
| `images` | per component: repository, tag, digest, platform, acquisition, `status`, `build`, `registry_verification`; vendored chart images for core |
| `packages` | per scenario: kind (`ran`/`core`), KNF/NSD `descriptor_id`, artifact path, SHA-256, size, provenance |
| `artifacts` | exact index of every artifact file → SHA-256 |
| `target` | `architecture` (`linux/amd64`), `environment` (`hp-lab`) |
| `osm_revision_binding` | descriptor ids stay the registry ids; OSM assigns revisions on publication |
| `eligibility` | `publication_allowed`, `automatic_deployment_allowed` (always `false` in P2), `provenance` (`UNVERIFIED`), `blockers` |

Package bytes are produced by the existing `osm_packages.build()` and are
byte-identical across rebuilds of the same commit and toolchain.

### OSM revision association

Descriptor ids are never versioned (OSM v19 keeps revisions under one id and
rejects in-use descriptor changes). The future agent publishes package bytes, reads
back the new OSM revision, and records `(descriptor id, revision, package sha256)`
in its ledger; P1's `active_release.knf_revision` and Helm revisions record what is
running.

## Trust model

The manifest is a claim. `release_manifest.verify()` checks schema, repository,
commit, ref/branch, workflow identity, package coverage, artifact bytes, image
evidence and the absence of credential material, and recomputes eligibility
instead of trusting it. Provenance becomes `VERIFIED` only through an injected
attestation verifier that proves the manifest digest was attested by
`.github/workflows/release-candidate.yml` on the approved ref and commit from a
GitHub-hosted runner. `lifecycle_release()` refuses anything not VERIFIED and
publication-eligible, and a verdict is bound to the SHA-256 of the manifest it verified.

**Status:** attestations are generated in CI (Sigstore via GitHub). Target-side
verification is P3: see [release-verification.md](release-verification.md)
(`scripts/release_verify.py`, pinned `gh` 2.101.0). Until a candidate passes it,
it is `UNVERIFIED` and ineligible.

## Images

| Component | Source | P2 status |
|---|---|---|
| `oai-cu`, `oai-du` | Docker Hub, tags `2026.w13`/`2026.w25` | `MISSING_DIGEST` → blocks eligibility |
| `srsran` | pinned source build imported into containerd (`localhost/…`, `Never`) | `LOCAL_ONLY`, build `UNSUPPORTED` |
| `fran-edge`, `ue-discovery` | Docker Hub, pinned digests | `PINNED_DIGEST`; CI verifies digest and platform anonymously |
| Open5GS core | vendored chart, `gradiant/*` tags | `MUTABLE_TAG` |

No image is built or pushed. Nothing is invented: unknown digests stay `null`.

## Prerequisites for a future optional deployment agent (reproducible installer)

Not implemented. The installer is unchanged and must stay usable without CI/CD.

1. A separate, opt-in installer entry point (not a stage of the full `run()`,
   because rerunning the installer re-onboards packages), disabled by default.
2. Configuration: release repository, approved branch, signer workflow path, target
   environment id, upgrade allowlist, OSM project user (non-admin) and password
   file; no GitHub credentials are required for a public repository.
3. An attestation verifier on the target (`gh` ≥ 2.49 or a pinned Sigstore
   verifier), validated against a real attested candidate.
4. The agent runs as the user owning `RUNTIME_DIR` so it shares
   `.lifecycle.lock` and `active-ran.yaml` with the dashboard.
5. The dashboard's catalog check must accept verified release packages (P1
   review D1) before any publication.
6. Image prerequisites: OAI digests pinned in `config/reference-versions.json`;
   srsRAN published to an approved registry with a digest, or kept out of
   automatic releases; core chart images pinned.
7. No self-hosted runner registration; the agent pulls artifacts outbound only.
8. P4 approval. `scripts/lifecycle.py` ships with `P4_KDU_UPGRADE_APPROVAL = None`:
   the controller, its `_apply` step and the OSM adapter's `kdu_action` all refuse
   in-place KDU upgrades (and rollbacks), whatever `upgrade_scenarios`, configuration,
   environment or state say. An affected active RAN gets `approval-required` after
   catalog-only publication. Opening the gate is a reviewed commit recording the P4
   report digest, the validated OSM LCM image digest and the approving commit.
