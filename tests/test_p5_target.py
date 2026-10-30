"""Synthetic-only P5 target observation tests. Never contact a real cluster."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/experiments/p5"))
import p5_target as target


def pod(name, containers=("main",), restarts=0, ready=True):
    return {
        "metadata": {"name": name},
        "spec": {"containers": [{"name": n} for n in containers]},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"name": n, "ready": ready, "restartCount": restarts} for n in containers
            ],
        },
    }


def list_of(*items):
    return {"kind": "List", "items": list(items)}


def snapshot():
    deployments = []
    releases = []
    for name, (revision, chart, version) in target.EXPECTED_HELM.items():
        deployments.append({
            "metadata": {"name": name},
            "spec": {"replicas": 1},
            "status": {"readyReplicas": 1, "availableReplicas": 1},
        })
        releases.append({
            "name": name,
            "namespace": target.P4_NAMESPACE,
            "revision": revision,
            "status": "deployed",
            "chart": chart,
            "app_version": version,
        })
    return {
        "hostname": target.EXPECTED_HOST,
        "context": target.EXPECTED_CONTEXT,
        "api_server": target.EXPECTED_SERVER,
        "namespace": {
            "metadata": {"name": target.P4_NAMESPACE},
            "status": {"phase": "Active"},
        },
        "deployments": list_of(*deployments),
        "pods": list_of(pod("p4kdu-cu-test"), pod("p4kdu-du-test")),
        "helm": releases,
        "osm_pods": list_of(pod("nbi-test"), pod("lcm-test"), pod("ro-test"),
                            pod("osm-scheduler-test", ("scheduler", "watcher"), restarts=3)),
        "osm_services": list_of(
            {"metadata": {"name": "nbi"},
             "spec": {"type": "ClusterIP", "ports": [{"port": 9999}]}},
            {"metadata": {"name": "ro"},
             "spec": {"type": "ClusterIP", "ports": [{"port": 9090}]}},
        ),
    }


class TargetTests(unittest.TestCase):
    def test_matching_dell_baseline_is_observation_not_authorization(self):
        result = target.evaluate(snapshot())
        self.assertEqual(result["status"], "OBSERVED")
        self.assertTrue(result["kubernetes_identity_matches_expected"])
        self.assertTrue(result["p4_baseline_matches_expected"])
        self.assertTrue(result["osm_kubernetes_ready"])
        self.assertFalse(result["osm_nbi_ns_state_verified"])
        self.assertFalse(result["osm_vim_registration_verified"])
        self.assertFalse(result["catalog_publication_authorized"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["mutations_performed"])

    def test_p4_restarts_or_readiness_failure_block(self):
        for field in ("ready", "restartCount"):
            state = snapshot()
            status = state["pods"]["items"][0]["status"]["containerStatuses"][0]
            status[field] = False if field == "ready" else 1
            with self.subTest(field=field):
                result = target.evaluate(state)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn("P4_PODS_UNREADY", result["blockers"])

    def test_wrong_host_context_server_fail(self):
        for field, value, problem in (
            ("hostname", "rclab-HP-406-G1-MT", "WRONG_HOST"),
            ("context", "other@cluster", "WRONG_CONTEXT"),
            ("api_server", "https://example.test:6443", "WRONG_API_SERVER")
        ):
            state = snapshot()
            state[field] = value
            with self.subTest(field=field):
                result = target.evaluate(state)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn(problem, result["blockers"])
                self.assertFalse(result["deployment_authorized"])

    def test_helm_revision_decimal_strings_are_accepted(self):
        state = snapshot()
        for release in state["helm"]:
            release["revision"] = str(release["revision"])
        result = target.evaluate(state)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["helm_revisions"], {"p4kdu-cu": 6, "p4kdu-du": 8})
        self.assertFalse(result["deployment_authorized"])

    def test_helm_revision_noncanonical_or_wrong_type_rejected(self):
        for value in ("06", "6.0", 6.0, True, None, 7):
            state = snapshot()
            state["helm"][0]["revision"] = value
            with self.subTest(revision=value):
                result = target.evaluate(state)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn("HELM_BASELINE_MISMATCH", result["blockers"])
                self.assertFalse(result["deployment_authorized"])

    def test_revision_drift_blocks(self):
        state = snapshot()
        state["helm"][0]["revision"] += 1
        result = target.evaluate(state)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HELM_BASELINE_MISMATCH", result["blockers"])

    def test_extra_deployment_blocks(self):
        state = snapshot()
        state["deployments"]["items"].append({"metadata": {"name": "unreviewed"}})
        self.assertIn("P4_DEPLOYMENT_SET_MISMATCH", target.evaluate(state)["blockers"])

    def test_extra_pod_blocks(self):
        state = snapshot()
        state["pods"]["items"].append(pod("unreviewed-pod"))
        self.assertIn("P4_PODS_UNREADY", target.evaluate(state)["blockers"])

    def test_osm_pod_failure_blocks(self):
        state = snapshot()
        state["osm_pods"]["items"][1]["status"]["containerStatuses"][0]["ready"] = False
        self.assertIn("OSM_PODS_UNREADY", target.evaluate(state)["blockers"])

    def test_osm_missing_nbi_blocks(self):
        state = snapshot()
        state["osm_pods"]["items"] = [
            p for p in state["osm_pods"]["items"] if not p["metadata"]["name"].startswith("nbi-")
        ]
        self.assertIn("OSM_CONTROL_COMPONENT_MISSING", target.evaluate(state)["blockers"])

    def test_wrong_service_port_blocks(self):
        state = snapshot()
        state["osm_services"]["items"][0]["spec"]["ports"][0]["port"] = 9000
        self.assertIn("OSM_SERVICE_MISMATCH", target.evaluate(state)["blockers"])

    def test_missing_container_status_blocks(self):
        state = snapshot()
        state["pods"]["items"][0]["status"]["containerStatuses"] = []
        self.assertIn("P4_PODS_UNREADY", target.evaluate(state)["blockers"])

    def test_readonly_collector_refuses_wrong_machine_without_running_commands(self):
        called = []
        def forbidden(argv):
            called.append(argv)
            raise AssertionError("no command may run on wrong host")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(target.ObservationError, "WRONG_HOST"):
                target.collect(hostname=lambda: "rclab-HP-406-G1-MT", run=forbidden)
        self.assertFalse(called)

    def test_readonly_collector_refuses_wrong_context_before_get(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return "production@cluster"
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(target.ObservationError, "WRONG_CONTEXT"):
                target.collect(hostname=lambda: target.EXPECTED_HOST, run=runner)
        self.assertEqual(calls, [["kubectl", "config", "current-context"]])

    def test_readonly_collector_refuses_wrong_server_before_get(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return target.EXPECTED_CONTEXT if len(calls) == 1 else "https://192.168.1.1:6443"
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(target.ObservationError, "WRONG_API_SERVER"):
                target.collect(hostname=lambda: target.EXPECTED_HOST, run=runner)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any("get" in call for call in calls))

    def test_helm_override_fails_before_get(self):
        def forbidden(argv):
            raise AssertionError("commands must not run")
        with mock.patch.dict(os.environ, {"HELM_KUBEAPISERVER": "https://other.test"}, clear=True):
            with self.assertRaisesRegex(target.ObservationError, "HELM_OVERRIDE_PRESENT"):
                target.collect(hostname=lambda: target.EXPECTED_HOST, run=forbidden)

    def test_readonly_collector_exact_commands_and_result(self):
        fixture = snapshot()
        calls = []
        responses = [
            target.EXPECTED_CONTEXT, target.EXPECTED_SERVER,
            fixture["namespace"], fixture["deployments"], fixture["pods"],
            fixture["helm"], fixture["osm_pods"], fixture["osm_services"]
        ]
        def runner(argv):
            calls.append(argv)
            result = responses[len(calls) - 1]
            return result if isinstance(result, str) else json.dumps(result)
        with mock.patch.dict(os.environ, {}, clear=True):
            observed = target.collect(hostname=lambda: target.EXPECTED_HOST, run=runner)
        self.assertEqual(target.evaluate(observed)["status"], "OBSERVED")
        self.assertEqual(len(calls), 8)
        self.assertFalse(any(any(x in argv for x in ("apply", "delete", "patch", "upgrade", "install",
                                                   "rollout", "create", "secret", "exec")) for argv in calls))
        self.assertEqual(calls[2][:3], ["kubectl", "--context", target.EXPECTED_CONTEXT])
        self.assertEqual(calls[5][:3], ["helm", "--kube-context", target.EXPECTED_CONTEXT])

    def test_offline_snapshot_needs_no_network(self):
        state = snapshot()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            path.write_text(json.dumps(state))
            with mock.patch.object(target, "collect", side_effect=AssertionError("must stay offline")):
                self.assertEqual(target.main(["--snapshot", str(path)]), 0)

    def test_bad_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            path.write_text('{"hostname":"Precision-3680","hostname":"Precision-3680"}')
            self.assertEqual(target.main(["--snapshot", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
