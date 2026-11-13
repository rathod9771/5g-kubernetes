"""Adversarial offline staging preflight tests; no Kubernetes API access."""
import copy
import importlib.util
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hcran_inventory", ROOT / "scripts/hcran_staging_inventory.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture():
    sha = "a" * 64
    roles = gate.EXPECTED
    deployments = []
    pods = []
    services = []
    for role in roles:
        deployments.append({
            "metadata": {"name": role},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [{
                "name": "cu" if role.endswith("-cu") else "du",
                "image": "registry.invalid/hcran@sha256:" + sha,
                "securityContext": {
                    "privileged": False,
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
            }]}}},
            "status": {"readyReplicas": 1, "availableReplicas": 1},
        })
        pods.append({
            "metadata": {"name": role + "-hash", "labels": {"app": role}},
            "spec": {"nodeName": "staging-node"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{
                    "name": "cu" if role.endswith("-cu") else "du",
                    "ready": True, "restartCount": 0,
                }],
            },
        })
        ports = [{"name":"f1", "port": 38472, "protocol":"SCTP"},
                 {"name":"f1u", "port": 38472, "protocol":"UDP"}]
        if role.endswith("-cu"):
            ports += [{"name":"ngap", "port":38412, "protocol":"SCTP"},
                      {"name":"ngu", "port":2152, "protocol":"UDP"}]
        services.append({"metadata":{"name":role}, "spec":{"ports":ports}})
    return {
        "pods": {"items": pods},
        "deployments": {"items": deployments},
        "services": {"items": services},
        "cu_config": {"data": {"gnb.conf":
           'security = { ciphering_algorithms = ( "nea2" ); '
           'integrity_algorithms = ( "nia2" ); };'}},
    }, {"nodes": [{
        "node": "staging-node", "cpu_some_avg10": 2.0,
        "io_some_avg10": 1.0, "load_per_cpu": 0.5,
    }]}


def evaluate(snapshot, psi):
    return gate.assess(snapshot["pods"], snapshot["deployments"],
                       snapshot["services"], snapshot["cu_config"], psi)


class StagingInventoryTests(unittest.TestCase):
    def test_synthetic_healthy_inventory_never_authorizes_production(self):
        snap, psi = fixture()
        report = evaluate(snap, psi)
        self.assertEqual(report["status"], "READY_FOR_ISOLATED_STAGING_TEST_ONLY")
        self.assertFalse(report["production_authorized"])
        self.assertEqual(report["production_decision"], "NO_GO")
        self.assertEqual(len(report["blockers"]), 0)
        self.assertNotIn("gnb.conf", str(report))
        self.assertNotIn("a" * 64, str(report))

    def test_fail_closed_common_failure_modes(self):
        changes = {
            "missing_deployment": lambda s, p: s["deployments"]["items"].pop(),
            "unavailable": lambda s, p: s["deployments"]["items"][0]["status"].update(availableReplicas=0),
            "mutable_image": lambda s, p: s["deployments"]["items"][0]["spec"]["template"]["spec"]["containers"][0].update(image="repo:latest"),
            "privileged": lambda s, p: s["deployments"]["items"][0]["spec"]["template"]["spec"]["containers"][0]["securityContext"].update(privileged=True),
            "privilege_escalation": lambda s, p: s["deployments"]["items"][0]["spec"]["template"]["spec"]["containers"][0]["securityContext"].update(allowPrivilegeEscalation=True),
            "missing_seccomp": lambda s, p: s["deployments"]["items"][0]["spec"]["template"]["spec"]["containers"][0]["securityContext"].pop("seccompProfile"),
            "missing_caps_drop": lambda s, p: s["deployments"]["items"][0]["spec"]["template"]["spec"]["containers"][0]["securityContext"]["capabilities"].update(drop=[]),
            "missing_pod": lambda s, p: s["pods"]["items"].pop(),
            "unready_pod": lambda s, p: s["pods"]["items"][0]["status"].update(phase="Pending"),
            "pod_restarts": lambda s, p: s["pods"]["items"][0]["status"]["containerStatuses"][0].update(restartCount=1),
            "missing_f1": lambda s, p: s["services"]["items"][1]["spec"].update(ports=[]),
            "e2_service": lambda s, p: s["services"]["items"][0]["spec"]["ports"].append({"port": 36421, "protocol":"SCTP"}),
            "e2_agent": lambda s, p: s["cu_config"]["data"].update({"gnb.conf": "e2_agent = {}"}),
            "null_cipher": lambda s, p: s["cu_config"]["data"]["gnb.conf"].replace('"nea2"', '"nea0"'),
            "high_io_pressure": lambda s, p: p["nodes"][0].update(io_some_avg10=30.0),
            "no_node_observation": lambda s, p: p.update(nodes=[]),
            "unexpected_node": lambda s, p: p["nodes"][0].update(node="other-node"),
        }
        for label, mutate in changes.items():
            with self.subTest(label=label):
                snapshot, psi = fixture()
                if label == "null_cipher":
                    snapshot["cu_config"]["data"]["gnb.conf"] = snapshot["cu_config"]["data"]["gnb.conf"].replace('"nea2"', '"nea0"')
                else:
                    mutate(snapshot, psi)
                result = evaluate(snapshot, psi)
                self.assertEqual(result["status"], "BLOCKED", label)
                self.assertFalse(result["production_authorized"], label)
                self.assertTrue(result["blockers"], label)

    def test_pressure_missing_never_silently_accepted(self):
        snapshot, psi = fixture()
        report = evaluate(snapshot, None)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any("pressure" in message for message in report["blockers"]))

    def test_kubectl_live_method_uses_only_get(self):
        response = mock.Mock(returncode=0, stdout='{"items":[]}', stderr="")
        with mock.patch.object(gate.subprocess, "run", return_value=response) as run:
            result = gate.kubectl_get("explicit-staging", "test-ns", "pods", "-l", "ran-type=hcran")
        self.assertEqual(result["items"], [])
        args = run.call_args.args[0]
        self.assertEqual(args[0], "kubectl")
        self.assertEqual(args[5], "get")
        self.assertNotIn("apply", args)
        self.assertNotIn("exec", args)
        self.assertNotIn("delete", args)


if __name__ == "__main__":
    unittest.main()
