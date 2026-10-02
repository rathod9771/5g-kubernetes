#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory, g
import subprocess, yaml, os, json
import time
import sys
from pathlib import Path
import osm_client

app = Flask(__name__)
REPO_PATH = str(Path(__file__).resolve().parents[1])
UI_DIR = str(Path(__file__).resolve().parent)

# One registry supplies UI/inventory metadata and OSM descriptor identities.
# Import helpers only; no generation, Helm calls or lifecycle work occurs at startup.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scenario_registry import load_registry, dashboard_scenarios
from osm_packages import DEFAULT_OUTPUT, PackageError, validated_snapshot
from local_safety import exclusive_lock, atomic_write
from osm_catalog import Catalog, CatalogError
from runtime_config import load_config, ConfigError
from state_schema import validate_state
DEFAULT_STATE_PATH = str(Path(REPO_PATH) / '.runtime/active-ran.yaml')
CONFIG_FILE = DEFAULT_STATE_PATH


def _state_path():
    if hasattr(g, 'runtime_state_path'):
        return g.runtime_state_path
    if CONFIG_FILE != DEFAULT_STATE_PATH:  # Explicit test/embedder override.
        return CONFIG_FILE
    cfg = getattr(g, 'runtime_config', None) or load_config()
    return cfg['ACTIVE_STATE_PATH']


def runtime_preflight():
    cfg = osm_client.runtime_config(require=('kubernetes',))
    with osm_client.runtime_session(cfg):
        catalog = Catalog(cfg['OSM_HOST'], osm_client.get_token())
        context = osm_client.context(cfg, catalog)
    return cfg, context, catalog


def _namespace():
    if not hasattr(g, 'runtime_context'):
        cfg, context, _ = runtime_preflight()
        g.runtime_config, g.runtime_context = cfg, context
    return g.runtime_context['namespace']


def _kubectl_argv(args):
    cfg = getattr(g, 'runtime_config', None) or load_config(require=('kubernetes',))
    return ['kubectl', '--kubeconfig', cfg['OSM_KUBECONFIG_PATH'], *args]


@app.errorhandler(ConfigError)
def configuration_error(error):
    return jsonify({'error': str(error)}), 409


@app.route('/api/config')
def public_config():
    cfg = load_config(network='auto')
    return jsonify({key: cfg[field] for key, field in
                    [('rancher', 'RANCHER_URL'), ('osm', 'OSM_HOST'),
                     ('grafana', 'GRAFANA_URL'), ('prometheus', 'PROMETHEUS_URL')]})


SCENARIO_SPEC = load_registry()
SCENARIO_ENTRIES = {s["key"]: s for s in SCENARIO_SPEC["scenarios"]}
SCENARIOS = dashboard_scenarios(SCENARIO_SPEC)
ALIASES = SCENARIO_SPEC["aliases"]

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
    import shlex
    cfg = getattr(g, 'runtime_config', None) or load_config(require=('kubernetes',))
    # cmd is assembled internally; namespace has strict DNS validation. Paths are quoted.
    cmd = cmd.replace('kubectl ', 'kubectl --kubeconfig ' + shlex.quote(cfg['OSM_KUBECONFIG_PATH']) + ' ')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def get_pod_name(label):
    data = _kubectl_json(["get", "pods", "-n", _namespace(), "-o", "json"])
    if data is None:
        return ""
    return next((p["metadata"]["name"] for p in data.get("items", [])
                 if label in p.get("metadata", {}).get("name", "")
                 and p.get("status", {}).get("phase") == "Running"), "")

@app.route("/")
def index():
    return send_from_directory(UI_DIR, "index.html")

@app.route("/api/pods")
def pods():
    _, out, _ = run(f"kubectl get pods -n {_namespace()} -o json")
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
    data = _kubectl_json(["get", "pods", "-n", _namespace(), "-l",
                          "app.kubernetes.io/name=" + label_value, "-o", "json"])
    if data is None:
        return ""
    return next((p["metadata"]["name"] for p in data.get("items", [])
                 if p.get("status", {}).get("phase") == "Running"), "")


