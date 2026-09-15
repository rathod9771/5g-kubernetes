# Findings: OSM `ns-update` (CHANGE_VNFPKG) Does Not Trigger Helm Upgrade for KDU Revisions

**Date:** 15 September 2026
**Context:** Testing end-to-end CI automation — Git push → CI onboards new package content to OSM → attempt to auto-propagate that update to a running NS instance.
**Environment:** OSM 19.0.0, kubeadm cluster (K8s v1.29.15) registered as K8s VIM, `full_stack_ns` instance (`open5gs` + `ims` + `srsran`, all KDU-based VNFs).

## Summary

OSM's package-content update endpoint (`PUT /vnfpkgm/v1/vnf_packages/<id>/package_content`) works correctly and reliably updates a VNF package's stored descriptor/chart content in place, incrementing an internal `revision` counter each time. However, the standard mechanism for propagating that update to an **already-running** NS instance — `POST /nslcm/v1/ns_instances/<id>/update` with `updateType: CHANGE_VNFPKG` — did **not** trigger a Helm upgrade in our testing, even though the API call succeeded (`202 Accepted`, operation completed as `COMPLETED`/`READY`).

## What Works

- CI pipeline (GitHub Actions, self-hosted runner) successfully packages and PUTs updated VNF/NS descriptor content to OSM on every push to `osm-packages/**`.
- Each successful PUT increments the package's internal revision (confirmed via `storage.folder` field showing `<id>:<revision>`, e.g. `991642eb-...:11` after 11 content updates).
- The script correctly distinguishes success (2xx) from failure and fails the CI job loudly on error (e.g. HTTP 422 when a disallowed field like `version` is changed).
- **Constraint discovered:** OSM rejects any change to the `version` field on a content-update PUT (`422 UNPROCESSABLE_ENTITY — 'version' cannot be modified`). In-place content updates must keep `version` identical; the `id`+`version` pair is treated as an immutable package identity, consistent with the ETSI SOL006 data model. A genuine new version requires onboarding as a new package (or delete+recreate), not a content-update PUT.

## What Does Not Work (This Finding)

Test performed:
1. Confirmed `srsran_knf` package was at revision 11 (content unchanged from what the running `srsran` VNF instance was deployed with — a deliberate no-diff control test).
2. Called:
   ```
   POST /nslcm/v1/ns_instances/<ns-id>/update
   Body:
     updateType: CHANGE_VNFPKG
     changeVnfPackageData:
       vnfInstanceId: <srsran vnf instance id>
       vnfdId: <srsran_knf package id>
   ```
   (Note: the API requires `changeVnfPackageData` as a single object, not a list — this differs from some SOL005 examples elsewhere.)
3. Received `202 Accepted`. LCM logs show the operation entered `PROCESSING`, then completed as `COMPLETED` in **~1 second**.
4. Checked the target pod (`srsran-gnb-...`): `RESTARTS: 0`, unchanged age. No `helm3 upgrade` command appears anywhere in the LCM logs for this operation — only for the original `helm3 install` at first instantiation.

**Conclusion:** `CHANGE_VNFPKG` appears designed to switch a VNF instance to reference a **different** `vnfd-id** (e.g., swap one VNF package for a different one entirely), not to pull in a newer **revision** of the same package. Since we passed the same `vnfdId` already associated with the instance, OSM short-circuited the operation as a no-op without invoking Helm at all.

## Open Question

It's not yet established whether:
(a) revision-based redeployment for K8s/KDU VNFs is simply unsupported in OSM 19's current implementation of `CHANGE_VNFPKG`,
(b) it requires a different API path (e.g., a scale or heal operation, or a dedicated KDU-upgrade endpoint not yet identified), or
(c) it requires deleting and re-instantiating the NS instance (the only method confirmed to work so far, demonstrated manually multiple times during earlier debugging).

This is worth checking against OSM's official documentation/source or raising with the OSM community, since VM-based VDU updates may have a more mature/different code path than KDU updates.

## Current State of the Pipeline

- **Automated (working):** code change → CI → OSM package catalog updated with new content, same version, incremented revision.
- **Manual (not yet automated):** propagating an onboarded update to a live running instance. Confirmed working method: delete the NS instance → delete/re-upload affected packages if a version bump is needed → re-instantiate fresh. No automated in-place upgrade path has been found yet for KDU-based VNFs.

## Recommendation

Treat this as a documented, known gap rather than a blocker. Proceed with replicating the proven package-and-compose pattern across the remaining RAN scenarios; revisit the live-update automation question afterward, either by consulting OSM's documentation/source directly or reaching out to the OSM community/mailing list with this specific reproduction case.
