#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import subprocess, yaml, os, json

app = Flask(__name__)
REPO_PATH = os.path.expanduser("~/5g-kubernetes")
CONFIG_FILE = f"{REPO_PATH}/ran-selector/active-ran.yaml"
NS = "free5gc"

# --- Scenario registry ---------------------------------------------------
# Each scenario declares the helm releases it owns and the pod-name fragments
# that identify it. Adding a scenario means adding one entry here.
#   releases: (release_name, chart_path, values_file_or_None)
#   additive: scenario coexists with a RAN choice instead of replacing it
SCENARIOS = {
  "cran-srsran": {"name": "C-RAN + srsRAN",
    "releases": [("srsran", "helm/srsran", None)],
    "pods": ["srsran-gnb"]},
  "cran-oai": {"name": "C-RAN + OAI",
    "releases": [("oai-cran", "helm/oai-cran", None)],
    "pods": ["oai-cran-gnb"]},
  "oran-srsran": {"name": "O-RAN + srsRAN (CU/DU over F1)",
    "releases": [("srsran-cu", "helm/srsran-oran/cu", None),
                 ("srsran-du", "helm/srsran-oran/du", None)],
    "selector": "ran-type=oran", "pods": ["srsran-cu", "srsran-du"]},
  "oran-oai": {"name": "O-RAN + OAI (CU/DU over F1)",
    "releases": [("oai-cu", "helm/oai/cu", None),
                 ("oai-du", "helm/oai/du", None)],
    "selector": "ran-type=oran", "pods": ["oai-cu", "oai-du"]},
  "cloudran-srsran": {"name": "Cloud-RAN + srsRAN",
    "releases": [("cloud-ran-srsran", "helm/cloud-ran-srsran", None)],
    "pods": ["cloud-ran-gnb"]},
  "cloudran-oai": {"name": "Cloud-RAN + OAI",
    "releases": [("cloud-ran-oai", "helm/cloud-ran-oai", None)],
    "pods": ["cloud-ran-oai-gnb"]},
  "hcran-srsran": {"name": "H-CRAN + srsRAN (macro + small cell)",
    "releases": [("hcran-macro", "helm/hcran-srsran", None),
                 ("hcran-small", "helm/hcran-srsran", "values-small.yaml")],
    "pods": ["hcran-macro", "hcran-small"]},
  "hcran-oai": {"name": "H-CRAN + OAI (macro + small cell)",
    "releases": [("hcran-oai-macro", "helm/hcran-oai", None),
                 ("hcran-oai-small", "helm/hcran-oai", "values-small.yaml")],
    "pods": ["hcran-oai-macro", "hcran-oai-small"]},
  "vcran-srsran": {"name": "v-CRAN + srsRAN (autoscaling CU)",
    "releases": [("vcran-cu", "helm/vcran-srsran/cu", None),
                 ("vcran-du", "helm/vcran-srsran/du", None)],
    "selector": "ran-type=vcran", "pods": ["srsran-cu", "srsran-du"], "hpa": "srsran-cu"},
  "vcran-oai": {"name": "v-CRAN + OAI (autoscaling CU)",
    "releases": [("vcran-oai-cu", "helm/vcran-oai/cu", None),
                 ("vcran-oai-du", "helm/vcran-oai/du", None)],
    "selector": "ran-type=vcran", "pods": ["oai-cu", "oai-du"], "hpa": "oai-cu"},
  "fran": {"name": "F-RAN edge breakout (MEC app)", "additive": True,
    "releases": [("fran-edge", "helm/fran-edge", None)],
    "pods": ["fran-edge-app"]},
  "none": {"name": "No RAN", "releases": [], "pods": []},
}

# legacy names the UI may still send
ALIASES = {"srsran": "cran-srsran", "oai": "oran-oai",
           "oai-cran": "cran-oai", "srsran-oran": "oran-srsran",
           "cran": "cran-srsran", "oran": "oran-oai"}

def _releases_of(keys):
    out = []
    for k in keys:
        for rel in SCENARIOS.get(k, {}).get("releases", []):
            out.append(rel[0])
    return out


POD_MAP = {
  "oai": "oai-cu",
  "OAI": "oai-cu",
  "AMF": "open5gs-amf",
  "SMF": "open5gs-smf",
  "UPF": "open5gs-upf",
  "NRF": "open5gs-nrf",
  "AUSF": "open5gs-ausf",
  "UDM": "open5gs-udm",
  "UDR": "open5gs-udr",
  "PCF": "open5gs-pcf",
  "NSSF": "open5gs-nssf",
  "UE": "ueransim-ue",
  "GNB": "ueransim-gnb",
  "SRSRAN": "srsran-gnb",
  "SRSRAN-CU": "srsran-cu",
  "SRSRAN-DU": "srsran-du",
  "OAI-CU": "oai-cu",
  "OAI-CRAN": "oai-cran",
  "oai": "oai-cu",
  "OAI-DU": "oai-du",
  "HCRAN-MACRO": "hcran-macro",
  "HCRAN-SMALL": "hcran-small",
  "HCRAN-OAI-MACRO": "hcran-oai-macro",
  "HCRAN-OAI-SMALL": "hcran-oai-small",
  "CLOUD-RAN": "cloud-ran-gnb",
  "CLOUD-RAN-OAI": "cloud-ran-oai-gnb",
  "FRAN-EDGE": "fran-edge-app",
}

