#!/usr/bin/env python3
"""Read-only H-CRAN staging preflight; NEVER grants production authorization.

Consumes Kubernetes read-only snapshots and optional single-node pressure.
Live mode runs ONLY kubectl get; no exec, logs, apply, delete, patch, scale,
restart, OSM call, subscriber or secret access. Output excludes config contents.
A ready result permits consideration of a separately approved staging TEST,
not deployment or 3GPP conformance. Multi-node PSI needs independent checks.
"""
import argparse
import datetime as dt
import math
import json
import pathlib
import re
import subprocess
import sys

EXPECTED = ("hcran-oai-cu", "hcran-oai-macro", "hcran-oai-small")
IMAGE_PIN = re.compile(r"@sha256:[0-9a-fA-F]{64}$")
E2 = re.compile(r"\b(?:e2_agent|near_ric_ip_addr|flexric|near[-_]?rt[-_]?ric)\b", re.I)
NEA2 = re.compile(r'ciphering_algorithms\s*=\s*\(\s*"nea2"\s*\)\s*;', re.I)
NIA2 = re.compile(r'integrity_algorithms\s*=\s*\(\s*"nia2"\s*\)\s*;', re.I)
PRESSURE_THRESHOLDS = {
    "cpu_some_avg10": 25.0,
    "io_some_avg10": 10.0,
    "load_per_cpu": 1.50,
}


def _items(value, kind):
    if not isinstance(value, dict):
        raise ValueError(f"{kind} must be a JSON object")
    items = value.get("items", None)
    if not isinstance(items, list):
        raise ValueError(f"{kind}.items must be an array")
    return items


def _name(item):
    return item.get("metadata", {}).get("name", "")


def _exact(items, name, label):
    matches = [item for item in items if _name(item) == name]
    if len(matches) != 1:
        raise ValueError(f"{label} {name} expected exactly once, found {len(matches)}")
    return matches[0]


def _pod_match(pod, name):
    labels = pod.get("metadata", {}).get("labels", {})
    return labels.get("app") == name


def _ready_pod(pod):
    if pod.get("status", {}).get("phase") != "Running":
        return False
    conditions = pod.get("status", {}).get("conditions", [])
    if not any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in conditions):
        return False
    statuses = pod.get("status", {}).get("containerStatuses", [])
    return (len(statuses) == 1 and statuses[0].get("ready") is True
            and statuses[0].get("state", {}).get("running") is not None)


def _protocol_ports(svc):
    return {(p.get("protocol", "TCP"), p.get("port"))
            for p in svc.get("spec", {}).get("ports", [])}


