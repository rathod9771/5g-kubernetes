#!/usr/bin/env python3
"""
Minimal OSM NBI client for the RAN selector backend.
Reuses the exact request patterns proven working via curl throughout
this project (see scripts/osm-onboard.sh for the same auth pattern).
"""
import subprocess
import time
import os
import yaml

OSM_HOST = "https://gui.172.30.18.32.nip.io:30843"
OSM_USER = "admin"
OSM_PASS = os.environ.get("OSM_PASSWORD", "admin")
VIM_ACCOUNT_ID = "b0481f03-f5eb-47a2-9a20-bd72430b3b13"  # dummyvim, set after full OSM rebuild

_token_cache = {"token": None, "fetched_at": 0}
_nsd_cache = {}  # nsd_name -> uuid


def _curl(args, data=None):
    cmd = ["curl", "-sk"] + args
    result = subprocess.run(cmd, input=data, capture_output=True, text=True, timeout=30)
    return result.stdout


def get_token(force=False):
    if not force and _token_cache["token"] and (time.time() - _token_cache["fetched_at"] < 2700):
        return _token_cache["token"]
    body = f"username: {OSM_USER}\npassword: {OSM_PASS}\nproject-id: admin"
    out = _curl([
        "-X", "POST", f"{OSM_HOST}/osm/admin/v1/tokens",
        "-H", "Content-Type: application/yaml",
        "--data-binary", "@-"
    ], data=body)
    parsed = yaml.safe_load(out)
    token = parsed.get("id") if parsed else None
    if not token:
        raise RuntimeError(f"Failed to get OSM token: {out}")
    _token_cache["token"] = token
    _token_cache["fetched_at"] = time.time()
    return token


def _auth_curl(args, data=None, retry_on_401=True):
    token = get_token()
    out = _curl(["-H", f"Authorization: Bearer {token}"] + args, data=data)
    if retry_on_401 and ("UNAUTHORIZED" in out or "Expired Token" in out):
        token = get_token(force=True)
        out = _curl(["-H", f"Authorization: Bearer {token}"] + args, data=data)
    return out


def get_nsd_uuid(nsd_name, force=False):
    if not force and nsd_name in _nsd_cache:
        return _nsd_cache[nsd_name]
    out = _auth_curl(["-X", "GET", f"{OSM_HOST}/osm/nsd/v1/ns_descriptors"])
    parsed = yaml.safe_load(out)
    if not parsed:
        raise RuntimeError(f"Failed to list NSDs: {out}")
    for entry in parsed:
        if entry.get("id") == nsd_name:
            uuid = entry.get("_id")
            _nsd_cache[nsd_name] = uuid
            return uuid
    raise RuntimeError(f"NSD '{nsd_name}' not found in OSM catalog")


def instantiate_ns(nsd_name, ns_name, description="Deployed via RAN selector dashboard"):
    nsd_id = get_nsd_uuid(nsd_name)
    body = (f"nsdId: {nsd_id}\nnsName: {ns_name}\n"
            f"nsDescription: {description}\nvimAccountId: {VIM_ACCOUNT_ID}")
    out = _auth_curl([
        "-w", "\nHTTP:%{http_code}\n",
        "-X", "POST", f"{OSM_HOST}/osm/nslcm/v1/ns_instances_content",
        "-H", "Content-Type: application/yaml",
        "--data-binary", "@-"
    ], data=body)
    if "HTTP:201" not in out and "HTTP:202" not in out:
        raise RuntimeError(f"Instantiate failed for {nsd_name}: {out}")
    parsed = yaml.safe_load(out.split("HTTP:")[0])
    return parsed.get("id"), parsed.get("nslcmop_id")


def terminate_ns(ns_instance_id):
    out = _auth_curl([
        "-w", "\nHTTP:%{http_code}\n",
        "-X", "POST", f"{OSM_HOST}/osm/nslcm/v1/ns_instances/{ns_instance_id}/terminate",
        "-H", "Content-Length: 0"
    ])
    if "HTTP:200" not in out and "HTTP:202" not in out:
        raise RuntimeError(f"Terminate failed for {ns_instance_id}: {out}")
    parsed = yaml.safe_load(out.split("HTTP:")[0])
    return parsed.get("id") if parsed else None


def delete_ns_instance(ns_instance_id):
    out = _auth_curl([
        "-w", "\nHTTP:%{http_code}\n",
        "-X", "DELETE", f"{OSM_HOST}/osm/nslcm/v1/ns_instances/{ns_instance_id}"
    ])
    return "HTTP:204" in out or "HTTP:200" in out


def get_op_state(nslcmop_id):
    out = _auth_curl(["-X", "GET", f"{OSM_HOST}/osm/nslcm/v1/ns_lcm_op_occs/{nslcmop_id}"])
    parsed = yaml.safe_load(out)
    return parsed.get("operationState") if parsed else None


def wait_for_op(nslcmop_id, timeout=180, interval=5):
    """Poll an LCM operation until it leaves PROCESSING/COMPLETING, or timeout."""
    waited = 0
    while waited < timeout:
        state = get_op_state(nslcmop_id)
        if state in ("COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "FAILED_TEMP"):
            return state
        time.sleep(interval)
        waited += interval
    return "TIMEOUT"