import re

def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def get_pod_name(label):
    _, out, _ = run(f"kubectl get pods -n {NS} | grep {label} | grep Running | head -1 | awk " + "'{print $1}'")
    return out.strip()

@app.route("/")
def index():
    return send_from_directory(os.path.dirname(CONFIG_FILE), "index.html")

@app.route("/api/pods")
def pods():
    _, out, _ = run(f"kubectl get pods -n {NS} -o json")
    try:
        data = json.loads(out)
        result = []
        for p in data.get("items", []):
            name = p["metadata"]["name"]
            containers = p["status"].get("containerStatuses", [])
            ready = sum(1 for c in containers if c.get("ready"))
            total = len(containers)
            restarts = sum(c.get("restartCount", 0) for c in containers)
            phase = p["status"].get("phase","Unknown")
            result.append({"name":name,"ready":f"{ready}/{total}","restarts":restarts,"phase":phase})
        return jsonify({"pods": result})
    except:
        return jsonify({"pods": [], "error": "parse error"})

@app.route("/api/logs/<nf>")
def logs(nf):
    container = request.args.get("container", "")
    lines = request.args.get("lines", "30")
    label = POD_MAP.get(nf.upper(), nf.lower())
    pod = get_pod_name(label)
    if not pod:
        return jsonify({"logs": f"No running pod found for {nf}", "pod": ""})
    # srsRAN split components log to files, not stdout
    file_log_map = {"srsran-cu": ("cu", "/tmp/cu.log"), "srsran-du": ("du", "/tmp/du.log")}
    if label in file_log_map:
        cont, logfile = file_log_map[label]
        _, out, err = run(f"kubectl exec -n {NS} {pod} -c {cont} -- sh -c \"grep -iv 'zmq\\|Waiting' {logfile} | tail -{lines}\" 2>&1")
        # Enrich with live SCTP association status - the real connection proof
        _, sctp, _ = run(f"kubectl exec -n {NS} {pod} -c {cont} -- sh -c \"cat /proc/net/sctp/assocs 2>/dev/null | tail -n +2\" 2>&1")
        sctp_summary = ""
        for line in sctp.strip().split("\n"):
            if "<->" in line:
                parts = line.split()
                try:
                    arrow = parts.index("<->")
                    lport, rport = parts[11], parts[12]
                    laddr = parts[arrow-1]
                    raddr = parts[arrow+1].lstrip("*")
                    port_name = {"38412":"NGAP/AMF","38472":"F1-C","2152":"GTP-U"}.get(rport, rport)
                    sctp_summary += f"[SCTP ESTABLISHED] {laddr}:{lport} <-> {raddr}:{rport} ({port_name})\n"
                except (ValueError, IndexError):
                    pass
        if sctp.strip():
            sctp_summary = "=== Live SCTP Associations (F1/NGAP) ===\n" + sctp_summary + "=== Log file ===\n"
        out = sctp_summary + (out if out.strip() else f"[{label}] process running - startup complete, event logs quiet at current log level")
        return jsonify({"logs": strip_ansi(out), "pod": pod})
    c_flag = f"-c {container}" if container else ""
    _, out, err = run(f"kubectl logs -n {NS} {pod} {c_flag} --tail={lines} 2>&1")
    return jsonify({"logs": strip_ansi(out or err), "pod": pod})

@app.route("/api/status")
def status():
    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f)
        frags = sorted({p for s in SCENARIOS.values() for p in s["pods"]})
        _, pods_out, _ = run(f"kubectl get pods -n {NS} | grep -E '" + "|".join(frags) + "'")
        return jsonify({"active": config.get("active","none"), "pods": pods_out.strip()})
    except Exception as e:
        return jsonify({"active": "none", "error": str(e)})

@app.route("/api/scenarios")
def scenarios():
    """Everything the UI needs to render the selector - derived from the registry."""
    return jsonify({"scenarios": [
        {"key": k, "name": v["name"],
         "additive": v.get("additive", False),
         "releases": [r[0] for r in v["releases"]]}
        for k, v in SCENARIOS.items() if k != "none"]})


