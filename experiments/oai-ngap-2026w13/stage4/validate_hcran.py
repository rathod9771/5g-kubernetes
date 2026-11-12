#!/usr/bin/env python3
"""Read-only H-CRAN CU + 2-DU static readiness audit. No deployment."""
import argparse
import datetime
import json
import pathlib
import re
import sys
import traceback

import yaml


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def documents(path):
    return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))


def find(documents_, kind, name):
    hits = [x for x in documents_ if isinstance(x, dict) and
            x.get("kind") == kind and x.get("metadata", {}).get("name") == name]
    require(len(hits) == 1, f"expected exactly one {kind}/{name}")
    return hits[0]


def setting(conf, key, expected):
    m = re.search(r"(?m)^\s*" + re.escape(key) + r"\s*=\s*([^;,\n]+)", conf)
    require(m is not None, f"missing {key}")
    actual = m.group(1).strip().strip('"')
    require(actual == str(expected), f"{key}: expected {expected}, got {actual}")


def has_port(service, port, protocol):
    return any(x["port"] == port and x["protocol"] == protocol
               for x in service["spec"]["ports"])


def run(args):
    root = pathlib.Path(args.repository)
    rendered = pathlib.Path(args.rendered)
    refs = json.loads((root / "config/reference-versions.json").read_text())["ran_images"]["components"]
    scenarios = json.loads((root / "config/scenarios.json").read_text())["scenarios"]
    scenario = next(x for x in scenarios if x["key"] == "hcran-oai")
    cu_ref = refs["oai-cu"]["repository"] + ":" + refs["oai-cu"]["tag"]
    du_ref = refs["oai-du"]["repository"] + ":" + refs["oai-du"]["tag"]
    check_results = []

    def check(label, action):
        try:
            action()
            check_results.append({"name": label, "result": "PASS"})
            print("PASS:", label, flush=True)
        except Exception as e:
            check_results.append({"name": label, "result": "FAIL", "detail": str(e)})
            print("FAIL:", label, str(e), flush=True)

    parts = {}
    for name in ("cu", "macro", "small"):
        docs = documents(rendered / (name + ".yaml"))
        fullname = "hcran-oai-" + name
        parts[name] = {
            "deploy": find(docs, "Deployment", fullname),
            "config": find(docs, "ConfigMap", fullname + "-config"),
            "service": find(docs, "Service", fullname),
        }
    cu = parts["cu"]
    macro = parts["macro"]
    small = parts["small"]

    def scenario_check():
        require({x["release_name"] for x in scenario["charts"]} ==
                {"hcran-oai-cu", "hcran-oai-macro", "hcran-oai-small"}, "missing canonical components")
        require(all(x["source"].startswith("helm/cran-oai/") for x in scenario["charts"]),
                "non-canonical chart")
        require(scenario["deployment_mechanism"] == "osm", "not OSM-managed")
    check("canonical OSM CU plus 2 DUs", scenario_check)

    def identity_check():
        setting(cu["config"]["data"]["gnb.conf"], "gNB_ID", 3585)
        setting(cu["config"]["data"]["gnb.conf"], "nr_cellid", "58736641L")
        identity = set()
        for name in ("macro", "small"):
            values = yaml.safe_load((root / "helm/cran-oai/du" /
                                     ("values-hcran-" + name + ".yaml")).read_text())
            radio = values["radio"]
            conf = parts[name]["config"]["data"]["gnb.conf"]
            for key, val in (("gNB_ID", 3585), ("gNB_DU_ID", radio["duId"]),
                             ("nr_cellid", str(radio["cellId"]) + "L"),
                             ("physCellId", radio["pci"]),
                             ("ssPBCH_BlockPower", radio["ssbPower"])):
                setting(conf, key, val)
            identity.add((radio["duId"], radio["cellId"], radio["pci"]))
        require(len(identity) == 2, "macro/small identity collision")
    check("unique macro/small gNB-DU, cell ID and PCI", identity_check)

    def f1_and_core():
        conf = cu["config"]["data"]["gnb.conf"]
        setting(conf, "tr_s_preference", "f1")
        setting(conf, "remote_s_address", "0.0.0.0")
        setting(conf, "local_s_portd", 38472)
        require("amf-ngap-stable" in cu["config"]["data"]["cu-init.sh"],
                "stable AMF discovery missing")
        for port, protocol in ((38412, "SCTP"), (38472, "SCTP"),
                               (38472, "UDP"), (2152, "UDP")):
            require(has_port(cu["service"], port, protocol),
                    f"missing CU {protocol}/{port}")
        for name in ("macro", "small"):
            part = parts[name]
            du_conf = part["config"]["data"]["gnb.conf"]
            setting(du_conf, "remote_n_address", "CU_IP")
            setting(du_conf, "remote_n_portd", 38472)
            require("hcran-oai-cu" in part["config"]["data"]["du-init.sh"],
                    f"{name} missing CU discovery")
            for protocol in ("SCTP", "UDP"):
                require(has_port(part["service"], 38472, protocol),
                        f"{name} missing {protocol}/38472")
    check("rendered F1-C/F1-U, NGAP and stable AMF endpoints", f1_and_core)

    def image_bindings():
        for name, image in (("cu", cu_ref), ("macro", du_ref), ("small", du_ref)):
            dep = parts[name]["deploy"]
            container = dep["spec"]["template"]["spec"]["containers"][0]
            require(dep["spec"]["replicas"] == 1, f"{name} replicas changed")
            require(container["image"] == image, f"{name} image policy drift")
            require(container["imagePullPolicy"] == "IfNotPresent", f"{name} pull-policy drift")
            require(container.get("resources", {}).get("limits", {}).get("memory") == "2Gi",
                    f"{name} memory limit changed")
            annotations = dep["spec"]["template"]["metadata"]["annotations"]
            require(annotations.get("sidecar.istio.io/inject") == "false",
                    f"{name} unexpected sidecar")
    check("unchanged CU/DU image policy and resource envelopes", image_bindings)

    def experimental_image():
        record = json.loads(pathlib.Path(args.image_inspect).read_text())
        require(len(record) == 1, "expected one image")
        require(record[0]["Architecture"] == "amd64", "image architecture mismatch")
        require(record[0]["Id"].startswith("sha256:"), "image ID missing")
        require(any("stage3-portable-4" in x for x in record[0]["RepoTags"]),
                "loaded artifact is not verified Stage 3 image")
        workflow = (root / ".github/workflows/oai-ngap-2026w13-stage3.yml").read_text()
        require("--noavx512 --noavx2" in workflow and "ngap-retry-v2.patch" in workflow,
                "Stage 3 source/build provenance changed")
    check("portable Stage 3 image and source provenance", experimental_image)

    blockers = [
        {"id": "E2_KPM_RC", "reason": "Stage 3 compile did not enable --build-e2; live E2 Setup/KPM/RC untested"},
        {"id": "TWO_DU_F1", "reason": "No macro DU ID 100 or small DU ID 101 ran during Stage 3"},
        {"id": "OAI_RELEASE_INTEROP", "reason": f"CU {cu_ref} and DU {du_ref} cross-release interoperability untested"},
        {"id": "UE_PDU", "reason": "UE registration and PDU session never tested"},
        {"id": "IMAGE_DISTRIBUTION", "reason": "Experimental archive is temporary; no verified OSM-controlled rollout and rollback"},
        {"id": "LIVE_CONTEXT", "reason": "No access to actual HP Kubernetes, OSM, Open5GS health or pod specs"},
    ]
    passed = bool(check_results) and all(x["result"] == "PASS" for x in check_results)
    output = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stage": "4A — isolated offline H-CRAN profile readiness",
        "stage3_success_run": 43157479388,
        "static_checks_passed": passed,
        "production_decision": "NO_GO",
        "deployment_authorized": False,
        "checks": check_results,
        "blockers": blockers,
    }
    pathlib.Path(args.report).write_text(json.dumps(output, indent=2) + "\n")
    print("ASSESSMENT:", json.dumps({"static_pass": passed, "production": "NO_GO",
                                    "remaining_blockers": len(blockers)}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--rendered", required=True)
    parser.add_argument("--image-inspect", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except Exception:
        path = pathlib.Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"static_checks_passed": False,
                                    "production_decision": "NO_GO",
                                    "fatal_error": traceback.format_exc()}, indent=2) + "\n")
        traceback.print_exc()
        sys.exit(1)
