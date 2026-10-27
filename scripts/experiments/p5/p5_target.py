#!/usr/bin/env python3
"""P5 phase 1B: read-only, Dell-only Kubernetes baseline observation.

Never explicitly retrieves or prints Secret contents, OSM credentials, or NS inventory.\nHelm list may internally read Kubernetes Secrets used as release storage. Does not
authorize release publication or deployment. Operator must explicitly choose
--observe to run allowlisted kubectl/helm READ commands. --snapshot is offline.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

EXPECTED_HOST = "Precision-3680"
EXPECTED_CONTEXT = "kubernetes-admin@kubernetes"
EXPECTED_SERVER = "https://172.30.0.20:6443"
P4_NAMESPACE = "p4-kdu-upgrade"
OSM_NAMESPACE = "osm"
MAX_OUTPUT = 2 << 20
EXPECTED_HELM = {
    "p4kdu-cu": (6, "p4kdu-cu-0.2.0", "r2"),
    "p4kdu-du": (8, "p4kdu-du-0.2.0", "r2"),
}


class ObservationError(ValueError):
    pass


def _read_json(data):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise ObservationError("MALFORMED_JSON")
        return dict(items)

    def bad_constant(_):
        raise ObservationError("MALFORMED_JSON")

    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ObservationError("MALFORMED_JSON") from error
    return result


def _command(argv):
    """An exact argv list, no shell, no streaming secrets or raw error output."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ObservationError("READ_COMMAND_FAILED") from error
    if result.returncode or len(result.stdout) > MAX_OUTPUT:
        raise ObservationError("READ_COMMAND_FAILED")
    return result.stdout.strip()


def _items(value):
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ObservationError("INVALID_KUBERNETES_LIST")
    return value["items"]


def collect(hostname=socket.gethostname, run=_command):
    """Check local identity and API endpoint BEFORE issuing any cluster GET."""
    host = hostname()
    if host != EXPECTED_HOST:
        raise ObservationError("WRONG_HOST")
    if os.environ.get("HELM_KUBEAPISERVER") or os.environ.get("HELM_KUBECONTEXT") or os.environ.get("HELM_KUBECONFIG"):
        raise ObservationError("HELM_OVERRIDE_PRESENT")
    ctx = run(["kubectl", "config", "current-context"])
    if ctx != EXPECTED_CONTEXT:
        raise ObservationError("WRONG_CONTEXT")
    server = run(["kubectl", "config", "view", "--minify",
                  "-o", "jsonpath={.clusters[0].cluster.server}"])
    if server != EXPECTED_SERVER:
        raise ObservationError("WRONG_API_SERVER")

    def kube(namespace, resource):
        return _read_json(run(["kubectl", "--context", EXPECTED_CONTEXT,
                               "-n", namespace, "get", resource, "-o", "json"]))

    return {
        "hostname": host, "context": ctx, "api_server": server,
        "namespace": kube(P4_NAMESPACE, "namespace/" + P4_NAMESPACE),
        "deployments": kube(P4_NAMESPACE, "deployments"),
        "pods": kube(P4_NAMESPACE, "pods"),
        "helm": _read_json(run(["helm", "--kube-context", EXPECTED_CONTEXT,
                                "--namespace", P4_NAMESPACE, "list", "--output", "json"])),
        "osm_pods": kube(OSM_NAMESPACE, "pods"),
        "osm_services": kube(OSM_NAMESPACE, "services"),
    }


def _names(entries):
    names = [p.get("metadata", {}).get("name") for p in entries if isinstance(p, dict)]
    if len(names) != len(entries) or any(not isinstance(name, str) or not name for name in names):
        raise ObservationError("MALFORMED_RESOURCE")
    return {name: item for name, item in zip(names, entries)} if len(set(names)) == len(names) else {}


def _pod_ready(pod, zero_restarts):
    if pod.get("status", {}).get("phase") != "Running":
        return False
    containers = pod.get("spec", {}).get("containers", [])
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if not isinstance(containers, list) or not containers or not isinstance(statuses, list):
        return False
    expected = {c.get("name") for c in containers if isinstance(c, dict)}
    reported = {c.get("name") for c in statuses if isinstance(c, dict)}
    if len(expected) != len(containers) or expected != reported:
        return False
    return all(s.get("ready") is True and type(s.get("restartCount")) is int and
               (not zero_restarts or s["restartCount"] == 0) for s in statuses)


