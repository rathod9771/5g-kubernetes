# P8 disposable live test — separate approval required

Nothing in this procedure was executed by P8. No production restart is needed.

1. Obtain approval to provision only a new project research-<tenant>, non-admin
   user, namespace research-<tenant>-demo, narrow read-only verification identity
   and OSM isolated VIM/K8s attachment. Record project/VIM/cluster/namespace UUIDs
   and namespace label telecom.research/tenant=<tenant>. Do not reuse protected
   project/catalog/namespace/NS or admin credentials. If isolation is not proven,
   STOP. Existing environment blockers from P6/P7 remain until verified.
2. Independently verify and pin an actual non-root HTTP image port8080 /healthz;
   provide registry allowlist + pull credentials as scoped secret references.
   Replace all example.invalid/all-ones placeholders in examples/telecom/disposable
   manifest.json and chart/values.yaml in a private workspace copy. Do not change
   tracked sample files or build on the protected 5G node.
3. Configure private server policy: catalog_targets[tenant] scope
   DISPOSABLE_RESEARCH_PROJECT, enabled:true, project:research-<tenant>, project_id,
   host (verified HTTPS), ca_file, secret_ref. Secret purpose osm-credentials,
   resource host#project; value is JSON {username,password}; no admin/defaults.
   deployment_targets[tenant]: scope DISPOSABLE_RESEARCH_NAMESPACE, enabled:true,
   tenant, namespace, namespace_uid, cluster_uid (kube-system UID), vim_id,
   kubeconfig (private0600 bounded JSON, one context/cluster/user, embedded CA, static
   scoped token or embedded certificate/key, read-only RBAC, no exec/auth-provider). Configure
   local_roots[tenant] and oci_repositories. Never place secrets in request JSON.
4. Preserve preflight: protected NS READY/running/IDLE, all13 Open5GS pods and
   CU/DU/fog Ready, F1-C/NGAP established, pending lifecycle operations none;
   watcher inactive, LIVE_AUTO/remediation disabled. Archive private telecom
   journal consistently, verify audit. No restart/repair/remediation permitted.
5. Start loopback research launcher as in P8.md. Using private Authorization
   headers, follow the eight exact API stages in P8.md. Use unique event IDs and
   separate reviewer identity; first request catalog approval, stop for approved
   catalog publication, then independently request/approve initial deployment.
   Never let a Git webhook or image build deploy automatically.
6. Submit initial-deployments exactly once. Expected one POST to
   /osm/nslcm/v1/ns_instances_content with scoped token, verified NSD UUID, VIM UUID
   and nf/nf k8s-namespace. Record new NS/operation UUIDs. Never retry timeout or
   UNKNOWN_OUTCOME. No upgrade, rollback, delete, terminate or force operation.
7. Manually refresh deployment-observations once after OSM completion, bounded
   timeout. Capture NS/op status, namespace UID, pod imageID digest and readiness
   probe. Application health independently GET /healthz only through an approved
   disposable endpoint; do not tunnel/exec against protected workloads. Record
   evidence as LIVE only if real, SIMULATED never qualifies production. No telecom
   protocol tests are implied by this HTTP application.
8. Validate dashboard/history/auth denial/cross-tenant denial, audit integrity,
   protected resources unchanged, no watcher/remediation. Stop on conflict,
   watchdog failure or unhealthy protected service. Stop launcher with Ctrl-C.
   Retain bounded diagnostics; no automatic retries or repairs.

Cleanup is a separate approval: inspect exact newly created NS ownership,
terminate only through OSM and wait/read back; remove only this package's newly
created catalog UUIDs after no instances reference them; then delete the empty
owned namespace/project using their lifecycle owners. Do not run uninstall.sh,
blanket kubectl delete, cleanup scripts or delete protected catalog entries.
