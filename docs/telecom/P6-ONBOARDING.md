# P6 researcher walkthrough and minimum next-test commands

Current status: BLOCKED for real external integrations. No backend.py edit is
required to register another provider. The existing schema-v1 declaration supports
RAN/Core roles, source or immutable image, interfaces and monitoring declarations.
Examples in examples/telecom are fixtures, not live-certified telecom software.

## Prerequisites requiring operator action / separate approval

Provision an isolated off-host worker and matching buildctl, bounded CPU/memory/
disk/process/lease enforcement and mTLS; a research registry and scoped credentials;
a disposable OSM v19 research project `research-<tenant>` with non-default,
least-privilege credentials. Keep production Kubernetes/OSM credentials off the
worker. Use server-owned policy files and secret references as described in
[P5-LIVE-TEST.md](P5-LIVE-TEST.md). No infrastructure was installed in P6.

The production admin project/credential is not a research target. Do not change its
password or permissions without separate authorization. Repairing the optional OSM
CLI's missing osm_im dependency also needs installation approval. Python Catalog
integration can be tested without that CLI once acceptable research credentials exist.

For a **prebuilt-image** test, supply a real single-platform OCI digest already in
the approved research repository. For a Dockerfile test, replace fixture declarations
with reviewed source and an actual repository identity. Current code exports OCI
rather than publishing it: image push/readback needs a separately approved tool or
implementation. Stop before catalog publication if image availability is unverified.

## Research-only mode: excludes reference workloads

Existing Python dependencies are already present on this inspected coordinator.
On another host, dependency installation requires its own approval. Do not invoke
install.sh: that installer imports reference images/subscribers and prepares Core/
RAN packages; it has no general research-only/skip-reference flag.

After operator-provisioned private files exist, the minimum isolated launch is:

```sh
cd /home/rclab/5g-kubernetes-cicd
export PYTHONPATH="$PWD/scripts"
export HELM_BIN=/usr/local/bin/helm.real
export TELECOM_AUTH_FILE=/PRIVATE/p6/auth.json
export TELECOM_RUNTIME_DIR=/PRIVATE/p6/runtime
export TELECOM_WORKSPACE=/PRIVATE/p6/workspace
export TELECOM_EXECUTION_POLICY=/PRIVATE/p6/execution-policy.json
test -r "$TELECOM_AUTH_FILE"
test -r "$TELECOM_EXECUTION_POLICY"
python3 docs/telecom/p6/research_only.py --port 5099
```

This serves only the research UI/API on 127.0.0.1:5099. No backend.py modification,
reference workload installation, production service restart, auto-reloader or
continuous worker. Stop with Ctrl-C. Keep it loopback-only; public researcher access
needs separately reviewed TLS/reverse-proxy/auth exposure. No static directory is
exposed: source/private files are not served. This is a test server, not a production
hosting recipe.

## Eight-step onboarding

1. **Register workload / supply source or image.** Prepare `/PRIVATE/p6/register.json`
   with `manifest` and unique `event_id`. The manifest includes source kind
   local-git (confined path + exact SHA), github (allowlisted HTTPS URL + SHA), oci
   (repository + sha256 digest) or helm (allowlisted URL + pinned archive digest).
   Declare the immutable image repository/digest/platform and applicable NF roles,
   PLMN/TAC/slices/DNN/interfaces/resources/monitoring. Provider declarations are not
   protocol verification. POST workloads; use its returned workload ID.
2. **Supply Helm chart.** For Git sources, chart is a relative path within the pinned
   captured tree. For OCI sources, it is relative to the server-approved tenant
   workspace. For Helm sources, it is relative to the verified extracted archive.
   Charts must satisfy current least-privilege policy; Dockerfile charts use scalar
   values.image so the verified output digest is bound to rendered/package bytes.
3. **Validate configuration.** Registration performs static manifest validation;
   execution performs real Helm lint/render and package/reference checks. Review
   declarations before queuing. Do not confuse this with live 3GPP conformance.
