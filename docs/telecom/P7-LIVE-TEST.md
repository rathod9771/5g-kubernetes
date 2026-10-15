# P7 proposed real integration test — separate approval required

NOT EXECUTED. No installation, infrastructure provisioning, registry push or catalog
publication is authorized by this document alone. Do not use the protected four-core
5G node as a worker or touch existing Fog-RAN/Open5GS/catalog resources.

## Minimum setup

1. Provision an off-host disposable rootless BuildKit worker, pinned buildctl and
   reviewed Skopeo version. Use a dedicated worker CA, mTLS client identity, hostname,
   pinned peer certificate SHA256 and one approved IPv4 address. Confirm deny rules
   for protected host/pod/service networks, metadata endpoints and production OSM.
2. Independently verify worker cgroups: <=1 CPU, <=1 GiB RAM, <=128 PIDs, <=2 GiB
   bounded disk, client <=120 seconds and worker lease <=120 seconds. Test lease
   expiry and controller cancellation without protected workload access. Limit
   coordinator RSS/disk and registry/proxy requests as well; client watchdogs are
   sampled. Deny unreviewed Dockerfile egress/frontends and never mount host sockets.
3. Provide a dedicated HTTPS test registry/repository for tenant demo, authenticated
   push/pull credentials, valid CA trust and no anonymous writes. Independently verify
   registry RBAC, cross-tenant denial and denial of anonymous valid-image publication
   in that disposable registry. Configure server/proxy request limits and capture its
   logs to certify the client's request-trace coverage/redirect bounds. Do not label
   debug counters as exact wire counters without this evidence.
4. Provision a disposable OSM research-demo project and non-default non-admin scoped
   account only when separately approved. Record its actual UUID. The broken OSM CLI
   is not needed for Python NBI operations. Do not repair/install osm_im by inference.
5. Provision owner-only auth, execution policy and credential-reference files outside
   Git, with separate builder/reviewer/publisher roles. Use only the real target
   identities/addresses and trusted public CA files, not the documentation placeholders.

## Server-owned execution contract (illustrative only)

```json
{
  "local_roots": {"demo":"demo"},
  "github_repositories": [],
  "build_worker": {
    "tenant":"demo", "isolation":"DEDICATED_OFF_HOST_QUOTA_ENFORCED",
    "address":"tcp://worker.example:1234", "allowed_ips":["192.0.2.20"],
    "denied_cidrs":["127.0.0.0/8","169.254.0.0/16","10.0.0.0/8"],
    "dedicated_ca":true, "certificate_sha256":"REPLACE_WITH_REVIEWED_64_HEX_FINGERPRINT",
    "ca":"/PRIVATE/p7/worker-ca.pem", "cert":"/PRIVATE/p7/client.pem", "key":"/PRIVATE/p7/client-key.pem",
    "cpu_millicores":1000, "memory_mib":1024, "pids":128, "disk_mib":2048,
    "max_seconds":120, "lease_seconds":120, "quota_evidence":"REVIEWED_EVIDENCE_DIGEST"
  },
  "registry_targets": {
    "demo": {
      "scope":"DISPOSABLE_RESEARCH_REPOSITORY", "enabled":false,
      "authentication_required":true, "repositories":["registry.example/research/demo/ran"],
      "secret_ref":"registry-publication", "ca_file":"/PRIVATE/p7/registry-ca.pem",
      "max_seconds":60, "max_requests":128, "client_version":"skopeo version 1.21.0",
      "transport_evidence":"REPLACE_WITH_VERIFIED_VERSION_AND_HTTP_TRACE_EVIDENCE"
    }
  },
  "secrets": {
    "registry-publication": {
      "tenant":"demo", "purpose":"registry-publish", "resources":["registry.example/research/demo/ran"],
      "file":"/PRIVATE/p7/registry-secret.json"
    }
  },
  "catalog_targets": {
    "demo": {"scope":"DISPOSABLE_RESEARCH_PROJECT", "enabled":false,
      "project":"research-demo", "project_id":"REPLACE_WITH_ACTUAL_PROJECT_UUID",
      "host":"https://test-osm.example", "secret_ref":"research-osm", "ca_file":"/PRIVATE/p7/osm-ca.pem"}
  }
}
```

Replace Skopeo example version with the actually reviewed exact version/flags and
trace contract; it is not a latest-version recommendation. Replace denied_cidrs with
ALL protected production/metadata networks; do not use this example as isolation
proof. Registry configuration is deliberately disabled until separate test approval.
The registry secret file contains a JSON value whose contents are a JSON-encoded
username/password object. OSM references use purpose osm-credentials and resource
exact host + # + project. No default/admin OSM credential is accepted. No credential
belongs in manifests, source trees, shell arguments or Git.

