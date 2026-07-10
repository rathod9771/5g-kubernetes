#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import subprocess, yaml, os, json

app = Flask(__name__)
REPO_PATH = os.path.expanduser("~/5g-kubernetes")
CONFIG_FILE = f"{REPO_PATH}/ran-selector/active-ran.yaml"
NS = "free5gc"

POD_MAP = {
  "oai": "oai-cu",
  "OAI": "oai-cu",
  "AMF": "free5gc-amf",
  "SMF": "free5gc-smf",
  "UPF": "free5gc-upf",
  "NRF": "free5gc-nrf",
  "AUSF": "free5gc-ausf",
  "UDM": "free5gc-udm",
  "UDR": "free5gc-udr",
  "PCF": "free5gc-pcf",
  "NSSF": "free5gc-nssf",
  "UE": "ueransim-ue",
  "GNB": "ueransim-gnb",
  "SRSRAN": "srsran-gnb",
  "OAI-CU": "oai-cu",
  "oai": "oai-cu",
  "OAI-DU": "oai-du",
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
    c_flag = f"-c {container}" if container else ""
    _, out, err = run(f"kubectl logs -n {NS} {pod} {c_flag} --tail={lines} 2>&1")
    return jsonify({"logs": strip_ansi(out or err), "pod": pod})

@app.route("/api/status")
def status():
    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f)
        _, pods_out, _ = run(f"kubectl get pods -n {NS} | grep -E 'srsran|oai'")
        return jsonify({"active": config.get("active","none"), "pods": pods_out.strip()})
    except Exception as e:
        return jsonify({"active": "none", "error": str(e)})

@app.route("/api/deploy", methods=["POST"])
def deploy():
    ran_data = request.json
    ran = ran_data.get("ran")
    ran = {"cran":"srsran","oran":"oai"}.get(ran,ran)
    if ran not in ["srsran","oai","none"]:
        return jsonify({"error": "Invalid RAN"}), 400
    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f)
        config["active"] = ran
        config["srsran"]["enabled"] = (ran == "srsran")
        config["oai"]["enabled"] = (ran == "oai")
        config["oai"]["components"]["cu"] = (ran == "oai")
        config["oai"]["components"]["du"] = (ran == "oai")
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        os.chdir(REPO_PATH)
        run("git add ran-selector/active-ran.yaml")
        run(f"git commit -m feat:_Switch_RAN_to_{ran}")
        run("git push origin main")
        if ran == "srsran":
            run("helm uninstall oai-cu oai-du -n free5gc 2>/dev/null || true")
            run(f"helm upgrade --install srsran {REPO_PATH}/helm/srsran -n {NS}")
        elif ran == "oai":
            run("helm uninstall srsran -n free5gc 2>/dev/null || true")
            run(f"helm upgrade --install oai-cu {REPO_PATH}/helm/oai/cu -n {NS}")
            run(f"helm upgrade --install oai-du {REPO_PATH}/helm/oai/du -n {NS}")
        return jsonify({"status":"success","ran":ran})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
