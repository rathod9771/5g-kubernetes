#!/usr/bin/env python3
"""Generate an isolated srsRAN+Open5GS CI topology from repository srsRAN Helm values.
No Kubernetes/OSM activity, no OAI images, no source compilation, no subscribers.
"""
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
PROFILES = {
    "cran": ("", "", [""]),
    "vcran": ("values-vcran.yaml", "values-vcran.yaml", ["values-vcran.yaml"]),
    "fran": ("values-fran.yaml", "values-fran.yaml", ["values-fran.yaml"]),
    "cloud-ran": ("values-cloudran.yaml", "values-cloudran.yaml", ["values-cloudran.yaml"]),
    "hcran": ("values-hcran.yaml", "", ["values-hcran-macro.yaml", "values-hcran-small.yaml"]),
    "oran": ("", "", [""]),
}
CU_ADDR = "172.30.250.10"
AMF_ADDR = "172.30.250.20"
NRF_ADDR = "172.30.250.30"
DU_ADDRS = ["172.30.250.11", "172.30.250.12"]

def check(condition, why):
    if not condition:
        raise RuntimeError(why)

def render_chart(chart, overlay, key):
    command = ["helm", "template", "l3-" + key, str(ROOT / chart)]
    if overlay:
        command.extend(["-f", str(ROOT / chart / overlay)])
    if "srsran-oran" not in chart:
        command += ["--set", "image.reference=reviewed-ci-prebuilt-image",
                    "--set", "image.pullPolicy=Never"]
    output = subprocess.run(command, check=True, capture_output=True, text=True, timeout=25)
    documents = list(yaml.safe_load_all(output.stdout))
    configmaps = [d for d in documents if isinstance(d, dict) and d.get("kind") == "ConfigMap"]
    check(len(configmaps) == 1, "CHART_CONFIGMAP_NOT_UNIQUE")
    config = configmaps[0]["data"]
    check(key + ".yml" in config, "MISSING_" + key.upper() + "_CONFIG")
    obj = yaml.safe_load(config[key + ".yml"])
    check(isinstance(obj, dict), "INVALID_" + key.upper() + "_YAML")
    return obj, [d for d in documents if isinstance(d, dict)]

def dump(path, value):
    path.write_text(yaml.safe_dump(value, sort_keys=False))