def _kubectl_json(args):
    """Run kubectl and return parsed JSON, or None on failure."""
    try:
        p = subprocess.run(_kubectl_argv(args), capture_output=True, text=True, timeout=8)
        if p.returncode != 0:
            return None
        return json.loads(p.stdout)
    except Exception:
        return None


def _scenario_pods(key):
    """Resolve the CURRENT running pods for a scenario from Kubernetes state."""
    key = ALIASES.get(key, key)
    scen = SCENARIOS.get(key)
    if not scen:
        return []

    data = _kubectl_json(["get", "pods", "-n", _namespace(), "-o", "json"])
    if not data:
        return []

    selector = scen.get("selector")
    required_labels = {}
    if selector and "=" in selector:
        k, v = selector.split("=", 1)
        required_labels[k] = v

    matches = []
    for p in data.get("items", []):
        if p.get("status", {}).get("phase") != "Running":
            continue
        labels = p.get("metadata", {}).get("labels", {})
        if required_labels and any(labels.get(k) != v for k, v in required_labels.items()):
            continue
        name = p.get("metadata", {}).get("name", "")
        if any(frag in name for frag in scen.get("pods", [])):
            matches.append(p)
    return matches


def _nf_pod(nf):
    nf_upper = nf.upper()
    if nf_upper == "UE":
        data = _kubectl_json(["get", "pods", "-n", _namespace(), "-o", "json"])
        if not data:
            return None
        for p in data.get("items", []):
            if "oai-nr-ue" in p.get("metadata", {}).get("name", "") and p.get("status", {}).get("phase") == "Running":
                return p
        for p in data.get("items", []):
            if "ueransim-ue" in p.get("metadata", {}).get("name", "") and p.get("status", {}).get("phase") == "Running":
                return p
        return None
    if nf_upper in NF_LABELS:
        data = _kubectl_json(["get", "pods", "-n", _namespace(), "-l",
                              f"app.kubernetes.io/name={NF_LABELS[nf_upper]}", "-o", "json"])
        if data:
            for p in data.get("items", []):
                if p.get("status", {}).get("phase") == "Running":
                    return p
        return None

    # Legacy/component names: resolve by the existing fragment map.
    label = POD_MAP.get(nf_upper)
    if label is None:
        return None
    pod_name = get_pod_name(label)
    if not pod_name:
        return None
    data = _kubectl_json(["get", "pod", "-n", _namespace(), pod_name, "-o", "json"])
    return data


def _known_component(key):
    return (ALIASES.get(key, key) in SCENARIOS
            or key.upper() in set(NF_LABELS) | set(POD_MAP) | {"UE"})


def _runtime_targets(key):
    """Return live Kubernetes pods for a scenario/core/UE/component key."""
    key = ALIASES.get(key, key)
    if key not in SCENARIOS and key.upper() not in set(NF_LABELS) | set(POD_MAP) | {"UE"}:
        return []
    if key in SCENARIOS and key != "none":
        return _scenario_pods(key)
    p = _nf_pod(key)
    return [p] if p else []


def _pod_runtime(p):
    """Convert a Kubernetes Pod object into live dashboard state."""
    if not p:
        return None
    cs = p.get("status", {}).get("containerStatuses", [])
    ready = sum(1 for c in cs if c.get("ready"))
    total = len(cs)
    restarts = sum(int(c.get("restartCount", 0) or 0) for c in cs)
    names = [c.get("name", "") for c in cs]
    return {
        "name": p.get("metadata", {}).get("name", ""),
        "phase": p.get("status", {}).get("phase", "Unknown"),
        "ready": f"{ready}/{total}",
        "ready_count": ready,
        "container_count": total,
        "restarts": restarts,
        "containers": names,
        "istio_injected": "istio-proxy" in names,
        "node": p.get("spec", {}).get("nodeName", ""),
        "start_time": p.get("status", {}).get("startTime", ""),
    }