def assess(pods, deployments, services, cu_config, pressure):
    """Fail closed on missing inputs; never return a production GO."""
    blockers = []
    findings = {}
    try:
        pds = _items(pods, "pods")
        deps = _items(deployments, "deployments")
        svcs = _items(services, "services")
        if len(pds) > 100:
            raise ValueError("Pod inventory exceeds bounded size")
    except (ValueError, TypeError) as error:
        return {"status": "BLOCKED", "production_decision": "NO_GO",
                "production_authorized": False, "blockers": [str(error)]}
    for name in EXPECTED:
        reasons = []
        try:
            dep = _exact(deps, name, "Deployment")
            svc = _exact(svcs, name, "Service")
            if dep.get("spec", {}).get("replicas") != 1:
                reasons.append("Deployment desired replica count is not exactly one")
            status = dep.get("status", {})
            generation = dep.get("metadata", {}).get("generation")
            observed = status.get("observedGeneration")
            if (not isinstance(generation, int) or isinstance(generation, bool)
                    or not isinstance(observed, int) or isinstance(observed, bool)
                    or observed < generation):
                reasons.append("Deployment controller has not observed latest generation")
            if (status.get("readyReplicas") != 1 or status.get("availableReplicas") != 1
                    or status.get("updatedReplicas") != 1
                    or status.get("unavailableReplicas", 0) not in (None, 0)):
                reasons.append("Deployment rollout is not current, ready and available (1/1)")
            template_spec = dep.get("spec", {}).get("template", {}).get("spec", {})
            if template_spec.get("automountServiceAccountToken") is not False:
                reasons.append("Deployment must disable service account token automount")
            if template_spec.get("hostNetwork", False) or template_spec.get("hostPID", False):
                reasons.append("Deployment requests host-network or host-PID access")
            if template_spec.get("initContainers") or template_spec.get("ephemeralContainers"):
                reasons.append("Unexpected init/ephemeral containers in RAN Deployment")
            containers = template_spec.get("containers", [])
            if len(containers) != 1:
                reasons.append("Deployment must have exactly one RAN container")
                spec = {}
            else:
                spec = containers[0]
            image = spec.get("image", "")
            if not isinstance(image, str) or not IMAGE_PIN.search(image):
                reasons.append("Workload image not pinned by immutable OCI sha256 digest")
            security = spec.get("securityContext", {})
            if security.get("privileged") is not False:
                reasons.append("Container is privileged or privilege is unspecified")
            if security.get("allowPrivilegeEscalation") is not False:
                reasons.append("Privilege escalation not explicitly disabled")
            if security.get("capabilities", {}).get("drop") != ["ALL"]:
                reasons.append("Linux capabilities not explicitly dropped")
            if security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
                reasons.append("RuntimeDefault seccomp profile not configured")
            matching = [pod for pod in pds if _pod_match(pod, name)]
            if len(matching) != 1:
                reasons.append(f"Expected one running Pod for role, found {len(matching)}")
            else:
                pod = matching[0]
                if not _ready_pod(pod):
                    reasons.append("Pod/containers not Ready/running")
                statuses = pod.get("status", {}).get("containerStatuses", [])
                if statuses and statuses[0].get("restartCount", -1) != 0:
                    reasons.append("Container has restarts in current observation")
                pod_spec = pod.get("spec", {})
                pod_containers = pod_spec.get("containers", [])
                if (len(pod_containers) != 1 or
                        pod_containers[0].get("image") != image or
                        pod_containers[0].get("name") != spec.get("name")):
                    reasons.append("Running Pod image/identity differs from desired Deployment template")
                elif pod_containers[0].get("securityContext") != security:
                    reasons.append("Running Pod effective securityContext differs from declared Deployment")
                if pod_spec.get("automountServiceAccountToken") is not False:
                    reasons.append("Running Pod may mount a Kubernetes service account token")
                if pod_spec.get("hostNetwork", False) or pod_spec.get("hostPID", False):
                    reasons.append("Running Pod has host-network or host-PID access")
                if pod_spec.get("initContainers") or pod_spec.get("ephemeralContainers"):
                    reasons.append("Running Pod contains unexpected init/ephemeral containers")
                if any(v.get("projected", {}).get("sources") and
                       any("serviceAccountToken" in source
                           for source in v.get("projected", {}).get("sources", []))
                       for v in pod_spec.get("volumes", [])):
                    reasons.append("Running Pod contains a projected Kubernetes API token volume")
                if pod.get("metadata", {}).get("deletionTimestamp"):
                    reasons.append("Running Pod has pending deletion timestamp")
            selector = svc.get("spec", {}).get("selector", {})
            if selector.get("app") != name or len(selector) != 1:
                reasons.append("Service selector does not exclusively target its H-CRAN role")
            if svc.get("spec", {}).get("type", "ClusterIP") != "ClusterIP":
                reasons.append("H-CRAN Service type is not internal ClusterIP")
            ports = _protocol_ports(svc)
            expected = {("SCTP", 38472), ("UDP", 38472)}
            if name == "hcran-oai-cu":
                expected |= {("SCTP", 38412), ("UDP", 2152)}
            if not expected.issubset(ports):
                reasons.append("Required F1/NG transport Service ports absent")
            if any(port in (36421, 36422) for _, port in ports):
                reasons.append("Unexpected E2 port in H-CRAN Service")
        except (ValueError, KeyError, AttributeError, TypeError) as error:
            reasons.append(type(error).__name__ + ": " + str(error))
        if reasons:
            blockers.extend([name + ": " + reason for reason in reasons])
        findings[name] = {"preflight": "PASS" if not reasons else "BLOCKED",
                          "issue_count": len(reasons)}
    if not isinstance(cu_config, dict):
        blockers.append("CU ConfigMap was not provided")
    else:
        cfg = cu_config.get("data", {}).get("gnb.conf")
        if not isinstance(cfg, str) or not cfg:
            blockers.append("CU gnb.conf missing")
        else:
            if E2.search(cfg):
                blockers.append("CU gnb.conf contains a Near-RT RIC/E2 agent directive")
            if not NEA2.search(cfg):
                blockers.append("CU security configuration is not strictly NEA2")
            if not NIA2.search(cfg):
                blockers.append("CU security configuration is not strictly NIA2")
    pressure_ok = False
    if not isinstance(pressure, dict):
        blockers.append("Independent staging-node CPU/IO pressure evidence not provided")
    else:
        observations = pressure.get("nodes")
        if not isinstance(observations, list) or not observations:
            blockers.append("No staging-node resource observations")
        else:
            observed_names = set()
            pressure_ok = True
            for p in observations:
                if not isinstance(p, dict) or not p.get("node"):
                    blockers.append("Node pressure entry missing node identity")
                    pressure_ok = False
                    continue
                if p["node"] in observed_names:
                    blockers.append("Duplicate node pressure observation")
                    pressure_ok = False
                observed_names.add(p["node"])
                try:
                    stamp = dt.datetime.fromisoformat(
                        str(p.get("collected_utc", "")).replace("Z", "+00:00"))
                    age = (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds()
                    if stamp.tzinfo is None or not -30 <= age <= 300:
                        raise ValueError("Node sample must be UTC-offset-aware and less than five minutes old")
                except (ValueError, TypeError, OverflowError):
                    blockers.append(f"{p['node']}: missing, future or stale node pressure timestamp")
                    pressure_ok = False
                for field, maximum in PRESSURE_THRESHOLDS.items():
                    value = p.get(field)
                    if (isinstance(value, bool) or not isinstance(value, (float, int))
                            or not math.isfinite(value) or not (0 <= value < maximum)):
                        blockers.append(f"{p['node']}: {field} missing, invalid or above staging threshold {maximum}")
                        pressure_ok = False
            pod_nodes = {p.get("spec", {}).get("nodeName") for p in pds
                         if p.get("metadata", {}).get("labels", {}).get("app") in EXPECTED}
            if not pod_nodes or None in pod_nodes or not pod_nodes.issubset(observed_names):
                blockers.append("Not all RAN Pod nodes have resource-pressure observations")
                pressure_ok = False
    return {
        "schema": "hcran-staging-inventory-v1",
        "scope": "read-only single-observation preflight, no actual UE procedure tested",
        "hcran_ric_required": False,
        "node_pressure_complete": pressure_ok,
        "findings": findings,
        "status": "READY_FOR_ISOLATED_STAGING_TEST_ONLY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "production_authorized": False,
        "production_decision": "NO_GO",
        "limitations": [
            "No live F1AP/NGAP/PDU/RRC procedure decoded or replayed",
            "No independent Prometheus scrape, mobility, UE recovery or SLO evidence verified",
            "No actual OSM upgrade/rollback or human release approval verified",
        ],
    }


def kubectl_get(context, namespace, kind, *names):
    args = ["kubectl", "--context", context, "--namespace", namespace,
            "get", kind, *names, "-o", "json"]
    p = subprocess.run(args, text=True, capture_output=True, timeout=25)
    if p.returncode:
        # Never print kubectl stderr to avoid accidental credential disclosure.
        raise RuntimeError(f"kubectl read-only get {kind} failed (exit {p.returncode})")
    if len(p.stdout) > 8 * 1024 * 1024:
        raise RuntimeError("Kubernetes snapshot exceeds 8 MiB")
    return json.loads(p.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshots", type=pathlib.Path,
                      help="Offline JSON fixture with pods, deployments, services and cu_config")
    mode.add_argument("--live-read-only", action="store_true",
                      help="Run bounded kubectl GET operations only; never mutates cluster")
    ap.add_argument("--context", help="Required explicit kube context for live reads")
    ap.add_argument("--namespace", help="Required explicit namespace for live reads")
    ap.add_argument("--pressure", type=pathlib.Path,
                    help="Separate read-only JSON with per-node PSI resource observations")
    ap.add_argument("--report", required=True, type=pathlib.Path)
    args = ap.parse_args(argv)

    if args.live_read_only:
        if not args.context or not args.namespace:
            ap.error("--context and --namespace required for --live-read-only")
        if args.namespace in ("default", "kube-system"):
            ap.error("Refusing default or kube-system namespace for H-CRAN preflight")
        data = {
            "pods": kubectl_get(args.context, args.namespace, "pods", "-l", "ran-type=hcran"),
            "deployments": kubectl_get(args.context, args.namespace, "deployments", *EXPECTED),
            "services": kubectl_get(args.context, args.namespace, "services", *EXPECTED),
            "cu_config": kubectl_get(args.context, args.namespace, "configmap", "hcran-oai-cu-config"),
        }
    else:
        data = json.loads(args.snapshots.read_text(encoding="utf-8"))
    pressure = json.loads(args.pressure.read_text(encoding="utf-8")) if args.pressure else None
    report = assess(data.get("pods"), data.get("deployments"),
                    data.get("services"), data.get("cu_config"), pressure)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_ISOLATED_STAGING_TEST_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
