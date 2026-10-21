# Trusted release verification (P3): verify, never deploy

`scripts/release_verify.py` runs on the HP target and decides, independently of
anything the release claims about itself, whether a GitHub release candidate from
`.github/workflows/release-candidate.yml` is trustworthy. A `VERIFIED` result is a
**publication candidate** whose identity is recorded for later lifecycle
processing. It is not an authorization: the result always carries
`deployment_authorized: false`, and nothing in P3 contacts OSM, Kubernetes or the
dashboard, takes the lifecycle lock, or reads or writes the active-RAN record.

## Flow

```
operator: release_verify.py --run-id N --attempt A --artifact-zip|--artifact-dir|--download
  1. pinned verifier check     gh binary: exact SHA-256 + version, owner-only writable   (before any network)
  2. GitHub run record          anonymous API: run attempt, repository + owner ids, workflow path,
                                event=push, branch=reproducible-installer, completed/success,
                                build+attest jobs succeeded on GitHub-hosted runners,
                                exactly one artifact release-candidate-<sha>-<attempt>
  3. artifact bytes             zip digest == GitHub's recorded artifact digest (zip/download);
                                safe extraction; exact layout; SHA256SUMS covers exactly the files
  4. manifest identity          repository, commit, branch/ref, workflow, run id + attempt, channel
  5. release policy             recomputed from evidence (below), never from eligibility fields
  6. attestation                bundles fetched anonymously for the manifest digest, verified by
                                Sigstore through the pinned gh, then re-checked field by field;
                                attested subjects == every downloaded file (name and digest)
  7. P2 + P1 contracts          release_manifest.verify() with the attestation proof,
                                lifecycle_release() and lifecycle.verify_release()
  8. record                     under .release-verify.lock: duplicate/replay checks, private
                                content-addressed copy of the verified bytes, verified-releases.json
```

Every failure is a `REJECTED` result with a stable code; unexpected errors are also
rejections. Exit status: 0 verified, 2 rejected.

## Trust model

| Claim | Established by |
|---|---|
| Built by this repository | GitHub run record (repository **and numeric id/owner id**, so a renamed or recreated repository cannot replay) and the Sigstore certificate (`sourceRepositoryURI`, `sourceRepositoryIdentifier`, `sourceRepositoryOwnerIdentifier`) |
| From the approved branch and commit | run `head_branch`/`head_sha`, certificate `sourceRepositoryRef`/`sourceRepositoryDigest`, manifest `source` |
| By the release workflow at that commit | run `path`, certificate `buildSignerURI`/`subjectAlternativeName` and `buildSignerDigest == commit` |
| In this run attempt | run attempt record, certificate `runInvocationURI` (`/runs/<id>/attempts/<n>`), manifest `workflow` |
| On a GitHub-hosted runner, push-triggered | job labels/runner group, certificate `runnerEnvironment`/`buildTrigger`, `--deny-self-hosted-runners` |
| CI passed | run conclusion, every job `success` (a skipped `attest` job rejects), manifest `ci_validation` |
| These exact bytes | GitHub artifact digest, SHA256SUMS, attestation subjects (all files), P2 artifact index and P1 digests |
| Signed by GitHub's Sigstore identity | `gh attestation verify --bundle` with issuer `https://token.actions.githubusercontent.com` and predicate `https://slsa.dev/provenance/v1`, using Sigstore's public-good trust root (TUF) or an explicit `--custom-trusted-root` |

The verifier's exit status alone is never trusted: every identity field above is
compared again in `release_attestation.binding()`, and exactly one verified
attestation must exist for the manifest.

**Recomputed release policy** (manifest eligibility fields are ignored except that
`automatic_deployment_allowed` must be `false` and a builder-recorded blocker is
respected):

- channel `release`, event `push`; development, pull-request and manual builds are rejected;
- at least one package; no blocked scenario in the impact; every packaged scenario `ready` in the registry;
- target exactly `{environment: hp-lab, architecture: linux/amd64}`;
- every image of every packaged scenario (bound components and vendored chart images):
  not local-only (`LOCAL_ONLY`, `localhost/…`, non-portable), pinned by `sha256` digest,
  `registry_verification: VERIFIED` in CI, and re-checked anonymously against Docker Hub
  from the target (`recheck_registry`, fail-closed).

### Compatibility with the P2 workflow

- The artifact is `${{ runner.temp }}/release/` uploaded as
  `release-candidate-<sha>-<run_attempt>`: the zip root holds `release-manifest.json`,
  `SHA256SUMS`, `packages/*.tar.gz` and `provenance/*.provenance.json`.