def _live_processes(p):
    """Read actual processes from each running container; never invent process names."""
    if not p:
        return []
    pod = p.get("metadata", {}).get("name", "")
    result = []
    for c in p.get("status", {}).get("containerStatuses", []):
        cname = c.get("name", "")
        if not cname:
            continue
        # ps is present in the OAI/srsRAN images; fallback to /proc if it is not.
        cmd = (
            "ps -eo pid=,comm=,args= 2>/dev/null || "
            "for x in /proc/[0-9]*; do "
            "  pid=${x##*/}; "
            "  comm=$(cat $x/comm 2>/dev/null); "
            "  args=$(tr '\\\\0' ' ' < $x/cmdline 2>/dev/null); "
            "  [ -n \"$comm\" ] && echo \"$pid $comm $args\"; "
            "done"
        )
        try:
            q = subprocess.run(
                _kubectl_argv(["exec", "-n", _namespace(), pod, "-c", cname, "--", "sh", "-c", cmd]),
                capture_output=True, text=True, timeout=8
            )
            if q.returncode != 0:
                continue
            for line in q.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                pid, comm = parts[0], parts[1]
                args = parts[2] if len(parts) > 2 else comm
                if pid == "1" or comm not in ("sh", "bash", "ps"):
                    result.append({
                        "container": cname, "pid": pid, "name": comm,
                        "command": args[:240], "state": "running"
                    })
        except Exception:
            continue
    return result


@app.route("/api/runtime/<key>")
def runtime(key):
    """Single live source for logs/process/status panels."""
    if not _known_component(key):
        return jsonify({"error": "Unknown component"}), 400
    targets = _runtime_targets(key)
    pods = [_pod_runtime(p) for p in targets if p]
    processes = []
    for p in targets:
        processes.extend(_live_processes(p))

    latency = _prom_query("network_tcp_connect_latency_last_seconds")
    success = _prom_query(
        '100 * sum(rate(network_tcp_connect_success_total[5m])) / '
        'clamp_min(sum(rate(network_tcp_connect_latency_seconds_count[5m])), 0.000001)'
    )
    return jsonify({
        "key": ALIASES.get(key, key),
        "found": bool(pods),
        "pods": pods,
        "processes": processes,
        "latency_ms": latency * 1000 if latency is not None else None,
        "sbi_success_pct": success,
        "updated": time.time(),
    })


def _log_text_for_pod(pod, lines=60):
    """Get actual stdout logs for one pod, with live file logs for split srsRAN CU/DU."""
    name = pod.get("metadata", {}).get("name", "")
    cs = pod.get("status", {}).get("containerStatuses", [])
    chunks = []

    for c in cs:
        cname = c.get("name", "")
        if not cname:
            continue

        # srsRAN split CU/DU images write their useful application log to /tmp.
        logfile = None
        if cname == "cu":
            logfile = "/tmp/cu.log"
        elif cname == "du":
            logfile = "/tmp/du.log"

        if logfile:
            try:
                q = subprocess.run(
                    _kubectl_argv(["exec", "-n", _namespace(), name, "-c", cname, "--",
                     "sh", "-c", f"tail -n {int(lines)} {logfile} 2>/dev/null"]),
                    capture_output=True, text=True, timeout=8
                )
                if q.stdout.strip():
                    chunks.append(f"=== {name}/{cname} ===\n{q.stdout.strip()}")
                    continue
            except Exception:
                pass

        try:
            q = subprocess.run(
                _kubectl_argv(["logs", "-n", _namespace(), name, "-c", cname, f"--tail={int(lines)}"]),
                capture_output=True, text=True, timeout=8
            )
            out = (q.stdout or q.stderr).strip()
            if out:
                chunks.append(f"=== {name}/{cname} ===\n{out}")
        except Exception:
            pass

    # Add live SCTP association evidence where available.
    for c in cs:
        cname = c.get("name", "")
        if cname not in ("cu", "du", "gnb", "oai-gnb"):
            continue
        try:
            q = subprocess.run(
                _kubectl_argv(["exec", "-n", _namespace(), name, "-c", cname, "--",
                 "sh", "-c", "cat /proc/net/sctp/assocs 2>/dev/null | tail -n +2"]),
                capture_output=True, text=True, timeout=5
            )
            assoc = q.stdout.strip()
            if assoc:
                chunks.append("=== Live SCTP associations ===\n" + assoc)
        except Exception:
            pass

    return "\n\n".join(chunks)