4. **Trigger CI/CD.** POST pipeline-jobs with workload_id/chart/event_id/image_mode
   (`prebuilt` or `dockerfile`). Local commit/push integrations submit exact SHAs and
   stable delivery IDs; signed GitHub ingress only queues. Explicitly run the job
   once using the foreground worker. Stop on any failure/uncertain state, no retry.
5. **View release.** GET pipeline-jobs and releases; inspect state history, source SHA,
   image/platform verification, package digests and provenance. A digest from a tool
   log is insufficient. Verify the OCI export and independent registry readback.
6. **Request catalog publication.** POST publication-requests with release_id,
   action=catalog and event_id. It binds to a server-owned research target. This is
   a request, not authorization to publish. Independent reviewer approval, enabled
   disposable target policy and publisher authorization are separate prerequisites.
7. **Review catalog evidence.** Only after separately approved publication, validate
   UUID/package identity and exact remote bytes in the research project. Existing
   names must conflict, never overwrite. UNKNOWN_OUTCOME blocks replay; inspect only.
8. **View deployment status.** publication-requests and release/job histories display
   actual recorded states. An update request returns NOT_VERIFIED; no deployment,
   running NF health or rollback success can be claimed. Instance deployment and
   telecom health tests require a separate future approved phase.

## Exact commands after prerequisites and approved isolated integration

Private curl config files contain the scoped bearer header and are owner-only. No
password/token is passed in argv. Requests below are operator-prepared JSON, not the
fixture's invalid registry.example reference. In a second terminal with the same
private environment:

```sh
set -e
umask 077
curl --fail-with-body --config /PRIVATE/p6/builder.curl \
  --json @/PRIVATE/p6/register.json \
  http://127.0.0.1:5099/api/telecom/workloads > /PRIVATE/p6/registered.json
```

Put the returned workload id into `/PRIVATE/p6/job.json`, containing chart, event_id,
image_mode and optional exact commit/registry secret_ref, then:

```sh
curl --fail-with-body --config /PRIVATE/p6/builder.curl \
  --json @/PRIVATE/p6/job.json \
  http://127.0.0.1:5099/api/telecom/pipeline-jobs > /PRIVATE/p6/job-result.json
P6_JOB_ID=$(python3 -c 'import json; print(json.load(open("/PRIVATE/p6/job-result.json"))["id"])')
# Required only for image_mode=dockerfile; absence is a blocker, not a fallback.
command -v buildctl
python3 -m telecom.worker --job-id "$P6_JOB_ID" \
  --token-file /PRIVATE/p6/builder-token.json > /PRIVATE/p6/worker-result.json
curl --fail-with-body --config /PRIVATE/p6/builder.curl \
  http://127.0.0.1:5099/api/telecom/pipeline-jobs > /PRIVATE/p6/jobs.json
curl --fail-with-body --config /PRIVATE/p6/builder.curl \
  http://127.0.0.1:5099/api/telecom/releases > /PRIVATE/p6/releases.json
```

For a prebuilt test omit the buildctl check. Check worker exit code AND COMPLETED;
inspect actual OCI verification and registry readback, never just HTTP 200. The
worker runs one queued job, not a daemon, and has no automatic restart. Cancellation
uses the existing pipeline-cancellations endpoint or foreground SIGINT/SIGTERM;
verify remote worker lease cleanup independently. A hard kill or lost session is
not permission to rerun a claimed job.

After successful review, prepare `/PRIVATE/p6/catalog-request.json` with the verified
release id, action=catalog and a new event_id:

```sh
curl --fail-with-body --config /PRIVATE/p6/builder.curl \
  --json @/PRIVATE/p6/catalog-request.json \
  http://127.0.0.1:5099/api/telecom/publication-requests \
  > /PRIVATE/p6/catalog-request-result.json
```

**STOP here.** No catalog-publications POST, instance operation, Helm install,
service restart or infrastructure setup command is authorized by these instructions.
Publication needs separate explicit approval, acceptable project ownership evidence
and image availability. Built-image registry publication is still a software gap.

Dashboard operations can submit the same JSON contracts and show eight tenant-scoped
views. It is a JSON workflow interface, not a complete graphical source/chart editor.
No polling or persistent browser token storage is enabled. Future production
activation needs a separately reviewed promotion/configuration/rollback procedure.
