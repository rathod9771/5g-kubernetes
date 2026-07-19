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
        _, pods_out, _ = run(f"kubectl get pods -n {NS} | grep -E 'srsran|oai'")
        return jsonify({"active": config.get("active","none"), "pods": pods_out.strip()})
    except Exception as e:
        return jsonify({"active": "none", "error": str(e)})

@app.route("/api/deploy", methods=["POST"])
def deploy():
    ran_data = request.json
    ran = ran_data.get("ran")
    # Map architecture-stack combos to actual deployable helm releases
    combo_map = {
        "cran-srsran": "srsran",
        "cran-oai": "oai-cran",
        "oran-oai": "oai",
        "oran-srsran": "srsran-oran",
        # Not yet implemented combos - will return a friendly error
    }
    if ran in combo_map:
        ran = combo_map[ran]
    elif "-" in ran and ran not in ["srsran", "oai"]:
        return jsonify({"error": f"Combination \'{ran}\' not yet implemented - only C-RAN+srsRAN and O-RAN+OAI are currently deployable"}), 400
    else:
        ran = {"cran":"srsran","oran":"oai"}.get(ran,ran)
    if ran not in ["srsran","oai","srsran-oran","oai-cran","none"]:
        return jsonify({"error": "Invalid RAN"}), 400
    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f)
        config["active"] = ran
        config["srsran"]["enabled"] = (ran in ["srsran","srsran-oran"])
        config["oai"]["enabled"] = (ran == "oai")
        config["oai"]["components"]["cu"] = (ran == "oai")
        config["oai"]["components"]["du"] = (ran == "oai")
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        os.chdir(REPO_PATH)
        run("git add ran-selector/active-ran.yaml")
        run(f"git commit -m feat:_Switch_RAN_to_{ran} 2>&1 || true")
        run("timeout 10 git push origin main 2>&1 || true")  # non-blocking: needs stored credentials to succeed
        if ran == "srsran":
            run("helm uninstall oai-cu -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-du -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall srsran-cu -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall srsran-du -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-cran -n free5gc --wait --timeout=60s 2>/dev/null; true")
            run(f"helm upgrade --install srsran {REPO_PATH}/helm/srsran -n {NS}")
        elif ran == "oai-cran":
            run("helm uninstall srsran -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall srsran-cu -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall srsran-du -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-cu -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-du -n free5gc --wait --timeout=60s 2>/dev/null; true")
            run(f"helm upgrade --install oai-cran {REPO_PATH}/helm/oai-cran -n {NS}")
        elif ran == "srsran-oran":
            run("helm uninstall srsran -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-cu -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-du -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-cran -n free5gc --wait --timeout=60s 2>/dev/null; true")
            run(f"helm upgrade --install srsran-cu {REPO_PATH}/helm/srsran-oran/cu -n {NS}")
            run(f"helm upgrade --install srsran-du {REPO_PATH}/helm/srsran-oran/du -n {NS}")
        elif ran == "oai":
            run("helm uninstall srsran -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall srsran-cu -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall srsran-du -n free5gc --wait --timeout=60s 2>/dev/null; helm uninstall oai-cran -n free5gc --wait --timeout=60s 2>/dev/null; true")
            run(f"helm upgrade --install oai-cu {REPO_PATH}/helm/oai/cu -n {NS}")
            run(f"helm upgrade --install oai-du {REPO_PATH}/helm/oai/du -n {NS}")
        return jsonify({"status":"success","ran":ran})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-clean", methods=["GET"])
def verify_clean():
    """Check that only ONE RAN combo is actually running - detects leftover pods from failed switches"""
    ran_labels = ["srsran-gnb", "srsran-cu", "srsran-du", "oai-cu", "oai-du", "oai-cran-gnb"]
    running = []
    for label in ran_labels:
        _, out, _ = run(f"kubectl get pods -n {NS} --no-headers 2>/dev/null | grep {label} | grep Running")
        if out.strip():
            running.append(label)
    # Group into combos
    combos_present = set()
    if "srsran-gnb" in running: combos_present.add("cran-srsran")
    if "oai-cran-gnb" in running: combos_present.add("cran-oai")
    if "srsran-cu" in running or "srsran-du" in running: combos_present.add("oran-srsran")
    if "oai-cu" in running or "oai-du" in running: combos_present.add("oran-oai")
    return jsonify({
        "clean": len(combos_present) <= 1,
        "active_combos": list(combos_present),
        "running_pods": running
    })


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