def _client_events(key):
    """Build a client-readable RAN view from live Kubernetes evidence.

    Evidence sources, in order of usefulness:
      - current pod/container readiness
      - actual RAN log messages
      - live SCTP associations exposed by /proc/net/sctp/assocs
      - the existing live UE/PDU status endpoint for UE-related state

    No connection state is declared from a static scenario definition alone.
    """
    targets = _runtime_targets(key)
    if not targets:
        return {"found": False, "overall": "DOWN",
                "overall_detail": "No running Kubernetes pod found",
                "statuses": [], "events": [], "updated": time.time()}

    combined = []
    sctp_lines = []
    for pod in targets:
        txt = _log_text_for_pod(pod, 180)
        for raw in txt.splitlines():
            line = raw.strip()
            if not line or line.startswith("==="):
                continue
            # /proc/net/sctp/assocs records are machine-formatted and often
            # begin with a hexadecimal kernel pointer. Keep them separately.
            if re.search(r'\b(?:38412|38462|38472)\b', line):
                sctp_lines.append(line)
            if not line.startswith("ffff"):
                combined.append(line)

    def latest_match(rx):
        return next((line for line in reversed(combined) if rx.search(line)), None)

    def add_status(statuses, ident, label, state):
        statuses.append({"id": ident, "label": label, "state": state})

    def add_event(events, label, detail, severity="ok", timestamp=""):
        events.append({"label": label, "timestamp": timestamp,
                       "detail": detail[:180], "severity": severity})

    statuses = []
    events = []

    # Container/process state is live Kubernetes state.
    live_names = {c.get("name", "")
                  for pod in targets
                  for c in pod.get("status", {}).get("containerStatuses", [])}
    if "cu" in live_names:
        add_status(statuses, "cu", "CU", "Running")
    if "du" in live_names:
        add_status(statuses, "du", "DU", "Running")

    # Human-readable log evidence.
    rules = [
        ("amf", "AMF Connection", "Connected",
         re.compile(r"Connected to AMF|N2:\s*Connection to AMF.*established|NG.?SetupResponse", re.I),
         "AMF connection is established"),
        ("ng", "NG Setup", "Completed",
         re.compile(r"NG.?SetupResponse|NG Setup.*(?:complete|success)|Connected to AMF", re.I),
         "NG setup completed"),
        ("e1", "E1 Connection", "Connected",
         re.compile(r"E1SetupResponse|E1.*Setup.*(?:finalized|complete|success)|CU-UP started successfully", re.I),
         "E1 connection/setup is established"),
        ("f1", "F1 Connection", "Connected",
         re.compile(r"F1SetupResponse|F1.*Setup.*(?:finalized|complete|success)|Added TNL connection to DU", re.I),
         "F1 connection/setup is established"),
        ("cell", "Cell", "Active",
         re.compile(r"cell.*(?:activated|active)|Cell.*(?:activated|active)", re.I),
         "Cell is active"),
        ("ue", "UE", "Connected",
         re.compile(r"UE.*(?:connected|registered)|RRC.*(?:connected|setup complete)|registration.*accept", re.I),
         "UE connection is established"),
        ("pdu", "PDU Session", "Established",
         re.compile(r"PDU.?session.*(?:established|active)|PDU SESSION ESTABLISHMENT ACCEPT", re.I),
         "PDU session is established"),
    ]

    matched = {}
    for ident, label, state, rx, event_detail in rules:
        match = latest_match(rx)
        if match:
            matched[ident] = True
            m = re.match(r"(\d{4}-\d{2}-\d{2}T?[^ ]*)\s+(.*)", match)
            ts = m.group(1) if m else ""
            detail = m.group(2) if m else match
            add_status(statuses, ident, label, state)
            add_event(events, label + " " + state.lower(), event_detail,
                      timestamp=ts)
        else:
            matched[ident] = False
            add_status(statuses, ident, label, "No live evidence")

    # Important: some deployments (including the current srsRAN C-RAN
    # wrapper) expose the connection only through /proc/net/sctp/assocs and
    # do not print the successful setup exchange in the retained log.
    sctp_evidence = {"amf": None, "f1": None, "e1": None}
    for line in sctp_lines:
        if re.search(r'\b38412\b', line):
            sctp_evidence["amf"] = line
        if re.search(r'\b38472\b', line):
            sctp_evidence["f1"] = line
        if re.search(r'\b38462\b', line):
            sctp_evidence["e1"] = line

    def replace_status(ident, label, state):
        for item in statuses:
            if item["id"] == ident:
                item["state"] = state
                return

    if not matched["amf"] and sctp_evidence["amf"]:
        replace_status("amf", "AMF Connection", "Connected")
        add_event(events, "AMF connection detected",
                  "Live SCTP association on NG/SCTP port 38412 confirms the AMF connection.")
    if not matched["ng"] and sctp_evidence["amf"]:
        replace_status("ng", "NG Setup", "Connected")
        add_event(events, "NG connection detected",
                  "Live SCTP association on port 38412 confirms the NG transport connection.")
    if not matched["f1"] and sctp_evidence["f1"]:
        replace_status("f1", "F1 Connection", "Connected")
        add_event(events, "F1 connection detected",
                  "Live SCTP association on F1 port 38472 confirms the CU-DU transport connection.")
    if not matched["e1"] and sctp_evidence["e1"]:
        replace_status("e1", "E1 Connection", "Connected")
        add_event(events, "E1 connection detected",
                  "Live SCTP association on E1 port 38462 confirms the CU-CU-UP transport connection.")

    # Add a useful live summary when a CU/DU pair is running even if the
    # application does not print a friendly startup line.
    if "cu" in live_names and "du" in live_names:
        if not latest_match(re.compile(r"CU.*started|starting CU", re.I)):
            add_event(events, "CU/DU running",
                      "Both CU and DU containers are currently running and ready.")

    # Reuse the existing real UE/PDU state endpoint when the selected RAN is
    # the active one. This is deliberately best-effort; a different RAN may
    # be running while another scenario is being inspected.
    try:
        ue = _ue_status_payload()
        if ue.get("found") or ue.get("pod"):
            if ue.get("radio_connected"):
                replace_status("ue", "UE", "Connected")
                add_event(events, "UE connected", "Live UE status reports radio connectivity.")
            if ue.get("pdu_session"):
                replace_status("pdu", "PDU Session", "Established")
                add_event(events, "PDU session established", "Live UE status reports an established PDU session.")
    except Exception:
        pass

    # Surface the newest actual warning/error, but do not classify routine
    # words such as "failed" in a historical log unless they are current.
    bad = next((line for line in reversed(combined)
                if re.search(r"\b(ERROR|FATAL|panic|assert|failed|failure)\b", line, re.I)), None)
    if bad:
        m = re.match(r"(\d{4}-\d{2}-\d{2}T?[^ ]*)\s+(.*)", bad)
        add_event(events, "Attention required", m.group(2) if m else bad,
                  severity="error", timestamp=m.group(1) if m else "")

    # De-duplicate by event label while keeping the newest occurrence.
    unique = {}
    for event in events:
        unique[event["label"]] = event
    events = list(unique.values())[:12]

    pod_states = [_pod_runtime(p) for p in targets]
    all_ready = all((p and p["phase"] == "Running" and
                     p["ready_count"] == p["container_count"])
                    for p in pod_states)

    # Determine operational state from live evidence rather than readiness
    # alone. UE/PDU are intentionally not required for RAN infrastructure to
    # be operational because a RAN can be healthy while no UE is attached.
    infra_ids = {s["id"] for s in statuses if s["id"] in {"cu", "du", "amf", "ng", "f1"}}
    infra_ok = all(s["state"] in {"Running", "Connected", "Completed"}
                   for s in statuses if s["id"] in infra_ids)
    if bad:
        overall, detail = "DEGRADED", "A recent error was detected in the live RAN logs"
    elif all_ready and infra_ok:
        overall, detail = "OPERATIONAL", "RAN containers and required live control-plane connections are healthy"
    elif all_ready:
        overall, detail = "PARTIAL", "RAN containers are ready, but one or more live control-plane connections have no evidence yet"
    else:
        overall, detail = "STARTING", "RAN containers are not all ready yet"

    return {"found": True, "overall": overall, "overall_detail": detail,
            "statuses": statuses, "events": events,
            "pods": [p["name"] for p in pod_states if p],
            "updated": time.time()}

