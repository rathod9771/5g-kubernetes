#!/usr/bin/env python3
"""Offline synthetic tests of Stage 4V contract; no RC control is exercised."""
import copy
import datetime as dt
import importlib.util
import pathlib
import unittest

FILE = pathlib.Path(__file__).with_name("rc_effect_contract.py")
SPEC = importlib.util.spec_from_file_location("rc_effect_contract", FILE)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture():
    """Fabricated values intentionally used only to test the validator."""
    return {
        "schema_version": "stage4v-rc-effect-v1",
        "scope": "isolated-github-hosted-disposable-runner",
        "source": {"run_id": 1, "head_sha": "a" * 40, "du_count": 2, "ue_count": 2},
        "control": {
            "service_model": "E2SM-RC", "procedure": "RICcontrol",
            "ran_function_id": 3, "style_id": 1, "action_id": 1,
            "request_id": "1:100", "ack_request_id": "1:100",
            "request_wire_frame": 10, "ack_wire_frame": 12,
            "outcome": "RICcontrolAcknowledge", "decoded_from_wire": True,
        },
        "reversal": {
            "request_id": "1:101", "ack_request_id": "1:101",
            "request_wire_frame": 30, "ack_wire_frame": 32,
            "outcome": "RICcontrolAcknowledge", "decoded_from_wire": True,
        },
        "effect": {
            "metric_name": "synthetic-unitless-target-state",
            "unit": "synthetic", "expected_direction": "increase",
            "baseline": 2.0, "after_control": 7.0, "after_reversal": 2.05,
            "min_effect": 3.0, "max_restore_error": 0.1,
            "baseline_samples": 5, "after_samples": 5, "restore_samples": 5,
            "observer_kind": "independent_ran_readback",
            "independent_observer": True,
            "baseline_utc": "2026-11-13T10:00:00Z",
            "control_utc": "2026-11-13T10:00:05Z",
            "readback_utc": "2026-11-13T10:00:10Z",
            "reversal_utc": "2026-11-13T10:00:15Z",
            "restored_utc": "2026-11-13T10:00:20Z",
        },
        "captures": {
            "e2ap_pcap_sha256": "a" * 64,
            "external_observer_sha256": "b" * 64,
            "dual_ue_traffic_sha256": "c" * 64,
        },
        "continuity": {
            "both_f1c_connected": True,
            "both_pdu_sessions_after": True,
            "post_control_traffic": True,
            "post_reversal_traffic": True,
        },
        "safety": {
            "disposable_subscribers_only": True,
            "rollback_executed": True,
            "rollback_on_failure": True,
            "live_hp_cluster_access": False,
            "production_deployment": False,
        },
        "production_decision": "NO_GO",
    }


def change(data, dotted, value):
    root = data
    parts = dotted.split(".")
    for key in parts[:-1]:
        root = root[key]
    root[parts[-1]] = value


class RCProofContractTests(unittest.TestCase):
    def test_form_validity_never_promotes_production_or_claims_real_control(self):
        result = gate.report(fixture())
        self.assertEqual(result["result"], "STRUCTURE_VALID_ONLY")
        self.assertFalse(result["real_rc_effect_verified"])
        self.assertFalse(result["production_deployment_authorized"])
        self.assertEqual(result["production_decision"], "NO_GO")

    def test_false_positive_attempts_rejected(self):
        scenarios = [
            ("monitoring_is_not_control", "control.procedure", "RICindication"),
            ("kpm_is_not_rc", "control.service_model", "E2SM-KPM"),
            ("subscription_is_not_ack", "control.outcome", "subscription_success"),
            ("no_wire_decoding", "control.decoded_from_wire", False),
            ("wrong_request_correlation", "control.ack_request_id", "some-other-request"),
            ("no_reversal_ack", "reversal.outcome", "RICcontrolFailure"),
            ("no_rollback", "safety.rollback_executed", False),
            ("no_rollback_policy", "safety.rollback_on_failure", False),
            ("not_independent", "effect.independent_observer", False),
            ("xapp_log_not_independent", "effect.observer_kind", "xapp_log"),
            ("no_effect", "effect.after_control", 2.0),
            ("wrong_direction", "effect.after_control", -2.0),
            ("not_reverted", "effect.after_reversal", 5.0),
            ("bad_wire_order", "reversal.request_wire_frame", 11),
            ("reused_id", "reversal.request_id", "1:100"),
            ("no_post_traffic", "continuity.post_control_traffic", False),
            ("one_ue", "source.ue_count", 1),
            ("missing_pdu", "continuity.both_pdu_sessions_after", False),
            ("live_cluster_access", "safety.live_hp_cluster_access", True),
            ("production_mutation", "safety.production_deployment", True),
            ("expired_or_fake_hash", "captures.e2ap_pcap_sha256", "not-a-sha"),
            ("bad_source_sha", "source.head_sha", "abc"),
            ("missing_timestamp_zone", "effect.control_utc", "2026-11-13T10:00:05"),
            ("out_of_order_times", "effect.readback_utc", "2026-11-13T09:59:00Z"),
            ("not_no_go", "production_decision", "GO"),
        ]
        for name, key, value in scenarios:
            with self.subTest(name=name):
                doc = fixture()
                change(doc, key, value)
                output = gate.report(doc)
                self.assertEqual(output["result"], "REJECTED")
                self.assertTrue(output["issues"])
                self.assertEqual(output["production_decision"], "NO_GO")
                self.assertFalse(output["real_rc_effect_verified"])

    def test_missing_content_is_rejected(self):
        doc = fixture()
        del doc["control"]["ack_wire_frame"]
        self.assertEqual(gate.report(doc)["result"], "REJECTED")
        self.assertEqual(gate.report(None)["result"], "REJECTED")

    def test_numeric_guards_reject_boolean_nan_and_infinity(self):
        for key, value in [
            ("effect.after_control", float("nan")),
            ("effect.after_control", float("inf")),
            ("effect.min_effect", 0),
            ("source.ue_count", True),
            ("control.action_id", -1),
        ]:
            with self.subTest(key=key, value=value):
                doc = fixture()
                change(doc, key, value)
                self.assertEqual(gate.report(doc)["result"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