## Exact bounded test commands after setup AND separate approval

Use a coordinator other than the protected build node. Prepare an ordinary local
Git repo containing a reviewed tiny Dockerfile and scalar values.image chart. Pin
its exact commit in schema-v1 registration JSON. Keep output below 128 MiB and <=32
OCI layers, amd64, uncompressed OCI TAR (bounded PAX timestamps supported; no
path/size overrides). The current registry schema requires HTTPS on default port 443. The fixture images in offline demos
are not runnable NFs or a real registry test.

```sh
cd /home/rclab/5g-kubernetes-cicd
export PYTHONPATH="$PWD/scripts"
export HELM_BIN=/usr/local/bin/helm.real
export TELECOM_AUTH_FILE=/PRIVATE/p7/auth.json
export TELECOM_RUNTIME_DIR=/PRIVATE/p7/runtime
export TELECOM_WORKSPACE=/PRIVATE/p7/workspace
export TELECOM_EXECUTION_POLICY=/PRIVATE/p7/execution-policy.json
command -v buildctl
command -v skopeo
buildctl --version
skopeo --version
python3 docs/telecom/p6/research_only.py --port 5099
```

Foreground loopback-only test server, no auto-reloader or service unit. In another
terminal with the same private environment, after the approved operator enables
only the research registry target:

```sh
set -e
umask 077
curl --fail-with-body --config /PRIVATE/p7/builder.curl \
  --json @/PRIVATE/p7/register.json \
  http://127.0.0.1:5099/api/telecom/workloads > /PRIVATE/p7/registered.json
# job.json: returned workload_id, relative chart, unique event_id, image_mode=dockerfile.
curl --fail-with-body --config /PRIVATE/p7/builder.curl \
  --json @/PRIVATE/p7/job.json \
  http://127.0.0.1:5099/api/telecom/pipeline-jobs > /PRIVATE/p7/job-result.json
P7_JOB_ID=$(python3 -c 'import json; print(json.load(open("/PRIVATE/p7/job-result.json"))["id"])')
python3 -m telecom.worker --job-id "$P7_JOB_ID" \
  --token-file /PRIVATE/p7/builder-token.json > /PRIVATE/p7/worker-result.json
curl --fail-with-body --config /PRIVATE/p7/builder.curl \
  http://127.0.0.1:5099/api/telecom/pipeline-jobs > /PRIVATE/p7/jobs.json
curl --fail-with-body --config /PRIVATE/p7/builder.curl \
  http://127.0.0.1:5099/api/telecom/registry-publications > /PRIVATE/p7/registry-results.json
curl --fail-with-body --config /PRIVATE/p7/builder.curl \
  http://127.0.0.1:5099/api/telecom/releases > /PRIVATE/p7/releases.json
```

Private curl configs contain bearer headers and must be mode 0600. Never echo them.
Perform exactly one initial job; no automatic retry. Check worker exit status AND
COMPLETED. Require matching source commit, worker certificate/identity, OCI digest,
registry receipt VERIFIED, exact raw manifest readback, packaged image digest and
KNF/NSD hashes. Check worker/server request logs, CPU/RSS/disk/lease/cancellation
limits independently before promoting OFFLINE_TESTED to LIVE_VERIFIED.

With the verified release ID, prepare catalog-request.json and submit only a request:

```sh
curl --fail-with-body --config /PRIVATE/p7/builder.curl \
  --json @/PRIVATE/p7/catalog-request.json \
  http://127.0.0.1:5099/api/telecom/publication-requests \
  > /PRIVATE/p7/catalog-request-result.json
```

This requires a fresh registry receipt (<=300 seconds) and current unchanged registry
policy. Stop on expired evidence; do not edit timestamps or the journal. A subsequent
separately approved identical-content job can reuse a VERIFIED publication and
refresh readback without another copy. Unknown receipts block replay and require
manual read-only investigation; no automatic uncertain-outcome recovery is supplied.

STOP at AWAITING_APPROVAL. No catalog-publications POST, instance lifecycle action,
service restart or workload install is included. Real catalog publication requires
separate approval, independent reviewer/publisher and acceptable OSM project UUID/
credential/readback. Instance execution remains NOT_VERIFIED even after publication.

Cancellation: use pipeline-cancellations for the job or foreground Ctrl-C/SIGTERM;
verify local child cleanup and remote lease expiry independently. Stop after any
watchdog, auth/TLS/digest mismatch or unexpected state. Partial registry artifacts
may require separately approved sandbox garbage collection; do not delete/retry to
force success. Stop the loopback server with Ctrl-C and preserve private evidence.