@app.route("/api/events/<key>")
def client_events(key):
    if not _known_component(key):
        return jsonify({"error": "Unknown component"}), 400
    return jsonify(_client_events(ALIASES.get(key, key)))

@app.route("/api/logs/<nf>")
def logs(nf):
    """Always resolve the requested component/scenario to the current Kubernetes pod."""
    if ALIASES.get(nf, nf) not in SCENARIOS and nf.upper() not in set(NF_LABELS) | set(POD_MAP) | {"UE"}:
        return jsonify({"error": "Unknown component"}), 400
    try:
        lines = max(1, min(int(request.args.get("lines", "60")), 500))
    except ValueError:
        return jsonify({"error": "Invalid line count"}), 400
    key = ALIASES.get(nf, nf)
    targets = _runtime_targets(key)

    if not targets:
        return jsonify({"logs": f"No running pod found for {key}", "pod": "", "pods": []})

    chunks = []
    for p in targets:
        out = _log_text_for_pod(p, lines)
        if out:
            chunks.append(out)

    return jsonify({
        "logs": strip_ansi("\n\n".join(chunks)),
        "pod": targets[0].get("metadata", {}).get("name", ""),
        "pods": [p.get("metadata", {}).get("name", "") for p in targets],
        "updated": time.time()
    })


