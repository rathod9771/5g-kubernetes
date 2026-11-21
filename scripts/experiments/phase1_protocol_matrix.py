#!/usr/bin/env python3
"""Phase 1: read-only, offline multi-RAN protocol-interface *configuration* audit.

Helm templates never touch Kubernetes or execute UE/network procedures.
A green CI run means the audit and its fail-closed static assertions ran;
it NEVER certifies real NGAP, F1AP, NAS, PDU, or user-plane packet exchange.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required (pinned by CI)") from exc

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config/scenarios.json"
NOT_RUN = ("NGAP_SETUP", "F1_SETUP", "NAS_REGISTRATION",
           "PDU_SESSION", "F1U_TRAFFIC", "NGU_TRAFFIC", "UE_RECOVERY")
MANDATORY = ("NGAP_STATIC", "F1AP_STATIC")


def load_registry(path=REGISTRY):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = obj["scenarios"]
    if not isinstance(entries, list):
        raise ValueError("Registry scenario list is missing")
    names = [e["key"] for e in entries]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate scenario registry key")
    return {e["key"]: e for e in entries}


def ports(service):
    return {(p.get("protocol", "TCP"), p.get("port"))
            for p in service.get("spec", {}).get("ports", [])}


def single_doc(docs, kind):
    hits = [item for item in docs if isinstance(item, dict) and item.get("kind") == kind]
    if len(hits) != 1:
        raise ValueError(f"Expected exactly one {kind}; found {len(hits)}")
    return hits[0]


def check_config(docs, role, implementation):
    """Check only declared interfaces; do not claim packets or protocol success."""
    service = single_doc(docs, "Service")
    config = single_doc(docs, "ConfigMap")
    deployment = single_doc(docs, "Deployment")
    source = config.get("data", {})
    if not isinstance(source, dict):
        raise ValueError("ConfigMap data must be a mapping")
    conf = "\n".join(x for x in source.values() if isinstance(x, str))
    if not conf:
        raise ValueError("Missing rendered CU/DU configuration")
    container_list = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if len(container_list) != 1:
        raise ValueError("Expected exactly one RAN container")
    if not isinstance(container_list[0].get("image"), str) or not container_list[0]["image"]:
        raise ValueError("Missing container image identity")
    p = ports(service)
    if role == "cu":
        has_ngap_config = ("amf_ip_address" in conf) if implementation.lower() == "oai" else (
            "cu_cp:" in conf and "amf:" in conf)
        has_f1ap_config = ("local_s_portd" in conf) if implementation.lower() == "oai" else "f1ap:" in conf
        return {
            "NGAP_STATIC": "CONFIGURED" if ("SCTP", 38412) in p and has_ngap_config else "CONFIG_GAP",
            "F1AP_STATIC": "CONFIGURED" if ("SCTP", 38472) in p and has_f1ap_config else "CONFIG_GAP",
            "NGU_STATIC": "SERVICE_PORT_PRESENT" if ("UDP", 2152) in p else "SERVICE_PORT_ABSENT",
            "config": conf,
        }
    if role != "du":
        raise ValueError("Unsupported role")
    has_f1ap_config = ("MACRLCs" in conf and "remote_n_address" in conf) if implementation.lower() == "oai" else (
        "f1ap:" in conf and "cu_cp_addr:" in conf)
    has_rf = ("rfsimulator" in conf) if implementation.lower() == "oai" else "device_driver: zmq" in conf
    return {
        "F1AP_STATIC": "CONFIGURED" if ("SCTP", 38472) in p and has_f1ap_config else "CONFIG_GAP",
        "F1U_STATIC": "SERVICE_PORT_PRESENT" if ("UDP", 38472) in p or ("UDP", 2152) in p else "SERVICE_PORT_ABSENT",
        "RF_BACKEND": "RF_SIM_CONFIGURATION_PRESENT" if has_rf else "RF_SIM_CONFIGURATION_NOT_DETECTED",
        "config": conf,
    }


def render_chart(entry, chart, role, position, root=ROOT):
    source = chart["source"]
    root = Path(root)
    if not (root / source / "Chart.yaml").is_file():
        raise ValueError("Missing registered Helm chart: " + source)
    release = ("p1-" + entry["key"] + "-" + role + str(position)).replace("_", "-")
    cmd = ["helm", "template", release, str(root / source)]
    for path in chart.get("values_files", []):
        if not (root / path).is_file():
            raise ValueError("Missing registered Helm profile: " + path)
        cmd += ["-f", str(root / path)]
    # Synthetic image reference for OFFLINE rendering only, never pulled/deployed.
    cmd += ["--set-string", "image.reference=example.invalid/phase1-probe:offline",
            "--set-string", "image.pullPolicy=IfNotPresent"]
    try:
        process = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Helm executable absent or rendering timeout") from exc
    if process.returncode:
        # Do not print full manifests, ConfigMaps, environment or kubectl errors.
        raise ValueError("Helm rendering failed for " + source)
    docs = list(yaml.safe_load_all(process.stdout))
    return check_config(docs, role, entry["implementation"])


def analyze_scenario(key, entries, root=ROOT, renderer=render_chart):
    if key not in entries or key == "none":
        raise ValueError("Unknown/non-testable RAN scenario")
    entry = entries[key]
    base = {"schema": "phase1-multiran-protocol-static-v1",
            "scenario": key,
            "generation_status": entry.get("generation_status"),
            "runtime_protocol_tests": {name: "NOT_RUN" for name in NOT_RUN},
            "hp_cluster_touched": False,
            "deployment_performed": False,
            "research_claim": "CONFIGURATION_AUDIT_ONLY",
            "production_decision": "NO_GO"}
    if entry.get("generation_status") != "ready":
        base.update(status="REGISTRY_BLOCKED", blocker="SCENARIO_BLOCKED_BY_REGISTRY")
        return base
    if not entry.get("validation_policy", {}).get("split_ran"):
        base.update(status="NOT_SPLIT_RAN", blocker="EDGE_ONLY_NO_CU_DU_PROTOCOL_TEST")
        return base
    charts = entry.get("charts", [])
    if len(charts) < 2:
        raise ValueError("Split RAN must declare CU and DU")
    roles = ["cu"] + ["du"] * (len(charts) - 1)
    if key.startswith("fran-"):
        # The third chart is an additive fog service, not another DU.
        roles = ["cu", "du"]  # fog is handled by separate MEC testing.
    if len(roles) != len(charts) and not key.startswith("fran-"):
        raise ValueError("Unrecognized role mapping")
    if len(charts) > 3 or (len(charts) == 3 and not (key.startswith("hcran-") or key.startswith("fran-"))):
        raise ValueError("Unrecognized chart topology")
    selected = charts[:len(roles)]
    rendered = [renderer(entry, chart, role, idx, root) for idx, (chart, role) in enumerate(zip(selected, roles))]
    cu, dus = rendered[0], rendered[1:]
    if not dus:
        raise ValueError("No DU in split RAN profile")
    values = {"NGAP_STATIC": cu["NGAP_STATIC"],
              "F1AP_STATIC": "CONFIGURED" if cu["F1AP_STATIC"] == "CONFIGURED" and
                             all(du["F1AP_STATIC"] == "CONFIGURED" for du in dus) else "CONFIG_GAP",
              "F1U_STATIC": "SERVICE_PORT_PRESENT" if all(du["F1U_STATIC"] == "SERVICE_PORT_PRESENT" for du in dus)
                              else "SERVICE_PORT_ABSENT",
              "NGU_STATIC": cu["NGU_STATIC"],
              "RF_BACKEND": "RF_SIM_CONFIGURATION_PRESENT" if all(
                  du["RF_BACKEND"] == "RF_SIM_CONFIGURATION_PRESENT" for du in dus)
                  else "RF_SIM_CONFIGURATION_NOT_DETECTED"}
    blockers = [f"{name}_MISSING" for name in MANDATORY if values[name] != "CONFIGURED"]
    if key.startswith("hcran-"):
        if len(dus) != 2:
            blockers.append("HCRAN_REQUIRES_TWO_DUS")
        if re.search(r"\be2_agent\s*=", cu["config"], re.I) or "near_ric_ip_addr" in cu["config"]:
            blockers.append("HCRAN_FORBIDS_E2_AGENT")
        identifiers = []
        for du in dus:
            expr = r"gNB_DU_ID\s*=\s*([0-9]+)" if entry["implementation"].lower() == "oai" else r"gnb_du_id:\s*([0-9]+)"
            match = re.search(expr, du["config"])
            identifiers.append(match.group(1) if match else None)
        if any(id_ is None for id_ in identifiers) or len(set(identifiers)) != len(identifiers):
            blockers.append("HCRAN_DU_IDENTITIES_NOT_DISTINCT")
    base["static_interfaces"] = values
    base["checked_role_count"] = len(rendered)
    base["extra_component"] = "FOG_NOT_PROTOCOL_TESTED" if key.startswith("fran-") else "NONE"
    base["blockers"] = blockers
    base["status"] = "STATIC_CONFIG_PRESENT_RUNTIME_NOT_TESTED" if not blockers else "CONFIG_BLOCKED"
    return base


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    result = analyze_scenario(args.scenario, load_registry())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    # All tested eligible scenarios must expose minimum static NGAP and F1AP;
    # known blocked/edge-only profiles are reported without manufacturing PASS.
    return 2 if result["status"] == "CONFIG_BLOCKED" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError) as exc:
        print("FAIL CLOSED Phase1 static inventory: " + str(exc), file=sys.stderr)
        sys.exit(2)