def main():
    check(len(sys.argv) == 3, "USAGE: prepare.py <profile> <output_dir>")
    profile, dest = sys.argv[1], pathlib.Path(sys.argv[2])
    check(profile in PROFILES, "UNKNOWN_PROFILE")
    check(not dest.exists(), "DEST_ALREADY_EXISTS")
    dest.mkdir(parents=True)
    cuov, _, duovs = PROFILES[profile]
    cu_chart = "helm/srsran-oran/cu" if profile == "oran" else "helm/cran-srsran/cu"
    du_chart = "helm/srsran-oran/du" if profile == "oran" else "helm/cran-srsran/du"
    cu, cu_render = render_chart(cu_chart, cuov, "cu")
    check(any(d.get("kind") == "Deployment" for d in cu_render), "NO_CU_DEPLOYMENT")
    cp = cu["cu_cp"]
    check(cp["amf"]["supported_tracking_areas"][0]["tac"] == 1, "CU_TAC_NOT_EXPECTED")
    check(cp["amf"]["supported_tracking_areas"][0]["plmn_list"][0]["plmn"] == "20893", "CU_PLMN_NOT_EXPECTED")
    check(cp["amf"]["supported_tracking_areas"][0]["plmn_list"][0]["tai_slice_support_list"][0]["sd"] == 66051,
          "CU_SLICE_NOT_EXPECTED")
    cu_txt = yaml.safe_dump(cu)
    check(cu_txt.count("AMF_IP") == 1 and cu_txt.count("POD_IP") >= 2, "CU_ADDRESS_TEMPLATE_MISMATCH")
    cu = yaml.safe_load(cu_txt.replace("AMF_IP", AMF_ADDR).replace("POD_IP", CU_ADDR))
    cu["log"]["all_level"] = "info"
    dump(dest / "cu.yml", cu)
    du_configs = []
    for i, overlay in enumerate(duovs):
        du, du_render = render_chart(du_chart, overlay, "du")
        check(any(d.get("kind") == "Deployment" for d in du_render), "NO_DU_DEPLOYMENT")
        check(du["cell_cfg"]["plmn"] == "20893" and du["cell_cfg"]["tac"] == 1, "DU_PLMN_TAC_MISMATCH")
        du_text = yaml.safe_dump(du)
        check(du_text.count("CU_IP") == 1 and du_text.count("POD_IP") >= 1, "DU_ADDRESS_TEMPLATE_MISMATCH")
        du = yaml.safe_load(du_text.replace("CU_IP", CU_ADDR).replace("POD_IP", DU_ADDRS[i]))
        du["log"]["all_level"] = "info"
        dump(dest / ("du" + str(i + 1) + ".yml"), du)
        du_configs.append({
            "address": DU_ADDRS[i],
            "pci": du["cell_cfg"]["pci"],
            "bandwidth_mhz": du["cell_cfg"]["channel_bandwidth_MHz"],
            "gnb_du_id": du.get("gnb_du_id"),
        })
    check(len(set(x["address"] for x in du_configs)) == len(du_configs), "NONUNIQUE_DUS")
    if profile == "hcran":
        check(len(du_configs) == 2 and {x["gnb_du_id"] for x in du_configs} == {100, 101},
              "HCRAN_TWO_DU_ID_CONTRACT")
        check(cp.get("max_nof_dus") == 2, "HCRAN_CU_LIMIT")
        check(not any(cu.get(k, {}).get("enabled", False) for k in ("ric", "e2Agent", "e2")),
              "HCRAN_RIC_MUST_BE_DISABLED")
    else:
        check(len(du_configs) == 1, "UNEXPECTED_DU_COUNT")
    nrf = {
        "logger": {"level": "info"}, "global": {"max": {"ue": 8, "peer": 8}},
        "nrf": {"serving": [{"plmn_id": {"mcc": "208", "mnc": "93"}}],
                "sbi": {"server": [{"address": NRF_ADDR, "port": 7777}]}}
    }
    amf = {
        "logger": {"level": "info"}, "global": {"max": {"ue": 8, "peer": 8}},
        "amf": {
            "sbi": {"server": [{"address": AMF_ADDR, "port": 7777}],
                    "client": {"nrf": [{"uri": "http://" + NRF_ADDR + ":7777"}]}},
            "ngap": {"server": [{"address": AMF_ADDR, "port": 38412}]},
            "guami": [{"plmn_id": {"mcc": "208", "mnc": "93"},
                       "amf_id": {"region": 2, "set": 1, "pointer": 0}}],
            "tai": [{"plmn_id": {"mcc": "208", "mnc": "93"}, "tac": 1}],
            "plmn_support": [{"plmn_id": {"mcc": "208", "mnc": "93"},
                              "s_nssai": [{"sst": 1, "sd": "010203"}]}],
            "security": {"integrity_order": ["NIA2", "NIA1", "NIA0"],
                         "ciphering_order": ["NEA0", "NEA1", "NEA2"]},
            "network_name": {"full": "L3-CI-SYNTHETIC", "short": "L3-CI"},
            "amf_name": "l3-ci-amf",
            "time": {"t3512": {"value": 540}}
        }}
    dump(dest / "nrf.yaml", nrf)
    dump(dest / "amf.yaml", amf)
    report = {
        "schema": 1,
        "profile": profile,
        "cu_source_chart": cu_chart,
        "du_source_chart": du_chart,
        "srsran_image": "softwareradiosystems/srsran-project:release_avx2-4bf1543936__2026-02-16",
        "open5gs_image": "gradiant/open5gs:2.7.5",
        "amf_address": AMF_ADDR,
        "cu_address": CU_ADDR,
        "du_configs": du_configs,
        "plmn": "20893", "tac": 1, "sst": 1, "sd": "010203",
        "no_ue": True,
        "no_oai": True,
        "no_local_cluster": True,
        "scope": "NG_SETUP_AND_F1_SETUP_ONLY"
    }
    (dest / "plan.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PREPARED_ISOLATED_" + profile.upper() + "_CONFIG_FROM_TRACKED_SRSRAN_CHARTS")

if __name__ == "__main__":
    main()