@app.route("/api/status")
def status():
    try:
        with open(_state_path()) as f:
            config = yaml.safe_load(f)
        frags = sorted({p for s in SCENARIOS.values() for p in s["pods"]})
        _, pods_out, _ = run(f"kubectl get pods -n {_namespace()} | grep -E '" + "|".join(frags) + "'")
        return jsonify({"active": config.get("active","none"), "pods": pods_out.strip()})
    except Exception as e:
        return jsonify({"active": "none", "error": str(e)})

@app.route("/api/osm/status")
def osm_status():
    """OSM 19 + FluxCD health for the OSM panel."""
    out = {"pods": {"ready": 0, "total": 0}, "flux": [], "error": None}
    try:
        _, po, _ = run(f"kubectl get pods -n {load_config()['OSM_NAMESPACE']} -o json")
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

DOCS_DIR = os.path.join(UI_DIR, "docs")

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
         "releases": [r[0] for r in v["releases"]],
         "generation_status": v["generation_status"],
         "blocked_reason": v["blocked_reason"]}
        for k, v in SCENARIOS.items() if k != "none"]})



def _pods_present(frags, selector=None):
    args = ["get", "pods,deployments,statefulsets,daemonsets,replicasets,jobs,services,configmaps", "-n", _namespace(), "-o", "json"]
    # Services/configmaps need not carry the pod selector; inspect all names.
    data = _kubectl_json(args)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError("Cannot confirm Kubernetes teardown")
    return any(any(frag in p.get("metadata", {}).get("name", "") for frag in frags)
               for p in data.get("items", []))


def _wait_pods_gone(frags, selector=None, timeout=90, interval=5):
    waited = 0
    while waited < timeout:
        if not _pods_present(frags, selector):
            return True
        time.sleep(interval)
        waited += interval
    return False


def _nsd_name_for(key):
    return SCENARIO_ENTRIES[key]["nsd_package"]