def evaluate(snapshot):
    """Returns fixed-field, sanitized observations. Never grants authorization."""
    if not isinstance(snapshot, dict):
        raise ObservationError("INVALID_SNAPSHOT")
    problems = []
    if snapshot.get("hostname") != EXPECTED_HOST:
        problems.append("WRONG_HOST")
    if snapshot.get("context") != EXPECTED_CONTEXT:
        problems.append("WRONG_CONTEXT")
    if snapshot.get("api_server") != EXPECTED_SERVER:
        problems.append("WRONG_API_SERVER")
    ns = snapshot.get("namespace")
    if not isinstance(ns, dict) or ns.get("metadata", {}).get("name") != P4_NAMESPACE or ns.get("status", {}).get("phase") != "Active":
        problems.append("P4_NAMESPACE_NOT_ACTIVE")
    try:
        deployments = _names(_items(snapshot.get("deployments")))
        pods = _names(_items(snapshot.get("pods")))
        osm_pods = _names(_items(snapshot.get("osm_pods")))
        osm_services = _names(_items(snapshot.get("osm_services")))
    except (ObservationError, AttributeError, TypeError):
        raise ObservationError("INVALID_SNAPSHOT") from None
    if set(deployments) != set(EXPECTED_HELM):
        problems.append("P4_DEPLOYMENT_SET_MISMATCH")
    else:
        for key, value in deployments.items():
            spec, status = value.get("spec", {}), value.get("status", {})
            if spec.get("replicas", 1) != 1 or status.get("readyReplicas", 0) != 1 or status.get("availableReplicas", 0) != 1:
                problems.append("P4_DEPLOYMENT_UNREADY")
                break
    if len(pods) != 2 or not all(any(name.startswith(key + "-") for key in EXPECTED_HELM) and
                                 _pod_ready(pod, True) for name, pod in pods.items()) or \
            any(not any(name.startswith(key + "-") for name in pods) for key in EXPECTED_HELM):
        problems.append("P4_PODS_UNREADY")
    helm = snapshot.get("helm")
    if not isinstance(helm, list) or any(not isinstance(h, dict) for h in helm):
        problems.append("HELM_INVENTORY_INVALID")
    else:
        named = {h.get("name"): h for h in helm}
        if len(named) != len(helm) or set(named) != set(EXPECTED_HELM):
            problems.append("HELM_RELEASE_SET_MISMATCH")
        else:
            for name, (revision, chart, version) in EXPECTED_HELM.items():
                h = named[name]
                # Helm list JSON encodes revision as a decimal string in some
                # versions, while other consumers use an integer. Accept only
                # the exact expected value, never a float, bool or padded text.
                observed = h.get("revision")
                exact_revision = ((type(observed) is int and observed == revision) or
                                  (type(observed) is str and observed == str(revision)))
                if (h.get("namespace") != P4_NAMESPACE or h.get("status") != "deployed" or
                        not exact_revision or h.get("chart") != chart or
                        h.get("app_version") != version):
                    problems.append("HELM_BASELINE_MISMATCH")
                    break
    if not osm_pods or not all(_pod_ready(p, False) for p in osm_pods.values()):
        problems.append("OSM_PODS_UNREADY")
    for component in ("nbi-", "lcm-", "ro-"):
        if sum(name.startswith(component) for name in osm_pods) != 1:
            problems.append("OSM_CONTROL_COMPONENT_MISSING")
            break
    for name, port in (("nbi", 9999), ("ro", 9090)):
        svc = osm_services.get(name, {})
        spec = svc.get("spec", {}) if isinstance(svc, dict) else {}
        if (spec.get("type") != "ClusterIP" or
                not any(isinstance(p, dict) and p.get("port") == port for p in spec.get("ports", []))):
            problems.append("OSM_SERVICE_MISMATCH")
            break
    return {
        "schema": "5g-kubernetes/p5-target-observation",
        "schema_version": 1,
        "status": "OBSERVED" if not problems else "BLOCKED",
        "target": "dell-p4",
        "kubernetes_identity_matches_expected": not any(x in problems for x in
            ("WRONG_HOST", "WRONG_CONTEXT", "WRONG_API_SERVER")),
        "p4_baseline_matches_expected": not any(x.startswith(("P4_", "HELM_")) for x in problems),
        "osm_kubernetes_ready": not any(x.startswith("OSM_") for x in problems),
        "osm_nbi_ns_state_verified": False,
        "osm_vim_registration_verified": False,
        "deployment_authorized": False,
        "catalog_publication_authorized": False,
        "mutations_performed": False,
        "helm_revisions": {name: values[0] for name, values in EXPECTED_HELM.items()}
            if "HELM_BASELINE_MISMATCH" not in problems and "HELM_RELEASE_SET_MISMATCH" not in problems and
                "HELM_INVENTORY_INVALID" not in problems else {},
        "blockers": sorted(set(problems) | {
            "NO_OSM_NBI_NS_VERIFICATION", "NO_VIM_BINDING_VERIFICATION",
            "NO_SIGNED_DELL_RELEASE", "NO_OPERATOR_DEPLOYMENT_APPROVAL",
            "P4_PRODUCTION_UPGRADE_GATE_CLOSED"
        }),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--observe", action="store_true", help="Explicit read-only kubectl/helm inspection after local identity checks")
    mode.add_argument("--snapshot", type=Path, help="Evaluate an operator-provided local JSON snapshot without any commands")
    args = parser.parse_args(argv)
    try:
        if args.observe:
            record = collect()
        else:
            path = args.snapshot
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_OUTPUT:
                raise ObservationError("SNAPSHOT_UNAVAILABLE")
            record = _read_json(path.read_text(encoding="utf-8"))
        result = evaluate(record)
    except (ObservationError, OSError, ValueError, TypeError, AttributeError) as error:
        result = {"status": "REJECTED", "reason_code": type(error).__name__,
                  "deployment_authorized": False, "mutations_performed": False}
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
