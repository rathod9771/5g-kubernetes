"""Synthetic-only P5 live NBI client tests, with no network or real OSM creds."""
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

    def test_cli_rejects_missing_token_without_network(self):
        with mock.patch.object(live, "observe", side_effect=live.LiveObservationError("TOKEN_UNAVAILABLE")):
            self.assertEqual(live.main(["--observe", "--token-file", str(self.token_file)]), 2)


if __name__ == "__main__":
    unittest.main()
