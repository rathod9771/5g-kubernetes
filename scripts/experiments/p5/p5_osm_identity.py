#!/usr/bin/env python3
"""P5 phase 1C: offline validation of *sanitized* OSM metadata.

This tool NEVER contacts OSM, reads credentials or authorizes deployment.
Its input is untrusted metadata, not a live target attestation. It refuses
additional fields so raw VIM config, tokens and other secrets cannot enter
the input contract. It cannot establish live OSM health by itself.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import uuid

NS_ID = "57fd4942-6c44-4da4-8297-1a29d101286e"
NS_NAME = "p4-kdu-upgrade-test"
NSD = "p4kdu_ns"
KNF = "p4kdu_knf"
PROJECT = "admin"
VIM = "dummyvim"
CLUSTER = "lab-cluster"
NAMESPACE = "p4-kdu-upgrade"
EXPECTED_KDUS = {"cu": "p4kdu-cu", "du": "p4kdu-du"}
SCHEMA = "5g-kubernetes/p5-sanitized-osm-snapshot"
MAX_BYTES = 256 * 1024
MODEL = re.compile(
    r"^/app/storage/(?P<uuid>[0-9a-f-]{36}):(?P<revision>[1-9][0-9]*)/"
    r"p4kdu_knf/helm-chart-v3s/(?P<kdu>cu|du)$"
)

class SnapshotError(ValueError):
    pass


def parse_strict(blob):
    def pairs(items):
        keys = [key for key, _ in items]
        if len(keys) != len(set(keys)):
            raise SnapshotError("DUPLICATE_JSON_KEY")
        return dict(items)
    def constant(_):
        raise SnapshotError("INVALID_JSON")
    if len(blob) > MAX_BYTES:
        raise SnapshotError("SNAPSHOT_TOO_LARGE")
    try:
        return json.loads(blob.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise SnapshotError("INVALID_JSON") from None


def valid_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def fields(item, required, optional=()):
    if not isinstance(item, dict) or not set(required) <= set(item) or not set(item) <= set(required) | set(optional):
        raise SnapshotError("INVALID_METADATA_FIELDS")
    return item


def rows(value, required, optional=()):
    if not isinstance(value, list) or len(value) > 128:
        raise SnapshotError("INVALID_METADATA_LIST")
    return [fields(row, required, optional) for row in value]


def _exact(rows_, name):
    found = [x for x in rows_ if x["name"] == name]
    return found[0] if len(found) == 1 else None


def evaluate(s):
    """Return fixed reason codes; no raw OSM inventory is copied to output."""
    fields(s, ("schema", "schema_version", "target", "projects", "vims",
               "clusters", "ns_instances", "vnf_instances", "operations",
               "knf_packages", "nsd_packages"))
    if s["schema"] != SCHEMA or s["schema_version"] != 1 or s["target"] != "dell-p4":
        raise SnapshotError("SNAPSHOT_IDENTITY_INVALID")
    projects = rows(s["projects"], ("name", "id"))
    vims = rows(s["vims"], ("name", "id", "diagnostic"))
    clusters = rows(s["clusters"], ("name", "id", "vim_id", "helm_v3", "state"))
    nss = rows(s["ns_instances"], ("name", "id", "ns_state", "operation", "status",
                                    "nsd_id", "vim_id", "kdus"))
    vnfs = rows(s["vnf_instances"], ("id", "ns_id", "vnfd_id"))
    operations = rows(s["operations"], ("id", "ns_id", "state"))
    knfs = rows(s["knf_packages"], ("name", "id", "revision"))
    nsds = rows(s["nsd_packages"], ("name", "id", "vnfd_ids"))
    problems = []
    project, vim, cluster = (_exact(rows_, name) for rows_, name in
                              ((projects, PROJECT), (vims, VIM), (clusters, CLUSTER)))
    ns, knf, nsd = (_exact(rows_, name) for rows_, name in
                    ((nss, NS_NAME), (knfs, KNF), (nsds, NSD)))
    if not project or not valid_uuid(project["id"]):
        problems.append("PROJECT_IDENTITY_MISMATCH")
    if not vim or not valid_uuid(vim["id"]):
        problems.append("VIM_IDENTITY_MISMATCH")
    if not cluster or not valid_uuid(cluster["id"]):
        problems.append("CLUSTER_IDENTITY_MISMATCH")
    if vim and vim["diagnostic"] not in ("INCONCLUSIVE", "HEALTHY"):
        problems.append("VIM_REPORTED_UNUSABLE")
    if cluster and vim and (cluster["vim_id"] != vim["id"] or cluster["helm_v3"] is not True or
                             cluster["state"] != "ENABLED"):
        problems.append("VIM_K8S_REGISTRATION_MISMATCH")
    if not knf or not valid_uuid(knf["id"]) or type(knf["revision"]) is not int or knf["revision"] < 2:
        problems.append("KNF_IDENTITY_MISMATCH")
    if not nsd or not valid_uuid(nsd["id"]) or not isinstance(nsd["vnfd_ids"], list) or (
            knf and nsd and knf["id"] not in nsd["vnfd_ids"]):
        problems.append("NSD_IDENTITY_MISMATCH")
    if not ns or ns["id"] != NS_ID or ns["ns_state"] != "READY" or ns["operation"] not in ("IDLE", None) or \
            ns["status"] != "running" or (nsd and ns["nsd_id"] != nsd["id"]) or \
            (vim and ns["vim_id"] != vim["id"]):
        problems.append("P4_NS_IDENTITY_OR_STATE_MISMATCH")
    if ns:
        kdus = ns["kdus"]
        if not isinstance(kdus, list) or len(kdus) != 2:
            problems.append("P4_KDU_INVENTORY_MISMATCH")
        else:
            seen = set()
            for k in kdus:
                fields(k, ("name", "release", "namespace", "model"))
                match = MODEL.fullmatch(str(k["model"]))
                if (k["name"] in seen or k["name"] not in EXPECTED_KDUS or
                        k["release"] != EXPECTED_KDUS.get(k["name"]) or
                        k["namespace"] != NAMESPACE or not match or
                        match["uuid"] != (knf or {}).get("id") or
                        match["revision"] != "2" or match["kdu"] != k["name"]):
                    problems.append("P4_KDU_INVENTORY_MISMATCH")
                    break
                seen.add(k["name"])
            if seen != set(EXPECTED_KDUS):
                problems.append("P4_KDU_INVENTORY_MISMATCH")
    if not ns or len([v for v in vnfs if v["ns_id"] == NS_ID]) != 1 or (
            knf and any(v["vnfd_id"] != knf["id"] for v in vnfs if v["ns_id"] == NS_ID)):
        problems.append("P4_VNF_BINDING_MISMATCH")
    if not ns or not operations or any(op["ns_id"] != NS_ID or not valid_uuid(op["id"]) or
            op["state"] not in ("COMPLETED", "FAILED", "FAILED_TEMP", "PARTIALLY_COMPLETED")
            for op in operations):
        problems.append("P4_LCM_OPERATION_UNVERIFIED")
    return {
        "schema": "5g-kubernetes/p5-osm-identity-review",
        "schema_version": 1,
        "status": "CONSISTENT_SNAPSHOT" if not problems else "BLOCKED",
        "source": "UNTRUSTED_OFFLINE_METADATA",
        "live_osm_identity_verified": False,
        "live_vim_health_verified": False,
        "catalog_publication_authorized": False,
        "deployment_authorized": False,
        "mutations_performed": False,
        "blockers": sorted(set(problems) | {
            "NO_AUTHENTICATED_LIVE_OSM_OBSERVATION",
            "NO_DELL_SIGNED_RELEASE",
            "NO_OPERATOR_DEPLOYMENT_APPROVAL",
            "P4_PRODUCTION_UPGRADE_GATE_CLOSED",
        }),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path,
                        help="Previously sanitized metadata only; NEVER raw NBI responses or credentials")
    args = parser.parse_args(argv)
    try:
        p = args.snapshot
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_BYTES:
            raise SnapshotError("SNAPSHOT_UNAVAILABLE")
        output = evaluate(parse_strict(p.read_bytes()))
    except (SnapshotError, OSError, TypeError, AttributeError, KeyError) as e:
        print(json.dumps({"status": "REJECTED", "reason_code": type(e).__name__,
                          "deployment_authorized": False, "mutations_performed": False}))
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output["status"] == "CONSISTENT_SNAPSHOT" else 2


if __name__ == "__main__":
    sys.exit(main())
