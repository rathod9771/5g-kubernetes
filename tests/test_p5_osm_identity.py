"""P5 Phase 1C synthetic sanitized OSM metadata regression tests.

No real OSM URLs, credentials, kubeconfig, GitHub, or subprocess calls.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/experiments/p5"))
import p5_osm_identity as osm

VIM_ID = "11111111-1111-4111-8111-111111111111"
CLUSTER_ID = "22222222-2222-4222-8222-222222222222"
PROJECT_ID = "33333333-3333-4333-8333-333333333333"
KNF_ID = "44444444-4444-4444-8444-444444444444"
NSD_ID = "55555555-5555-4555-8555-555555555555"
OP_ID = "66666666-6666-4666-8666-666666666666"
VNF_ID = "77777777-7777-4777-8777-777777777777"


def snapshot():
    def kdu(name):
        return {
            "name": name, "release": "p4kdu-" + name, "namespace": osm.NAMESPACE,
            "model": "/app/storage/" + KNF_ID + ":2/p4kdu_knf/helm-chart-v3s/" + name,
        }
    return {
        "schema": osm.SCHEMA, "schema_version": 1, "target": "dell-p4",
        "projects": [{"name": "admin", "id": PROJECT_ID}],
        "vims": [{"name": "dummyvim", "id": VIM_ID, "diagnostic": "INCONCLUSIVE"}],
        "clusters": [{
            "name": "lab-cluster", "id": CLUSTER_ID, "vim_id": VIM_ID,
            "helm_v3": True, "state": "ENABLED",
        }],
        "knf_packages": [{"name": osm.KNF, "id": KNF_ID, "revision": 2}],
        "nsd_packages": [{"name": osm.NSD, "id": NSD_ID, "vnfd_ids": [KNF_ID]}],
        "ns_instances": [{
            "name": osm.NS_NAME, "id": osm.NS_ID, "ns_state": "READY",
            "operation": "IDLE", "status": "running", "nsd_id": NSD_ID,
            "vim_id": VIM_ID, "kdus": [kdu("cu"), kdu("du")],
        }],
        "vnf_instances": [{"id": VNF_ID, "ns_id": osm.NS_ID, "vnfd_id": KNF_ID}],
        "operations": [{"id": OP_ID, "ns_id": osm.NS_ID, "state": "COMPLETED"}],
    }


class OSMIdentityTests(unittest.TestCase):
    def test_consistent_is_not_live_attestation(self):
        result = osm.evaluate(snapshot())
        self.assertEqual(result["status"], "CONSISTENT_SNAPSHOT")
        self.assertEqual(result["source"], "UNTRUSTED_OFFLINE_METADATA")
        self.assertFalse(result["live_osm_identity_verified"])
        self.assertFalse(result["live_vim_health_verified"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["catalog_publication_authorized"])
        self.assertFalse(result["mutations_performed"])

    def test_exact_disposable_ns_required(self):
        s = snapshot()
        s["ns_instances"][0]["id"] = PROJECT_ID
        r = osm.evaluate(s)
        self.assertEqual(r["status"], "BLOCKED")
        self.assertIn("P4_NS_IDENTITY_OR_STATE_MISMATCH", r["blockers"])

    def test_duplicate_ns_name_blocks(self):
        s = snapshot()
        s["ns_instances"].append(copy.deepcopy(s["ns_instances"][0]))
        self.assertIn("P4_NS_IDENTITY_OR_STATE_MISMATCH", osm.evaluate(s)["blockers"])

    def test_vim_cluster_binding_required(self):
        s = snapshot()
        s["clusters"][0]["vim_id"] = PROJECT_ID
        self.assertIn("VIM_K8S_REGISTRATION_MISMATCH", osm.evaluate(s)["blockers"])

    def test_missing_helm_v3_registration_blocks(self):
        s = snapshot()
        s["clusters"][0]["helm_v3"] = False
        self.assertIn("VIM_K8S_REGISTRATION_MISMATCH", osm.evaluate(s)["blockers"])

    def test_unhealthy_ns_or_mutation_blocks(self):
        for field, value in (("ns_state", "BROKEN"), ("operation", "PROCESSING"),
                             ("status", "failed"), ("vim_id", PROJECT_ID)):
            s = snapshot()
            s["ns_instances"][0][field] = value
            with self.subTest(field=field):
                self.assertIn("P4_NS_IDENTITY_OR_STATE_MISMATCH",
                              osm.evaluate(s)["blockers"])

    def test_kdu_package_revision_and_names_restricted(self):
        for changed, value in (("namespace", "osm"), ("release", "bad"),
                               ("model", "bad")):
            s = snapshot()
            s["ns_instances"][0]["kdus"][0][changed] = value
            with self.subTest(field=changed):
                self.assertIn("P4_KDU_INVENTORY_MISMATCH",
                              osm.evaluate(s)["blockers"])

    def test_inflight_operation_blocks(self):
        s = snapshot()
        s["operations"][0]["state"] = "PROCESSING"
        self.assertIn("P4_LCM_OPERATION_UNVERIFIED", osm.evaluate(s)["blockers"])

    def test_vnf_binding_blocks(self):
        s = snapshot()
        s["vnf_instances"][0]["vnfd_id"] = PROJECT_ID
        self.assertIn("P4_VNF_BINDING_MISMATCH", osm.evaluate(s)["blockers"])

    def test_malformed_snapshot_rejected(self):
        for value in ({"schema": osm.SCHEMA}, "broken", [], None):
            with self.subTest(value=value), self.assertRaises(osm.SnapshotError):
                osm.evaluate(value)

    def test_raw_secret_data_is_not_accepted(self):
        s = snapshot()
        s["vims"][0]["config"] = {"password": "private-material"}
        with self.assertRaisesRegex(osm.SnapshotError, "INVALID_METADATA_FIELDS"):
            osm.evaluate(s)
        self.assertNotIn("private-material", str(osm.evaluate(snapshot())))

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(osm.SnapshotError, "DUPLICATE_JSON_KEY"):
            osm.parse_strict(b'{"target":"dell-p4","target":"hp-lab"}')

    def test_synthetic_offline_cli(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p5-sanitized.json"
            p.write_text(json.dumps(snapshot()))
            self.assertEqual(osm.main(["--snapshot", str(p)]), 0)

    def test_cli_rejects_raw_and_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "unsafe.json"
            s = snapshot()
            s["vims"][0]["password"] = "cannot export"
            p.write_text(json.dumps(s))
            self.assertEqual(osm.main(["--snapshot", str(p)]), 2)
            self.assertEqual(osm.main(["--snapshot", str(p.with_suffix(".missing"))]), 2)


if __name__ == "__main__":
    unittest.main()
