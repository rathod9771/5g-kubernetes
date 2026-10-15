# P6 controlled integration readiness

Overall: **BLOCKED for real build → registry → OSM integration**. Discovery and
isolated dashboard/onboarding validation completed. No infrastructure installation,
production restart, catalog write, workload update, commit or push occurred.

## Observed infrastructure

| Component | Evidence | Status |
|---|---|---|
| Isolated BuildKit worker | No buildkitd/buildctl process, named Docker builder or matching Kubernetes pod found in inspected inventory; no configured execution policy/BUILDKIT_HOST | BLOCKED: no approved endpoint or isolation evidence |
| BuildKit clients | buildctl and buildkitd not on PATH; Docker client exists | BLOCKED; host Docker is not a replacement worker |
| Research registry | No configured P5 OCI repository, scoped secret reference or dedicated registry pod found | BLOCKED; external image registries used by existing pods do not prove research write/read authorization |
| OSM server | Verified HTTPS public version GET reports `19.0.0+ged40146`; observed NBI/LCM/RO images are releasenineteen-daily | LIVE_VERIFIED version read only |
| OSM credentials | Owner-only runtime snapshot is readable; require=('osm',) rejects its credential under the non-default credential policy | BLOCKED; no default credential bypass or authentication attempted |
| Disposable OSM project | Runtime configured for admin; no P5 research target/credential binding found | UNVERIFIED existence/ownership; no project list was authorized with acceptable credentials |
| OSM CLI | Executable exists but --version and package-validate --help exit 1, ModuleNotFoundError: osm_im | UNUSABLE; not needed by Python Catalog, but local CLI validation cannot be claimed |
| Production dashboard | Active; runs original checkout; GET / and /api/scenarios both 200; /telecom 404 | Existing UI operational; P5 not activated |
| Production P5 environment | TELECOM_AUTH_FILE/RUNTIME_DIR/WORKSPACE/EXECUTION_POLICY absent in inspected service process | BLOCKED for production activation |
| Legacy watcher | systemctl reports inactive | Remained stopped |

Inventory covers configured/discoverable resources, not proof that no unadvertised
remote service exists. No network scan, Docker daemon bootstrap, secret dump,
cluster Secret read, or service reconfiguration was performed. The initial generic
config check failed before I/O. Reading the private service snapshot without the OSM
requirement permitted only a public, unauthenticated version GET; it did not bypass
the credential policy for an authenticated operation.

## Actual build and registry readiness

A real pinned local Git sample and unique KNF/NSD candidate were prepared under
`/tmp/telecom-p6-evidence`. Source commit:
`e888ae8e6b78aeb0d945ada6a7838c29042a0e59`.
Sample name: `p6-68104923c3a44c21`.

- KNF: `research-2c20e32a79499b2605cc-knf.tar.gz`
  SHA256 `f4c72c8310eb507e00e7fb00de939473f05b7d630062053e77eb43a3e5438d2e`.
- NSD: `research-2c20e32a79499b2605cc-ns.tar.gz`
  SHA256 `6c19ea4f3fc2cc491198dc74d699ab3bd30c75682caca5ae4ef474a0e03e81e0`.

Real local Git acquisition, OCI fixture byte validation, Helm lint/render,
reference checks and package generation passed. The fixture is an empty OCI image,
not functioning RAN/Core software. Registry and catalog transports in the sample
workflow were SIMULATED. Candidates were not published. NBI schema acceptance,
project collision check, owner isolation and remote package readback are UNVERIFIED.

No real Dockerfile build, registry authentication or registry readback occurred.
Minimum prerequisites: dedicated off-host rootless worker, mTLS identity/CA, buildctl,
verified CPU <= 1 core / memory <= 1 GiB / bounded disk and process count, 120-second
worker lease/client timeout, denied production/metadata reachability and no mounted
production kubeconfig/OSM credentials. A server-owned quota_evidence string alone
is not proof; inspect cgroups/lease enforcement and capture independent evidence.
Provide an allowlisted research OCI repository with purpose/destination/tenant-bound
registry credentials and readback permissions. Do not install these here by inference.