- `actions/attest-build-provenance` v2.4.0 (`actions/attest` v2.4.0) creates **one**
  SLSA statement for all `subject-path` files and names each subject by its
  **basename**; SHA256SUMS is not a subject. `expected_subjects()` reproduces exactly that.
- The attest job downloads the artifact of its own `run_attempt`. Re-running only a
  failed `attest` job therefore cannot attest an earlier attempt's artifact (it finds
  no artifact and fails); use "Re-run all jobs". A manifest from attempt N with a
  certificate from attempt M is rejected.
- GitHub's attestation API returns every attestation for a digest (for example GitHub
  release attestations alongside SLSA provenance); `gh` is restricted to the SLSA
  predicate and exactly one verified result must remain.
- Recorded real responses (the P2 manual run, a public cli/cli attestation listing,
  its bundles and `gh` 2.101.0 output) are offline fixtures in
  `tests/fixtures/release_verify/` (signed storage URLs and the commit email removed).

## Verifier and version

GitHub CLI **2.101.0** (released 2026-09-15), pinned in
`config/release-verification.json` by version and by the SHA-256 of the `gh` binary
(`ea857a3f…4bff`); the official archive `gh_2.101.0_linux_amd64.tar.gz` is
`9bca2d1c…72b8` (from the release's checksums file). The local `gh` 2.45 has no
`attestation` command and is never used: the binary path is passed explicitly
(`--gh`) and never looked up on `PATH`.

The verifier is **not installed** by this change. An operator installs it once,
explicitly, for example:

```
gh release download v2.101.0 -R cli/cli -p gh_2.101.0_linux_amd64.tar.gz -p gh_2.101.0_checksums.txt
sha256sum -c --ignore-missing gh_2.101.0_checksums.txt      # and compare with the policy
sudo install -o root -g root -m 0755 -D gh_2.101.0_linux_amd64/bin/gh /opt/5g-kubernetes/tools/gh-2.101.0/gh
```

`gh` runs with an empty private HOME/config, no GitHub token and `PATH=/usr/bin:/bin`.
Verification needs no GitHub credentials: run metadata and attestation bundles of
a public repository are fetched anonymously (bundles are raw Snappy from
`*.blob.core.windows.net`, decoded with size bounds). Sigstore's trust root is
fetched by `gh` over TUF unless `--trusted-root` is given.

## Artifact sources

| Source | Binding to the run |
|---|---|
| `--artifact-zip` (e.g. `gh api …/actions/artifacts/<id>/zip`) | zip SHA-256 must equal GitHub's recorded artifact digest, then the attestation |
| `--artifact-dir` (e.g. `gh run download`) | the attestation and SHA256SUMS only (reported as `archive_digest_checked: false`) |
| `--download --github-token-file F` | GitHub requires a token for artifact zips even on public repositories. The token file must be a private regular file owned by the caller; the token is sent only to `api.github.com`, redirects are followed manually and the signed storage URL is fetched without it. It is never printed. |

## Limits

HTTPS only to `api.github.com`, `*.blob.core.windows.net` and
`*.actions.githubusercontent.com`; 30 s per request; API responses 2 MiB; bundles 1 MiB
compressed / 4 MiB decoded; at most 5 attestations per artifact; archive 128 MiB;
extraction 64 MiB and 256 files, rejecting traversal, absolute paths, links,
duplicates and encrypted entries; verifier 120 s and 4 MiB output.

## State (in the chosen `--state-dir`, normally `RUNTIME_DIR`)

- `verified-releases.json`: append-only (bounded) records with the full verified identity
  (repository/id, branch, commit, workflow, run id/attempt, artifact id/digest, manifest
  SHA-256, packages with descriptor ids and digests, target, verifier, attestation signer
  and timestamps), each `deployment_authorized: false`.
- `verified-releases/<manifest-sha256>/`: private copy of exactly the verified bytes plus
  the attestation bundle and verifier output.
- `.release-verify.lock`: serializes recording only (not the lifecycle lock).

Duplicates (same manifest) and replays (another manifest for a recorded run attempt,
or a run older than the newest verified one) are rejected and not recorded.
`load_verified()` re-hashes a stored release and returns the P1 lifecycle input
(`lifecycle_release()` + `lifecycle.verify_release()`) for a future, separately
authorized lifecycle step. No worker calls it in P3.

## Not in P3

Deployment authorization, any OSM publication, the dashboard catalog check against
verified releases (P1 review D1), installation of the verifier, and branch
protection for `reproducible-installer` (a prerequisite before attested releases can
be trusted, because anyone with push access can trigger an attested build).
