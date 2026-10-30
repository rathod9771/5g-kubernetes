"""Synthetic-only P5 live NBI client tests, with no network or real OSM creds."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/experiments/p5"))
import p5_osm_live as live
import p5_osm_identity as offline

PROJECT_ID = "33333333-3333-4333-8333-333333333333"
VIM_ID = "11111111-1111-4111-8111-111111111111"
CLUSTER_ID = "22222222-2222-4222-8222-222222222222"
KNF_ID = "44444444-4444-4444-8444-444444444444"
NSD_ID = "55555555-5555-4555-8555-555555555555"
OP_ID = "66666666-6666-4666-8666-666666666666"
VNF_ID = "77777777-7777-4777-8777-777777777777"


def raw():
    def model(name):
        return ("/app/storage/" + KNF_ID + ":2/p4kdu_knf/helm-chart-v3s/" + name)
    return {
        "/admin/v1/projects": [{"name": "admin", "_id": PROJECT_ID}],
        "/admin/v1/vim_accounts": [{"name": "dummyvim", "_id": VIM_ID,
            "config": {"password": "DO_NOT_EXPORT_THIS_PASSWORD"},
            "_admin": {"deployed": {"RO": {"operationalState": "ENABLED"}}}}],
        "/admin/v1/k8sclusters": [{"name": "lab-cluster", "_id": CLUSTER_ID,
            "vim_account": VIM_ID, "deployment_methods": {"helm-chart-v3": True},
            "_admin": {"operationalState": "ENABLED"}}],
        "/nslcm/v1/ns_instances": [{"name": offline.NS_NAME, "_id": offline.NS_ID,
            "nsState": "READY", "currentOperation": "IDLE", "operational-status": "running",
            "nsd-id": NSD_ID, "_admin": {"deployed": {"K8s": [
                {"kdu-name": "cu", "kdu-instance": "p4kdu-cu",
                 "namespace": "p4-kdu-upgrade", "kdu-model": model("cu")},
                {"kdu-name": "du", "kdu-instance": "p4kdu-du",
                 "namespace": "p4-kdu-upgrade", "kdu-model": model("du")},
            ]}}}],
        "/nslcm/v1/vnf_instances": [{"_id": VNF_ID, "nsr-id-ref": offline.NS_ID, "vnfd-id": KNF_ID}],
        "/vnfpkgm/v1/vnf_packages": [{"_id": KNF_ID, "id": offline.KNF,
                                    "_admin": {"revision": 3}}],
        "/nsd/v1/ns_descriptors": [{"_id": NSD_ID, "id": offline.NSD, "vnfd-id": [KNF_ID]}],
        live.OPERATIONS: [{"_id": OP_ID, "nsInstanceId": offline.NS_ID,
                           "lcmOperationType": "instantiate", "operationState": "COMPLETED",
                           "operationParams": {"nsdId": NSD_ID, "nsName": offline.NS_NAME,
                                               "vimAccountId": VIM_ID}}],
    }


def resolver(host, port, *, type):
    assert host == live.NBI_HOST
    assert port == 30843
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (live.NBI_IP, port))]


class OSMClientTests(unittest.TestCase):
    def test_nbi_origin_is_pinned_to_actual_dell_ingress(self):
        # Observed via kubectl get ingress nbi-ingress -n osm on the Dell.
        self.assertEqual(live.NBI_HOST, "nbi.osm.172.30.0.20.nip.io")
        self.assertEqual(live.NBI, "https://nbi.osm.172.30.0.20.nip.io:30843")
        self.assertEqual(live.NBI_IP, "172.30.0.20")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token_file = Path(self.temp.name) / "osm-token"
        self.token_file.write_text("simulated-read-only-token-12345678")
        self.token_file.chmod(0o600)

    def test_sanitization_only_copies_allowlisted_metadata(self):
        original = raw()
        snapshot = live.sanitize(original.__getitem__)
        self.assertEqual(offline.evaluate(snapshot)["status"], "CONSISTENT_SNAPSHOT")
        self.assertNotIn("DO_NOT_EXPORT_THIS_PASSWORD", json.dumps(snapshot))
        self.assertEqual(snapshot["ns_instances"][0]["vim_id"], VIM_ID)
        self.assertEqual(snapshot["vims"][0]["diagnostic"], "INCONCLUSIVE")

    def test_observe_reports_authenticated_identity_but_no_deploy(self):
        original = raw()
        calls = []
        def get(path):
            calls.append(path)
            return original[path]
        result = live.observe(
            self.token_file, collect=lambda: {"observation": True},
            evaluate_target=lambda _: {"status": "OBSERVED", "deployment_authorized": False},
            resolve=resolver, client_factory=lambda token, ca: get)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertTrue(result["live_osm_identity_verified"])
        self.assertTrue(result["osm_vim_registration_verified"])
        self.assertFalse(result["live_vim_health_verified"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["catalog_publication_authorized"])
        self.assertFalse(result["mutations_performed"])
        self.assertEqual(set(calls), live.PATH_SET)
        self.assertNotIn("DO_NOT_EXPORT_THIS_PASSWORD", json.dumps(result))

    def test_mutation_not_exposed_to_read_only_getter(self):
        with mock.patch.object(live.http_request, "build_opener") as mocked:
            getter = live.make_client("simulated-read-only-token-12345678")
            with self.assertRaisesRegex(live.LiveObservationError, "OSM_ENDPOINT_NOT_ALLOWLISTED"):
                getter("/nslcm/v1/ns_instances/" + offline.NS_ID + "/action")
            mocked.return_value.open.assert_not_called()

    def test_wrong_host_or_cluster_aborts_before_token_or_nbi(self):
        with mock.patch.object(live, "private_token", side_effect=AssertionError("token must not be read")):
            with self.assertRaisesRegex(live.LiveObservationError, "KUBERNETES_BASELINE_BLOCKED"):
                live.observe(self.token_file, collect=lambda: {},
                    evaluate_target=lambda _: {"status": "BLOCKED"},
                    resolve=resolver, client_factory=lambda *args: None)

    def test_misdirected_dns_refused_before_token(self):
        def hp_dns(host, port, *, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", port))]
        with mock.patch.object(live, "private_token", side_effect=AssertionError("token must not be read")):
            with self.assertRaisesRegex(live.LiveObservationError, "OSM_ENDPOINT_NOT_DELL"):
                live.observe(self.token_file, collect=lambda: {},
                    evaluate_target=lambda _: {"status": "OBSERVED", "deployment_authorized": False},
                    resolve=hp_dns, client_factory=lambda *args: None)

    def test_incorrect_ns_uuid_remains_blocked(self):
        original = raw()
        original["/nslcm/v1/ns_instances"][0]["_id"] = PROJECT_ID
        result = live.observe(self.token_file, collect=lambda: {},
            evaluate_target=lambda _: {"status": "OBSERVED", "deployment_authorized": False},
            resolve=resolver, client_factory=lambda token, ca: original.__getitem__)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["live_osm_identity_verified"])

    def test_missing_unique_instantiate_vim_binding_blocks(self):
        original = raw()
        original[live.OPERATIONS][0]["operationParams"].pop("vimAccountId")
        result = live.observe(self.token_file, collect=lambda: {},
            evaluate_target=lambda _: {"status": "OBSERVED", "deployment_authorized": False},
            resolve=resolver, client_factory=lambda token, ca: original.__getitem__)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("P4_NS_IDENTITY_OR_STATE_MISMATCH", result["blockers"])

    def test_private_token_file_enforced(self):
        self.assertEqual(live.private_token(self.token_file), "simulated-read-only-token-12345678")
        self.token_file.chmod(0o644)
        with self.assertRaisesRegex(live.LiveObservationError, "TOKEN_FILE_NOT_PRIVATE"):
            live.private_token(self.token_file)
        link = Path(self.temp.name) / "token-link"
        link.symlink_to(self.token_file)
        with self.assertRaisesRegex(live.LiveObservationError, "TOKEN_UNAVAILABLE"):
            live.private_token(link)

    def test_explicit_loopback_mode_uses_owned_tunnel_and_no_ca(self):
        original = raw()
        events = []

        @contextmanager
        def tunnel():
            events.append("tunnel_open")
            try:
                yield 29876
            finally:
                events.append("tunnel_closed")

        def client(token, ca, *, loopback_port):
            self.assertIsNone(ca)
            self.assertEqual(loopback_port, 29876)
            self.assertEqual(token, "simulated-read-only-token-12345678")
            events.append("client_ready")
            return original.__getitem__

        result = live.observe(
            self.token_file, transport="kubernetes-port-forward",
            collect=lambda: {}, evaluate_target=lambda _: {
                "status": "OBSERVED", "deployment_authorized": False,
            }, resolve=resolver, client_factory=client, tunnel_factory=tunnel)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["mutations_performed"])
        self.assertEqual(events, ["tunnel_open", "client_ready", "tunnel_closed"])

    def test_loopback_rejects_ca_file_before_tunnel_and_token(self):
        with mock.patch.object(live, "private_token", side_effect=AssertionError("token read")), \
             mock.patch.object(live, "dell_nbi_tunnel", side_effect=AssertionError("tunnel opened")):
            with self.assertRaisesRegex(live.LiveObservationError, "CA_FILE_WITH_LOOPBACK_UNSUPPORTED"):
                live.observe(
                    self.token_file, ca_file=Path("/unused/root.crt"),
                    transport="kubernetes-port-forward",
                    collect=lambda: {}, evaluate_target=lambda _: {
                        "status": "OBSERVED", "deployment_authorized": False,
                    }, resolve=resolver)

    def test_loopback_wrong_target_aborts_before_tunnel_or_token(self):
        with mock.patch.object(live, "private_token", side_effect=AssertionError("token read")):
            def forbidden_tunnel():
                raise AssertionError("must not establish port-forward")
            with self.assertRaisesRegex(live.LiveObservationError, "KUBERNETES_BASELINE_BLOCKED"):
                live.observe(
                    self.token_file, transport="kubernetes-port-forward",
                    collect=lambda: {}, evaluate_target=lambda _: {"status": "BLOCKED"},
                    resolve=resolver, tunnel_factory=forbidden_tunnel)

    def test_loopback_missing_token_still_closes_tunnel(self):
        events = []
        @contextmanager
        def tunnel():
            events.append("open")
            try:
                yield 29876
            finally:
                events.append("close")
        with mock.patch.object(live, "private_token", side_effect=live.LiveObservationError("TOKEN_UNAVAILABLE")):
            with self.assertRaisesRegex(live.LiveObservationError, "TOKEN_UNAVAILABLE"):
                live.observe(
                    self.token_file, transport="kubernetes-port-forward",
                    collect=lambda: {}, evaluate_target=lambda _: {
                        "status": "OBSERVED", "deployment_authorized": False,
                    }, resolve=resolver, tunnel_factory=tunnel)
        self.assertEqual(events, ["open", "close"])

    def test_owned_tunnel_command_and_cleanup(self):
        proc = mock.Mock()
        proc.stdout = io.StringIO("Forwarding from 127.0.0.1:23871 -> 9999\n")
        proc.poll.return_value = None
        with mock.patch.object(live.subprocess, "Popen", return_value=proc) as popen, \
             mock.patch.object(live.select, "select", return_value=([proc.stdout], [], [])):
            with live.dell_nbi_tunnel() as port:
                self.assertEqual(port, 23871)
            proc.terminate.assert_called_once()
            proc.wait.assert_called_once_with(timeout=5)
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [
                "kubectl", "--context", "kubernetes-admin@kubernetes",
                "--namespace", "osm", "port-forward", "--address", "127.0.0.1",
                "service/nbi", ":9999",
            ])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["stderr"], live.subprocess.DEVNULL)

    def test_unexpected_forward_line_rejected_and_process_closed(self):
        proc = mock.Mock()
        proc.stdout = io.StringIO("Forwarding from 0.0.0.0:23871 -> 9999\n")
        proc.poll.return_value = None
        with mock.patch.object(live.subprocess, "Popen", return_value=proc), \
             mock.patch.object(live.select, "select", return_value=([proc.stdout], [], [])):
            with self.assertRaisesRegex(live.LiveObservationError, "PORT_FORWARD_UNAVAILABLE"):
                with live.dell_nbi_tunnel():
                    self.fail("untrusted local listener was accepted")
            proc.terminate.assert_called_once()

    def test_loopback_getter_is_fixed_http_localhost_get_only(self):
        fake = mock.Mock()
        fake_response = fake.open.return_value.__enter__.return_value
        fake_response.status = 200
        fake_response.headers = {}
        fake_response.read.return_value = b"[]"
        with mock.patch.object(live.http_request, "build_opener", return_value=fake):
            get = live.make_client("simulated-read-only-token-12345678",
                                   loopback_port=23871)
            self.assertEqual(get("/admin/v1/projects"), [])
            request = fake.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:23871/osm/admin/v1/projects")
            self.assertEqual(request.get_method(), "GET")
            with self.assertRaisesRegex(live.LiveObservationError, "OSM_ENDPOINT_NOT_ALLOWLISTED"):
                get("/nslcm/v1/ns_instances/action")
            self.assertEqual(fake.open.call_count, 1)

    def test_loopback_rejects_arbitrary_port_and_ca(self):
        for port in ("23871", 0, 80, -1, 70000, True):
            with self.subTest(port=port), self.assertRaisesRegex(
                    live.LiveObservationError, "PORT_FORWARD_UNAVAILABLE"):
                live.make_client("fake-read-only-token", loopback_port=port)
        with self.assertRaisesRegex(live.LiveObservationError, "PORT_FORWARD_UNAVAILABLE"):
            live.make_client("fake-read-only-token", Path("/unused"), loopback_port=23871)

    def test_cli_transport_is_explicit_and_rejected_without_token(self):
        with mock.patch.object(live, "observe", side_effect=live.LiveObservationError("TOKEN_UNAVAILABLE")) as observe:
            self.assertEqual(live.main(["--observe", "--transport",
                                        "kubernetes-port-forward", "--token-file",
                                        str(self.token_file)]), 2)
            self.assertEqual(observe.call_args.kwargs["transport"], "kubernetes-port-forward")

    def test_cli_rejects_missing_token_without_network(self):
        with mock.patch.object(live, "observe", side_effect=live.LiveObservationError("TOKEN_UNAVAILABLE")):
            self.assertEqual(live.main(["--observe", "--token-file", str(self.token_file)]), 2)


if __name__ == "__main__":
    unittest.main()