There is also a software gap: P5 exports/retains verified OCI archives but does not
push images to a registry. Even provisioning worker/registry cannot establish a
built-image registry roundtrip through current code. A separately reviewed image
publication step or explicitly approved off-host publication tool is required.
The existing catalog approval gate does not independently require built-image
registry readback. Operationally block publication until that evidence exists;
a verified package alone is not proof its image can be pulled.

## OSM v19 compatibility

The existing Catalog client and KNF/NSD generators are reused. The public version
endpoint is reachable with configured CA trust. Authentication, project-limited RBAC,
new package POST acceptance and exact content readback remain UNVERIFIED because
acceptable research-scoped credentials/project binding are absent.

The [official v19 deployment guide](https://osm.etsi.org/docs/user-guide/v19/how-to/nfv/deploying-network-services.html)
documents KDU ns-action upgrade/rollback separately from CHANGE_VNFPKG. The local
historical findings used a same-descriptor/no-diff control; they do not prove all
KDU upgrade mechanisms unsupported. No action was submitted here. Exact LCM/backend
behavior, payload, image/generation rollout checks, rollback compatibility and
telecom continuity remain NOT_VERIFIED. Instance execute remains hard-blocked.
Do not replace this with terminate/recreate or a timeout/limit bypass.

## Dashboard validation

Actual ephemeral loopback HTTP server using reviewed backend source: **25/25 checks**.
Covered page/script 200s, operator inventory, missing authentication 403, workload
registration, duplicate queue identity, cross-tenant 403, all eight read views,
missing approval 403, valid approval with disabled target 403, NOT_VERIFIED update
status and retired legacy mutation 423. The release history came from a real local
Git/Helm/OCI-fixture workflow, not a live build. Local audit-chain verification passed.

Research-only launcher: **6/6 HTTP checks**. Existing blueprint/UI served 200; missing
authentication returned 403; /api/deploy and /backend.py returned 404. No import of
backend.py or reference installer. Both test servers stopped and released sockets.
Existing JavaScript browser-script tests: **2 passed in 0.115 seconds**; these are
Node VM behavior checks, not a full graphical browser/visual certification.

Evidence: `/tmp/telecom-p6-evidence/results.json`, `research-only.json`,
`production-dashboard-reads.json`, `/tmp/telecom-p6-discovery.json`,
`/tmp/telecom-p6-osm-readiness.json`, `/tmp/telecom-p6-cli.json`,
`/tmp/telecom-p6-browser-tests.log`. Temporary identities/journals are isolated from
production and excluded from Git. No production assurance journal was opened.

Production activation requires its own approval, reviewed source promotion from the
CI/CD checkout, dependency verification, private P5 identity/policy/runtime settings,
operator tenant binding and dashboard-only rollback. New operator authentication
must be provisioned before switching code. Merely restarting the current unit would
not load this checkout's P5 source/configuration. No such restart was performed.

## Research-only installation mode and next steps

Use [P6-ONBOARDING.md](P6-ONBOARDING.md) and the tested
[p6/research_only.py](p6/research_only.py) loopback launcher. It uses existing telecom
components and does not run install.sh, install OAI/srsRAN/Open5GS, initialize a
cluster, import reference subscribers, onboard reference packages or start services.
This is an API/UI-only mode on an existing approved coordinator. A greenfield
platform installer with a general skip-reference flag does not currently exist;
do not invent that flag or run the reference installer as a substitute.

Remaining UNVERIFIED: real GitHub delivery/acquisition, worker isolation/quotas/lease,
actual Dockerfile build/cancellation, registry authorization/readback, OSM research
project RBAC/package acceptance/readback, full browser exposure, production dashboard
activation and live monitoring of an external NF. Incomplete features include image
registry push, private GitHub/LFS/submodules, multi-platform builds, distributed
fencing, automatic uncertain-outcome reconciliation and instance upgrade/rollback.