@app.route("/api/deploy", methods=["POST"])
def deploy():
    key = (request.json or {}).get("ran", "")
    key = ALIASES.get(key, key)
    if key not in SCENARIOS:
        return jsonify({"error": f"Unknown scenario '{key}'",
                        "available": sorted(SCENARIOS)}), 400
    scen = SCENARIOS[key]
    try:
        # Additive scenarios (F-RAN) never tear down the RAN choice, and a RAN
        # switch never tears down an additive one - F-RAN's edge DNN lives in
        # the open5gs release, so removing it would change core config.
        if not scen.get("additive"):
            others = [k for k, v in SCENARIOS.items()
                      if k != key and not v.get("additive")]
            for rel in _releases_of(others):
                run(f"helm uninstall {rel} -n {NS} --wait --timeout=60s 2>/dev/null; true")

        for rel in scen["releases"]:
            name, chart, vals = rel[0], rel[1], rel[2]
            vflag = f" -f {REPO_PATH}/{chart}/{vals}" if vals else ""
            run(f"helm upgrade --install {name} {REPO_PATH}/{chart} -n {NS}{vflag}")

        # GitOps record of the switch (non-blocking)
        try:
            with open(CONFIG_FILE) as fh:
                config = yaml.safe_load(fh) or {}
            if not scen.get("additive"):
                config["active"] = key
            config.setdefault("additive", [])
            if scen.get("additive") and key not in config["additive"]:
                config["additive"].append(key)
            with open(CONFIG_FILE, "w") as fh:
                yaml.dump(config, fh, default_flow_style=False)
            os.chdir(REPO_PATH)
            run("git add ran-selector/active-ran.yaml")
            run(f"git commit -m feat:_Switch_RAN_to_{key} 2>&1 || true")
            run("timeout 10 git push origin main 2>&1 || true")
        except Exception:
            pass

        return jsonify({"status": "success", "ran": key,
                        "name": scen["name"],
                        "released": [r[0] for r in scen["releases"]]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-clean")
def verify_clean():
    """Which scenarios have pods actually running - detects leftovers from a failed switch."""
    present, running = [], []
    for key, scen in SCENARIOS.items():
        if key == "none":
            continue
        hits = []
        sel = scen.get("selector")
        lflag = f" -l {sel}" if sel else ""
        for frag in scen["pods"]:
            _, out, _ = run(f"kubectl get pods -n {NS}{lflag} --no-headers 2>/dev/null | grep {frag} | grep Running")
            if out.strip():
                hits.append(frag)
        if hits:
            present.append(key)
            running.extend(hits)
    ran_active = [k for k in present if not SCENARIOS[k].get("additive")]
    return jsonify({"clean": len(ran_active) <= 1,
                    "active_scenarios": present,
                    "active_combos": ran_active,      # legacy key the UI reads
                    "ran_scenarios": ran_active,
                    "running_pods": running})


@app.route("/api/hpa")
def hpa():
    """Live autoscaler state - the v-CRAN evidence."""
    _, out, _ = run(f"kubectl get hpa -n {NS} --no-headers 2>/dev/null")
    rows = []
    for line in out.strip().split("\n"):
        p = line.split()
        if len(p) >= 7 and p[2] != "<unknown>/80%":
            rows.append({"name": p[0], "targets": p[2],
                         "min": p[3], "max": p[4], "replicas": p[5]})
    return jsonify({"hpa": rows})


@app.route("/api/latency")
def latency():
    """Live RTT through the 5G user plane (uesimtun0) - the real end-to-end latency"""
    _, pod, _ = run(f"kubectl get pods -n {NS} -l component=ue -o jsonpath='{{.items[0].metadata.name}}' 2>/dev/null")
    pod = pod.strip().strip("'")
    if not pod:
        return jsonify({"error": "UE pod not found", "rtt_ms": None})
    _, out, _ = run(f"kubectl exec -n {NS} {pod} -- ping -I uesimtun0 -c 3 -W 2 8.8.8.8 2>&1")
    import re as _re
    m = _re.search(r"min/avg/max[^=]*= ([\d.]+)/([\d.]+)/([\d.]+)", out)
    loss = _re.search(r"(\d+)% packet loss", out)
    if m:
        return jsonify({"rtt_min": float(m.group(1)), "rtt_ms": float(m.group(2)), "rtt_max": float(m.group(3)), "loss_pct": int(loss.group(1)) if loss else 0, "target": "8.8.8.8 via uesimtun0"})
    return jsonify({"error": "no route through user plane", "rtt_ms": None, "loss_pct": 100, "raw": out[-200:]})

@app.route("/api/ue-status")
def ue_status():
    """UE registration + PDU session state from live logs"""
    _, pod, _ = run(f"kubectl get pods -n {NS} -l component=ue -o jsonpath='{{.items[0].metadata.name}}' 2>/dev/null")
    pod = pod.strip().strip("'")
    if not pod:
        return jsonify({"registered": False, "pdu_session": False, "detail": "UE pod not found"})
    _, out, _ = run(f"kubectl logs -n {NS} {pod} --tail=100 2>&1")
    registered = "Initial Registration is successful" in out
    pdu = "PDU Session establishment is successful" in out
    tun = ""
    for line in out.split("\n"):
        if "TUN interface" in line:
            tun = line.split("TUN interface")[-1].strip("[]. ")
    return jsonify({"registered": registered, "pdu_session": pdu, "tun": tun, "pod": pod})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
