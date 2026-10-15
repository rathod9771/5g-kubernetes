# P9 disposable live test — separate approval required

Nothing in this procedure was executed by P9. No production restart is
needed. This test is required before setting `upgrade_rollback_certified`
for any real tenant; until it has been run and reviewed, every real
`instance-updates` submission fails closed with `NOT_CERTIFIED` regardless of
configuration.

1. Complete the P8-LIVE-TEST.md disposable create procedure first and leave
   the resulting disposable instance `WORKLOAD_READY`. Do not reuse a
   protected project/catalog/namespace/NS or admin credentials. If isolation
   is not proven, STOP.
2. Produce and catalog-publish a second release of the same workload exactly
   as in the P8 walkthrough (a new chart revision is sufficient; a new image
   digest is not required to exercise the update path).
3. Request an instance update: `POST /instance-update-requests` with the
   disposable deployment id, the new publication id and `action:"update"`.
   Independently approve it. Do **not** set `upgrade_rollback_certified` yet —
   first confirm with the operator team the exact OSM v19 NS-update NBI call
   this installed backend expects (operation path, `updateType` value and
   required fields; current code assumes `POST
   /nslcm/v1/ns_instances/<id>/update` with
   `{"updateType":"CHANGE_VNFPKG","changeVnfPackageData":[...]}`, which is
   UNVERIFIED against this specific OSM v19 install and may need correction
   before the next step).
4. Only after that NBI shape is confirmed (or corrected in code and
   re-reviewed), set `upgrade_rollback_certified:true` for this one
   disposable tenant's `deployment_targets` entry in the private server
   policy. Submit exactly once via `instance-updates`. Never retry a timeout
   or `UNKNOWN_OUTCOME`; it requires manual operator reconciliation, same as
   the create path.
5. Manually refresh via `instance-update-observations` once after OSM
   reports completion. Capture the resulting operation id, image digest and
   readiness evidence as LIVE only if genuinely observed; record any mismatch
   as a required code fix, not a configuration issue.
6. Only after a successful live update, exercise rollback once the same way:
   request/approve/submit/observe a `rollback` action back to the
   immediately prior release. Confirm `release_history` and the deployment's
   current release update correctly.
7. Validate dashboard/history/audit integrity, protected resources unchanged,
   no watcher/remediation activity. Stop on any conflict, watchdog failure or
   unhealthy protected service.
8. Immediately after the test (pass or fail), unset
   `upgrade_rollback_certified` again unless a durable, reviewed decision has
   been made to keep live update/rollback enabled for that one disposable
   tenant. Do not propagate this flag to any other tenant or project without
   repeating this procedure.

Cleanup remains a separate approval, exactly as in P8-LIVE-TEST.md: inspect
exact ownership, terminate only through OSM and wait/read back, then remove
only this package's newly created catalog UUIDs and the empty owned
namespace/project through their lifecycle owners. Do not run `uninstall.sh`,
blanket `kubectl delete`, cleanup scripts, or delete protected catalog
entries.
