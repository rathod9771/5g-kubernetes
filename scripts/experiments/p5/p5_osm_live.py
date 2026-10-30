#!/usr/bin/env python3
"""P5 phase 1C: opt-in, restricted live OSM NBI identity observation.

All OSM requests are GET to a fixed Dell endpoint and fixed inventory paths.
Uses an existing private bearer token; never creates a token or logs raw NBI
records. Raw responses can contain credentials: only a sanitized, fixed-field
summary reaches stdout. Does not publish, mutate, or authorize deployment.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import ssl
import stat
import sys
from urllib import error as http_error
from urllib import request as http_request
import yaml

import p5_osm_identity as policy
import p5_target
# Reuse the P4 vetted, bounded VIM diagnostic instead of exposing raw fields.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "p4"))
from p4_vim import diagnose_vim

# The Kubernetes nbi-ingress.spec.rules[0].host observed on the Dell.
# Keep TLS SNI/HTTP Host and DNS verification bound to that exact ingress.
NBI_HOST = "nbi.osm.172.30.0.20.nip.io"
NBI = "https://" + NBI_HOST + ":30843"
NBI_IP = "172.30.0.20"
MAX_RESPONSE = 2 << 20
PORT_FORWARD_TIMEOUT = 12
LOOPBACK_FORWARD_LINE = re.compile(r"Forwarding from 127\\.0\\.0\\.1:([0-9]{1,5}) -> 9999\\Z")
TOKEN_FORMAT = re.compile(r"[A-Za-z0-9._~-]{16,4096}\Z")
PATHS = (
    "/admin/v1/projects",
    "/admin/v1/vim_accounts",
    "/admin/v1/k8sclusters",
    "/nslcm/v1/ns_instances",
    "/nslcm/v1/vnf_instances",
    "/vnfpkgm/v1/vnf_packages",
    "/nsd/v1/ns_descriptors",
)
OPERATIONS = "/nslcm/v1/ns_lcm_op_occs?nsInstanceId=" + policy.NS_ID
PATH_SET = frozenset(PATHS + (OPERATIONS,))


class LiveObservationError(ValueError):
    pass


def private_token(path):
    """Owner-only, no-symlink token read. Token is never included in errors."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > 4096):
                raise LiveObservationError("TOKEN_FILE_NOT_PRIVATE")
            value = os.read(fd, 4097).decode("ascii")
        finally:
            os.close(fd)
    except (OSError, UnicodeError):
        raise LiveObservationError("TOKEN_UNAVAILABLE") from None
    token = value.strip()
    if not TOKEN_FORMAT.fullmatch(token):
        raise LiveObservationError("TOKEN_FORMAT_INVALID")
    return token


def _dns_check(resolve=socket.getaddrinfo):
    try:
        addresses = {item[4][0] for item in resolve(NBI_HOST, 30843, type=socket.SOCK_STREAM)}
    except OSError:
        raise LiveObservationError("OSM_ENDPOINT_UNRESOLVED") from None
    if addresses != {NBI_IP}:
        raise LiveObservationError("OSM_ENDPOINT_NOT_DELL")


@contextmanager
def dell_nbi_tunnel():
    """Own a short-lived API-server port-forward: random localhost port, no shared listener.

    kubectl connects to the already pinned Dell API using the current context.
    HTTP bearer-token bytes travel only over the local loopback leg and the
    kubectl API-server tunnel; no Traefik-to-NBI plaintext network hop.
    """
    command = [
        "kubectl", "--context", p5_target.EXPECTED_CONTEXT,
        "--namespace", p5_target.OSM_NAMESPACE, "port-forward",
        "--address", "127.0.0.1", "service/nbi", ":9999",
    ]
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
    except OSError:
        raise LiveObservationError("PORT_FORWARD_UNAVAILABLE") from None
    try:
        ready, _, _ = select.select([proc.stdout], [], [], PORT_FORWARD_TIMEOUT)
        if not ready:
            raise LiveObservationError("PORT_FORWARD_UNAVAILABLE")
        line = proc.stdout.readline().strip()
        match = LOOPBACK_FORWARD_LINE.fullmatch(line)
        if not match or proc.poll() is not None:
            raise LiveObservationError("PORT_FORWARD_UNAVAILABLE")
        port = int(match.group(1))
        if not (1024 <= port <= 65535):
            raise LiveObservationError("PORT_FORWARD_UNAVAILABLE")
        yield port
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()


