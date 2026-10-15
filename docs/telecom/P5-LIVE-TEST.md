# Proposed isolated live test — separate approval required

This procedure was NOT executed. Do not use the current production cluster, OSM
catalog, Docker daemon, protected NSs or service unit. Do not restart ran-selector.

1. Provision a disposable off-host rootless BuildKit worker and a test registry,
   and a separate OSM test installation/project `research-demo`. Verify worker
   cgroups (CPU <= 1 core, memory <= 1 GiB, bounded disk/process count), 120-second
   job lease and network policy excluding production/Kubernetes metadata endpoints.
   Record quota/lease/cancellation evidence. Restrict OSM credentials to that project.
   Install matching `buildctl` on the isolated coordinator; it is absent here.
2. On an isolated coordinator create a private runtime and tenant source workspace.
   Use an ordinary disposable Git repository (not the protected deployment checkout).
   Prepare schema-v1 RAN/Core manifests with exact public GitHub commit SHA or local
   Git path; pinned base images, deterministic build inputs and scalar chart
   values.image; or provide an existing single OCI manifest digest. Test chart must
   pass existing least-privilege Helm validation. No chart is installed by this path.
3. Provision owner-only auth and execution policy files outside Git. Set
   TELECOM_AUTH_FILE, TELECOM_RUNTIME_DIR, TELECOM_WORKSPACE, TELECOM_EXECUTION_POLICY,
   PYTHONPATH=scripts, HELM_BIN=/usr/local/bin/helm.real. Use independent scoped
   researcher/builder, reviewer and publisher credentials. Never pass tokens/passwords
   in argv, URL or terminal output. Private token files hold JSON {"value":"..."}.
   Policy secret entries contain tenant, purpose, resources and file. Purpose must be
   registry-bearer (resource: exact registry/repository), github-webhook (resource:
   workload ID), or osm-credentials (resource: exact host + "#" + project). The file
   is owner-only and never supplied by the request. Registry entries
   hold a bearer value; OSM entries hold a JSON-encoded username/password object in
   that value. No credential appears in request JSON.
4. Review a policy with these keys (all paths server-owned):

```json
{
  "local_roots": {"demo": "demo"},
  "github_repositories": ["https://github.com/RESEARCHER/DISPOSABLE-REPO"],
  "oci_repositories": ["registry.example/research/ran"],
  "helm_charts": ["https://charts.example/research/ran.tgz"],
  "secrets": {},
  "webhooks": {},
  "build_worker": {
    "tenant": "demo", "isolation": "DEDICATED_OFF_HOST_QUOTA_ENFORCED",
    "quota_evidence": "REVIEWED-EVIDENCE-DIGEST", "max_seconds": 120,
    "lease_seconds": 120, "address": "tcp://builder.example:1234",
    "ca": "/PRIVATE/ca.pem", "cert": "/PRIVATE/client.pem", "key": "/PRIVATE/client-key.pem"
  },
  "catalog_targets": {
    "demo": {"scope":"DISPOSABLE_RESEARCH_PROJECT", "enabled":false,
      "project":"research-demo", "host":"https://TEST-OSM.example",
      "secret_ref":"test-osm", "ca_file":"/PRIVATE/osm-ca.pem"}
  }
}
```

5. For an isolated API process only (no existing service restart):

```sh
python3 -c 'from flask import Flask; from telecom.api import register; app=Flask("isolated-p5"); register(app); app.run(host="127.0.0.1",port=5099,debug=False)'
```

   Send authenticated JSON using a private curl config containing the bearer header,
   not an inline token. For example `curl --config /PRIVATE/builder.curl --json
   @/PRIVATE/register.json http://127.0.0.1:5099/api/telecom/workloads`. Configuration
   must include verified TLS if exposing a separate HTTPS reverse proxy; never
   expose the Flask development server publicly.
6. POST workloads; POST pipeline-jobs with workload_id/chart/event_id/image_mode.
   Run exactly one job in the foreground (not a daemon):

```sh
python3 -m telecom.worker --job-id JOB_ID --token-file /PRIVATE/builder-token.json
```

   Inspect its exit status and GET pipeline-jobs/releases. Stop on failure. Confirm
   actual OCI bytes/digest/platform, retained OCI artifact and packaged image digest.
   No success is accepted from a log line or build exit code alone. Verify remote
   worker lease cleanup independently. Request pipeline-cancellations in a separate
   deliberate cancellation test and check local and remote stop evidence. SIGINT
   stops the foreground worker; never auto-restart a claimed job.
7. Test one signed GitHub push with server-owned workload binding: subject,tenant,
   secret_ref, exact ref, chart and image_mode. Configure the GitHub webhook secret
   out of band. Duplicate delivery must return the same QUEUED job without building
   or deploying. Authenticated local commit/push integration calls pipeline-jobs
   with an exact new SHA and stable event_id; it does not install local Git hooks.
8. Catalog publication requires a NEW explicit approval for the disposable project.
   Enable that target only in the isolated policy. POST publication-requests,
   independent publication-approvals, then catalog-publications once. Confirm project,
   both immutable catalog UUIDs and byte-for-byte package checksum readback. Stop on
   UNKNOWN_OUTCOME; inspect with read-only tooling, never repeat the POST blindly.
   Existing names must produce conflict with zero PUTs. Stop the isolated server
   after validation and preserve its private journal. Do not alter production policy.
9. Request update/rollback and confirm NOT_VERIFIED with no OSM lifecycle request.
   Real deployment, rollout and rollback testing is a distinct future approved phase.
   Verify unrelated protected services through their existing approved read-only
   operations, not by granting this build worker access. Remove disposable resources
   only under separately approved test-environment cleanup; no production rollback
   command is part of this test.

Acceptance: source SHA pinned; verified image and package digests; no leaked secrets;
no duplicate job on replay; cross-tenant/invalid-approval denials; failure/cancellation
history intact; no production reachability; no uncertain submission retry. Live
verification status may be promoted only for the exact paths actually exercised.

Known gaps: private GitHub auth, LFS/submodules/linked worktrees, multi-platform OCI,
registry image push, remote quota/lease certification, distributed fencing, automated
unknown-outcome reconciliation, certified instance upgrade/rollback and telecom
continuity testing. These must not be represented as completed live functionality.