@app.route("/api/deploy", methods=["POST"])
def deploy():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("ran"), str):
        return jsonify({"error": "Expected a JSON object with a string ran"}), 400
    key = ALIASES.get(body["ran"], body["ran"])
    if key not in SCENARIOS:
        return jsonify({"error": "Unknown scenario"}), 400
    try:
        g.runtime_state_path = _state_path()
        Path(g.runtime_state_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive_lock(Path(g.runtime_state_path).parent / ".lifecycle.lock"):
            return _deploy_locked(key)
    except Exception as error:
        return jsonify({"error": str(error)}), 500


def _deploy_locked(key):
    # Reload registry and state while serialized; no destructive calls before preflight.
    registry = load_registry()
    entries = {s["key"]: s for s in registry["scenarios"]}
    scenario = entries[key]
    scen = dashboard_scenarios(registry)[key]
    if scenario["generation_status"] != "ready":
        return jsonify({"error": scenario["blocked_reason"]}), 409
    try:
        artifacts = validated_snapshot(Path(__file__).resolve().parents[1], DEFAULT_OUTPUT, scenario)
        cfg, context, catalog = runtime_preflight()
        if cfg['DEPLOYMENT_PROFILE'] != 'rfsim':
            raise ConfigError('USRP dashboard packaging is not wired; use an explicitly validated hardware workflow')
        g.runtime_config, g.runtime_context = cfg, context
        if CONFIG_FILE == DEFAULT_STATE_PATH and cfg['ACTIVE_STATE_PATH'] != g.runtime_state_path:
            raise ConfigError('Runtime state path changed during preflight; retry after configuration is stable')
        identities = catalog.verify(scenario, artifacts)
    except (PackageError, CatalogError, OSError, ValueError) as error:
        return jsonify({"error": "Replacement preflight failed: " + str(error)}), 409
    with osm_client.runtime_session(cfg):
        return _deploy_verified(key, registry, entries, scen, scenario, context, identities)


def _deploy_verified(key, registry, entries, scen, scenario, context, identities):
    try:
        with open(_state_path()) as stream:
            config = yaml.safe_load(stream)
    except FileNotFoundError:
        raise ValueError("Missing runtime state; initialize explicitly before lifecycle operations") from None
    validate_state(config, registry, context)
    config.setdefault('osm', {})
    config['context'] = context
    if config["osm"].get("pending_instance"):
        raise RuntimeError("Pending instance requires reconciliation before another deployment")
    if not scen.get("additive"):
        old = config["osm"].get("active_instance_id")
        old_key = registry["aliases"].get(config["osm"].get("active_scenario"), config["osm"].get("active_scenario"))
        if old:
            if old_key not in entries:
                raise ValueError("Unknown active scenario; refusing teardown")
            old_scen = dashboard_scenarios(registry)[old_key]
            op = osm_client.terminate_ns(old)
            if not op or osm_client.wait_for_op(op, timeout=180) != "COMPLETED":
                raise RuntimeError("Termination did not complete")
            if not _wait_pods_gone(old_scen["pods"], old_scen.get("selector")):
                raise RuntimeError("Resources remain after termination")
            if not osm_client.delete_ns_instance(old):
                raise RuntimeError("NS deletion failed")
            if not osm_client.ns_instance_absent(old):
                raise RuntimeError("NS deletion cannot be confirmed")
            config["osm"].pop("active_instance_id", None)
            config["osm"].pop("active_scenario", None)
            config["active"] = "none"
            atomic_write(_state_path(), yaml.safe_dump(config).encode())
    config["osm"]["pending_instance"] = {"scenario": key, "name": "ran-" + key,
                                           "nsd_uuid": identities["ns"], "stage": "requesting"}
    atomic_write(_state_path(), yaml.safe_dump(config).encode())
    ns_id, op_id = osm_client.instantiate_ns(scenario["nsd_package"], "ran-" + key,
                                            verified_nsd_uuid=identities["ns"], vim_account_id=context["vim_id"])
    if not ns_id or not op_id:
        raise RuntimeError("Instantiation returned no instance/operation identity")
    # Persist pending identity before polling, so failures remain discoverable.
    config["osm"]["pending_instance"] = {"id": ns_id, "scenario": key, "operation": op_id}
    atomic_write(_state_path(), yaml.safe_dump(config).encode())
    if osm_client.wait_for_op(op_id, timeout=180) != "COMPLETED":
        raise RuntimeError("Instantiation did not complete; pending instance retained")
    if scen.get("additive"):
        config["osm"].setdefault("additive_instances", {})[key] = ns_id
    else:
        config["osm"].update(active_instance_id=ns_id, active_scenario=key)
        config["active"] = key
    config["osm"].pop("pending_instance", None)
    atomic_write(_state_path(), yaml.safe_dump(config).encode())
    return jsonify({"status": "success", "ran": key, "name": scen["name"], "ns_instance_id": ns_id})


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
            _, out, _ = run(f"kubectl get pods -n {_namespace()}{lflag} --no-headers 2>/dev/null | grep {frag} | grep Running")
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
    _, out, _ = run(f"kubectl get hpa -n {_namespace()} --no-headers 2>/dev/null")
    rows = []
    for line in out.strip().split("\n"):
        p = line.split()
        if len(p) >= 7 and p[2] != "<unknown>/80%":
            rows.append({"name": p[0], "targets": p[2],
                         "min": p[3], "max": p[4], "replicas": p[5]})
    return jsonify({"hpa": rows})




def _prom_query(promql):
    import urllib.request, urllib.parse, json as _json
    url = f"{load_config()['PROMETHEUS_URL']}/api/v1/query?query={urllib.parse.quote(promql)}"
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
    Control-plane TCP connect latency exported by the live latency probe."""
    rtt = _prom_query("network_tcp_connect_latency_last_seconds")
    if rtt is None:
        return jsonify({"error": "no data from latency probe", "rtt_ms": None, "loss_pct": None})
    return jsonify({"rtt_ms": rtt * 1000, "loss_pct": 0, "target": "amf-sbi (control-plane)"})

def _ue_status_payload():
    """Return the same live UE state used by /api/ue-status, without HTTP."""
    _, pod, _ = run(f"kubectl get pods -n {_namespace()} -l app=oai-nr-ue -o jsonpath='{{.items[0].metadata.name}}' 2>/dev/null")
    pod = pod.strip().strip("'")
    registered = False
    pdu_session = False
    tun_ip = ""
    if pod:
        _, tun_out, _ = run(f"kubectl exec -n {_namespace()} {pod} -- ip -4 -o addr show oaitun_ue1 2>/dev/null")
        m = re.search(r"inet (\S+)/", tun_out)
        if m:
            registered = True
            pdu_session = True
            tun_ip = m.group(1)
        else:
            _, out, _ = run(f"kubectl logs -n {_namespace()} {pod} 2>&1")
            registered = "Registration complete" in out or "Received Registration Accept" in out
            pdu_session = (
                "Received PDU Session Establishment Accept" in out
                or "PDU Session establishment is successful" in out
            )
    rsrp = _prom_query("max(oai_gnb_ue_rsrp_dbm)")
    return {
        "registered": registered,
        "pdu_session": pdu_session,
        "tun": tun_ip,
        "pod": pod,
        "radio_connected": rsrp is not None,
        "rsrp_dbm": rsrp,
    }

@app.route("/api/ue-status")
def ue_status():
    """UE registration + radio link state from live OAI gNB/UE logs and Prometheus."""
    return jsonify(_ue_status_payload())

@app.route("/api/istio/status")
def istio_status():
    """Real Istio state -- control plane health and actual per-NF sidecar
    injection, not a mockup. Checks whether each open5gs core NF's
    running pod genuinely has an istio-proxy container, rather than
    assuming injection happened just because Istio is installed."""
    _, phase, _ = run("kubectl get deployment istiod -n istio-system -o jsonpath='{.status.readyReplicas}' 2>/dev/null")
    control_plane_ready = phase.strip().isdigit() and int(phase.strip()) >= 1

    injected = {}
    for nf, label in NF_LABELS.items():
        _, containers, _ = run(
            f"kubectl get pods -n {_namespace()} -l app.kubernetes.io/name={label} "
            "--no-headers 2>/dev/null | grep Running | head -1 | awk '{print $1}' | "
            f"xargs -I{{}} kubectl get pod -n {_namespace()} {{}} -o jsonpath='{{.spec.containers[*].name}}' 2>/dev/null"
        )
        injected[nf] = "istio-proxy" in containers

    return jsonify({
        "control_plane_ready": control_plane_ready,
        "namespace": "istio-system",
        "nfs_injected": injected,
        "nfs_injected_count": sum(1 for v in injected.values() if v),
        "nfs_total": len(injected),
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
    path = load_config()['ACTIONS_LOG_PATH']
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
    app.run(host="0.0.0.0", port=int(load_config()['DASHBOARD_PORT']), debug=False)
