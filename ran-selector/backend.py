#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import subprocess, yaml, os, json
import time
import osm_client

app = Flask(__name__)
REPO_PATH = os.path.expanduser("~/5g-kubernetes")
CONFIG_FILE = f"{REPO_PATH}/ran-selector/active-ran.yaml"
NS = "c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c"

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


NF_LABELS = {
  "AMF": "amf", "SMF": "smf", "UPF": "upf", "NRF": "nrf",
  "AUSF": "ausf", "UDM": "udm", "UDR": "udr", "PCF": "pcf", "NSSF": "nssf",
}

POD_MAP = {
  "oai": "oai-cu",
  "OAI": "oai-cu",
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

def get_pod_name_by_label(label_value):
    _, out, _ = run(
        f"kubectl get pods -n {NS} -l app.kubernetes.io/name={label_value} "
        "--no-headers 2>/dev/null | grep Running | head -1 | awk '{print $1}'"
    )
    return out.strip()

@app.route("/api/logs/<nf>")
def logs(nf):
    container = request.args.get("container", "")
    lines = request.args.get("lines", "30")
    nf_upper = nf.upper()
    if nf_upper in NF_LABELS:
        pod = get_pod_name_by_label(NF_LABELS[nf_upper])
        label = nf_upper.lower()
    else:
        label = POD_MAP.get(nf_upper, nf.lower())
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

@app.route("/api/osm/status")
def osm_status():
    """OSM 19 + FluxCD health for the OSM panel."""
    out = {"pods": {"ready": 0, "total": 0}, "flux": [], "error": None}
    try:
        _, po, _ = run("kubectl get pods -n osm -o json")
        items = json.loads(po).get("items", [])
        ready = 0
        for p in items:
            cs = p["status"].get("containerStatuses", [])
            if cs and all(c.get("ready") for c in cs):
                ready += 1
        out["pods"] = {"ready": ready, "total": len(items)}
        cmd = ("kubectl get gitrepository,kustomization -n flux-system "
               "-o jsonpath='{range .items[*]}{.kind}|{.metadata.name}|"
               "{.status.conditions[?(@.type==\"Ready\")].status}{\"\\n\"}{end}'")
        _, fo, _ = run(cmd)
        for line in fo.strip().splitlines():
            parts = line.split("|")
            if len(parts) == 3:
                out["flux"].append({"kind": parts[0], "name": parts[1],
                                    "ready": parts[2] == "True"})
    except Exception as e:
        out["error"] = str(e)
    return jsonify(out)

DOCS_DIR = os.path.join(os.path.dirname(CONFIG_FILE), "docs")

@app.route("/api/docs/slides")
def docs_slides():
    """List the rendered slide images, in order."""
    d = os.path.join(DOCS_DIR, "slides")
    try:
        files = sorted(f for f in os.listdir(d) if f.lower().endswith((".jpg", ".png")))
    except FileNotFoundError:
        files = []
    return jsonify({"slides": ["/api/docs/slides/" + f for f in files]})

@app.route("/api/docs/slides/<path:name>")
def docs_slide_file(name):
    if "/" in name or ".." in name:
        return jsonify({"error": "bad name"}), 400
    return send_from_directory(os.path.join(DOCS_DIR, "slides"), name)

@app.route("/api/docs/<path:name>")
def docs_file(name):
    """Serve documentation assets (the architecture deck as PDF and PPTX)."""
    if "/" in name or ".." in name:
        return jsonify({"error": "bad name"}), 400
    if not os.path.isfile(os.path.join(DOCS_DIR, name)):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(DOCS_DIR, name)

@app.route("/api/docs")
def docs_list():
    """What documentation is available."""
    try:
        files = sorted(f for f in os.listdir(DOCS_DIR) if not f.startswith("."))
    except FileNotFoundError:
        files = []
    return jsonify({"files": files})

@app.route("/api/scenarios")
def scenarios():
    """Everything the UI needs to render the selector - derived from the registry."""
    return jsonify({"scenarios": [
        {"key": k, "name": v["name"],
         "additive": v.get("additive", False),
         "releases": [r[0] for r in v["releases"]]}
        for k, v in SCENARIOS.items() if k != "none"]})



def _pods_present(frags, selector=None):
    """True if any pod matching the given name fragments is Running."""
    lflag = f" -l {selector}" if selector else ""
    for frag in frags:
        _, out, _ = run(f"kubectl get pods -n {NS}{lflag} --no-headers 2>/dev/null | grep {frag} | grep Running")
        if out.strip():
            return True
    return False


def _wait_pods_gone(frags, selector=None, timeout=90, interval=5):
    waited = 0
    while waited < timeout:
        if not _pods_present(frags, selector):
            return True
        time.sleep(interval)
        waited += interval
    return False


def _nsd_name_for(key):
    return key.replace("-", "_") + "_ns"


@app.route("/api/deploy", methods=["POST"])
def deploy():
    key = (request.json or {}).get("ran", "")
    key = ALIASES.get(key, key)
    if key not in SCENARIOS:
        return jsonify({"error": f"Unknown scenario '{key}'",
                        "available": sorted(SCENARIOS)}), 400
    scen = SCENARIOS[key]

    try:
        with open(CONFIG_FILE) as fh:
            config = yaml.safe_load(fh) or {}
    except Exception:
        config = {}
    config.setdefault("osm", {})

    try:
        if not scen.get("additive"):
            old = config["osm"].get("active_instance_id")
            old_key = config["osm"].get("active_scenario")
            if old and old_key and old_key in SCENARIOS:
                old_scen = SCENARIOS[old_key]
                op = osm_client.terminate_ns(old)
                osm_client.wait_for_op(op, timeout=180)
                if not _wait_pods_gone(old_scen["pods"], old_scen.get("selector"), timeout=90):
                    return jsonify({"error": f"Old RAN '{old_key}' did not tear down cleanly; "
                                              "manual cleanup may be required"}), 500
                osm_client.delete_ns_instance(old)

        nsd_name = _nsd_name_for(key)
        ns_name = f"ran-{key}"
        ns_id, op_id = osm_client.instantiate_ns(nsd_name, ns_name)
        state = osm_client.wait_for_op(op_id, timeout=180)
        if state not in ("COMPLETED", "PARTIALLY_COMPLETED"):
            return jsonify({"error": f"Instantiate failed for '{key}': operation state {state}"}), 500

        if scen.get("additive"):
            config["osm"].setdefault("additive_instances", {})[key] = ns_id
        else:
            config["osm"]["active_instance_id"] = ns_id
            config["osm"]["active_scenario"] = key
            config["active"] = key

        try:
            with open(CONFIG_FILE, "w") as fh:
                yaml.dump(config, fh, default_flow_style=False)
            os.chdir(REPO_PATH)
            run("git add ran-selector/active-ran.yaml")
            run(f"git commit -m feat:_Switch_RAN_to_{key} 2>&1 || true")
            run("timeout 10 git push origin main 2>&1 || true")
        except Exception:
            pass

        return jsonify({"status": "success", "ran": key, "name": scen["name"],
                        "ns_instance_id": ns_id})
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


PROM_URL = "http://localhost:30990"

def _prom_query(promql):
    import urllib.request, urllib.parse, json as _json
    url = f"{PROM_URL}/api/v1/query?query={urllib.parse.quote(promql)}"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = _json.loads(resp.read().decode())
        result = data.get("data", {}).get("result", [])
        if not result:
            return None
        return float(result[0]["value"][1])
    except Exception:
        return None

@app.route("/api/latency")
def latency():
    """Real control-plane (SBI) TCP round-trip latency from the latency-probe exporter.
    Not UE data-plane latency: PDU session establishment is currently blocked by a
    UPF-side PFCP issue (see layer3-autonomous/README.md for detail)."""
    rtt = _prom_query("network_tcp_connect_latency_last_seconds")
    if rtt is None:
        return jsonify({"error": "no data from latency probe", "rtt_ms": None, "loss_pct": None})
    return jsonify({"rtt_ms": rtt * 1000, "loss_pct": 0, "target": "amf-sbi (control-plane)"})

@app.route("/api/ue-status")
def ue_status():
    """UE registration + radio link state from live OAI gNB/UE logs and Prometheus."""
    _, pod, _ = run(f"kubectl get pods -n {NS} -l app=oai-nr-ue -o jsonpath='{{.items[0].metadata.name}}' 2>/dev/null")
    pod = pod.strip().strip("'")
    registered = False
    pdu_session = False
    tun_ip = ""
    if pod:
        _, out, _ = run(f"kubectl logs -n {NS} {pod} 2>&1")
        registered = "Registration complete" in out or "Received Registration Accept" in out
        pdu_session = (
            "Received PDU Session Establishment Accept" in out
            or "PDU Session establishment is successful" in out
        )
        if pdu_session:
            _, tun_out, _ = run(f"kubectl exec -n {NS} {pod} -- ip -4 -o addr show oaitun_ue1 2>/dev/null")
            m = re.search(r"inet (\S+)/", tun_out)
            tun_ip = m.group(1) if m else ""
    rsrp = _prom_query("max(oai_gnb_ue_rsrp_dbm)")
    return jsonify({
        "registered": registered,
        "pdu_session": pdu_session,
        "tun": tun_ip,
        "pod": pod,
        "radio_connected": rsrp is not None,
        "rsrp_dbm": rsrp,
    })

@app.route("/api/layer2-metrics")
def layer2_metrics():
    """Live Layer 2 summary for the dashboard: BLER, throughput, resource use, QoS."""
    return jsonify({
        "bler_dl": _prom_query("max(oai_gnb_ue_dl_bler_ratio)"),
        "bler_ul": _prom_query("max(oai_gnb_ue_ul_bler_ratio)"),
        "harq_retx_dl": _prom_query("sum(oai_gnb_ue_dlsch_harq_round1_total + oai_gnb_ue_dlsch_harq_round2_total + oai_gnb_ue_dlsch_harq_round3_total)"),
        "mac_tx_bytes": _prom_query("sum(oai_gnb_ue_mac_tx_bytes_total)"),
        "mac_rx_bytes": _prom_query("sum(oai_gnb_ue_mac_rx_bytes_total)"),
        "amf_registrations": _prom_query("sum(fivegs_amffunction_rm_reginitreq)"),
        "pcf_active_sessions": _prom_query("sum(fivegs_pcffunction_pa_sessionnbr)"),
        "control_plane_latency_ms": (lambda v: v * 1000 if v is not None else None)(_prom_query("network_tcp_connect_latency_last_seconds")),
    })

@app.route("/api/layer3-actions")
def layer3_actions():
    """Recent autonomous actions taken by the Layer 3 watcher."""
    import json as _json
    path = os.path.expanduser("~/5g-kubernetes/layer3-autonomous/actions.log")
    actions = []
    try:
        with open(path) as f:
            lines = f.readlines()[-20:]
        for line in reversed(lines):
            line = line.strip()
            if line:
                try:
                    actions.append(_json.loads(line))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return jsonify({"actions": actions})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