class _NoRedirect(http_request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def make_client(token, ca_file=None, *, loopback_port=None):
    """GET-only client: pinned verified HTTPS, or own loopback-only API port-forward.

    Local HTTP is permitted only after dell_nbi_tunnel() has selected a port.
    No configurable hosts, redirects, proxy forwarding, or non-GET calls.
    """
    if loopback_port is not None:
        if ca_file is not None or type(loopback_port) is not int or not (1024 <= loopback_port <= 65535):
            raise LiveObservationError("PORT_FORWARD_UNAVAILABLE")
        origin = "http://127.0.0.1:" + str(loopback_port)
        handler = http_request.HTTPHandler()
    else:
        origin = NBI
        context = ssl.create_default_context()
        if ca_file:
            path = Path(ca_file)
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
                    raise LiveObservationError("CA_FILE_UNAVAILABLE")
                context.load_verify_locations(cafile=str(path))
            except (OSError, ssl.SSLError):
                raise LiveObservationError("CA_FILE_UNAVAILABLE") from None
        handler = http_request.HTTPSHandler(context=context)
    opener = http_request.build_opener(
        http_request.ProxyHandler({}), handler, _NoRedirect())

    def get(path):
        if path not in PATH_SET:
            raise LiveObservationError("OSM_ENDPOINT_NOT_ALLOWLISTED")
        if loopback_port is None:
            _dns_check()
        req = http_request.Request(
            origin + "/osm" + path, headers={
                "Accept": "application/yaml", "Authorization": "Bearer " + token,
                "User-Agent": "5g-kubernetes-p5-dell-read-only"
            }, method="GET")
        try:
            with opener.open(req, timeout=15) as resp:
                if resp.status != 200:
                    raise LiveObservationError("OSM_READ_FAILED")
                if resp.headers.get("Content-Length") and int(resp.headers["Content-Length"]) > MAX_RESPONSE:
                    raise LiveObservationError("OSM_RESPONSE_TOO_LARGE")
                raw = resp.read(MAX_RESPONSE + 1)
        except (http_error.HTTPError, http_error.URLError, TimeoutError, OSError, ValueError):
            raise LiveObservationError("OSM_READ_FAILED") from None
        if len(raw) > MAX_RESPONSE:
            raise LiveObservationError("OSM_RESPONSE_TOO_LARGE")
        try:
            return yaml.safe_load(raw)
        except (yaml.YAMLError, UnicodeError, ValueError):
            raise LiveObservationError("OSM_RESPONSE_MALFORMED") from None

    return get

def _list(value):
    if not isinstance(value, list) or len(value) > 128 or any(not isinstance(x, dict) for x in value):
        raise LiveObservationError("OSM_INVENTORY_MALFORMED")
    return value


def _name(x):
    return x.get("name", x.get("nsName"))


def _id(x):
    return x.get("_id", x.get("id"))


def _admin(x):
    return x.get("_admin") if isinstance(x.get("_admin"), dict) else {}


def _revision(x):
    return _admin(x).get("revision", 0)


def sanitize(get):
    """NBI GET inventory -> strict metadata contract. No raw content leaves scope."""
    observed = {}
    for path in PATHS:
        observed[path] = _list(get(path))
    observed[OPERATIONS] = _list(get(OPERATIONS))
    projects = observed["/admin/v1/projects"]
    vims = observed["/admin/v1/vim_accounts"]
    clusters = observed["/admin/v1/k8sclusters"]
    ns_records = observed["/nslcm/v1/ns_instances"]
    operations = observed[OPERATIONS]

    instantiations = [op for op in operations
                      if (op.get("nsInstanceId") == policy.NS_ID and
                          op.get("lcmOperationType") == "instantiate" and
                          op.get("operationState") == "COMPLETED")]
    # Only use a unique, completed instantiate as the source of the VIM binding.
    params = instantiations[0].get("operationParams", {}) if len(instantiations) == 1 else {}
    if not isinstance(params, dict):
        params = {}
    vim_for_ns = params.get("vimAccountId") if (
        params.get("nsdId") and params.get("nsName") == policy.NS_NAME) else None

    def project(x):
        return {"name": x.get("name"), "id": _id(x)}

    def vim(x):
        return {"name": x.get("name"), "id": _id(x),
                "diagnostic": diagnose_vim(x)["status"]}

    def cluster(x):
        return {"name": x.get("name"), "id": _id(x),
                "vim_id": x.get("vim_account"),
                "helm_v3": (x.get("deployment_methods") or {}).get("helm-chart-v3") is True
                           if isinstance(x.get("deployment_methods"), dict) else False,
                "state": _admin(x).get("operationalState", "UNKNOWN")}

    def ns(x):
        admin = _admin(x)
        deployed = admin.get("deployed", {})
        deployed = deployed.get("K8s", []) if isinstance(deployed, dict) else []
        if not isinstance(deployed, list):
            deployed = []
        kdus = [{
            "name": k.get("kdu-name"), "release": k.get("kdu-instance"),
            "namespace": k.get("namespace"), "model": k.get("kdu-model")
        } for k in deployed if isinstance(k, dict)]
        return {
            "name": _name(x), "id": _id(x),
            "ns_state": x.get("nsState"), "operation": x.get("currentOperation"),
            "status": x.get("operational-status"), "nsd_id": x.get("nsd-id"),
            "vim_id": vim_for_ns if _id(x) == policy.NS_ID else None,
            "kdus": kdus,
        }

    def vnf(x):
        return {"id": _id(x), "ns_id": x.get("nsr-id-ref"), "vnfd_id": x.get("vnfd-id")}

    def operation(x):
        return {"id": _id(x), "ns_id": x.get("nsInstanceId"),
                "state": x.get("operationState")}

    def knf(x):
        return {"name": x.get("id"), "id": _id(x), "revision": _revision(x)}

    def nsd(x):
        return {"name": x.get("id"), "id": _id(x), "vnfd_ids": x.get("vnfd-id")}

    return {
        "schema": policy.SCHEMA, "schema_version": 1, "target": "dell-p4",
        "projects": [project(x) for x in projects],
        "vims": [vim(x) for x in vims],
        "clusters": [cluster(x) for x in clusters],
        "ns_instances": [ns(x) for x in ns_records],
        "vnf_instances": [vnf(x) for x in observed["/nslcm/v1/vnf_instances"]],
        "operations": [operation(x) for x in operations],
        "knf_packages": [knf(x) for x in observed["/vnfpkgm/v1/vnf_packages"]],
        "nsd_packages": [nsd(x) for x in observed["/nsd/v1/ns_descriptors"]],
    }


def observe(token_file, ca_file=None, *, transport="https", collect=p5_target.collect,
            evaluate_target=p5_target.evaluate, resolve=socket.getaddrinfo,
            client_factory=make_client, tunnel_factory=dell_nbi_tunnel):
    """Check Dell Kubernetes baseline first; then exact, authenticated NBI GETs."""
    baseline = evaluate_target(collect())
    if baseline.get("status") != "OBSERVED" or baseline.get("deployment_authorized") is not False:
        raise LiveObservationError("KUBERNETES_BASELINE_BLOCKED")
    _dns_check(resolve)
    if transport not in ("https", "kubernetes-port-forward"):
        raise LiveObservationError("TRANSPORT_NOT_ALLOWED")
    if transport == "kubernetes-port-forward":
        if ca_file is not None:
            raise LiveObservationError("CA_FILE_WITH_LOOPBACK_UNSUPPORTED")
        # No token read or NBI GET until our own kubectl tunnel is ready.
        with tunnel_factory() as port:
            token = private_token(token_file)
            get = client_factory(token, None, loopback_port=port)
            snapshot = sanitize(get)
    else:
        token = private_token(token_file)
        get = client_factory(token, ca_file)
        snapshot = sanitize(get)
    review = policy.evaluate(snapshot)
    healthy = review["status"] == "CONSISTENT_SNAPSHOT"
    return {
        "schema": "5g-kubernetes/p5-live-osm-observation",
        "schema_version": 1,
        "status": "OBSERVED" if healthy else "BLOCKED",
        "target": "dell-p4",
        "live_osm_identity_verified": healthy,
        "osm_vim_registration_verified": healthy,
        "live_vim_health_verified": False,
        "deployment_authorized": False,
        "catalog_publication_authorized": False,
        "mutations_performed": False,
        "blockers": sorted(set(review["blockers"]) - {"NO_AUTHENTICATED_LIVE_OSM_OBSERVATION"}
                           if healthy else review["blockers"]),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--observe", action="store_true", required=True,
                   help="Explicitly perform only allowlisted GETs to the pinned Dell NBI")
    p.add_argument("--token-file", type=Path, required=True, help="Existing private NBI bearer token file; never shown")
    p.add_argument("--ca-file", type=Path, help="Trusted CA certificate for pinned HTTPS transport")
    p.add_argument("--transport", choices=("https", "kubernetes-port-forward"), default="https",
                   help="Opt-in Dell-only short-lived loopback kubectl port-forward; no custom endpoint")
    a = p.parse_args(argv)
    try:
        result = observe(a.token_file, a.ca_file, transport=a.transport)
    except (LiveObservationError, policy.SnapshotError, p5_target.ObservationError,
            OSError, ValueError, TypeError, KeyError, AttributeError):
        result = {
            "status": "REJECTED", "reason_code": "OSM_LIVE_READ_REFUSED",
            "deployment_authorized": False, "mutations_performed": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
