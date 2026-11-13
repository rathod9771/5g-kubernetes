"""Adversarial Stage4Z 3GPP F1 handover evidence tests; synthetic fixtures only."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "f1contract", ROOT/"experiments/oai-ngap-2026w13/stage4z/evidence_contract.py")
f1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f1)


def ok():
    packets={"f1u_ul":14,"f1u_dl":12,"ngu_ul":28,"ngu_dl":24}
    def hop(src,dst,pci,key):
        return {"from":src,"to":dst,"source_pci":pci,
                "pre_pdu_ip":"10.45.0.2","post_pdu_ip":"10.45.0.2",
                "f1ap_context_update_confirmed":True,
                "source_context_release_confirmed":True,
                "rrc_reconfiguration_confirmed":True,
                "icmp_sent":6,"icmp_received":6,
                "packet_counts":copy.deepcopy(packets),
                "post_ho_pcap_sha256":key*64}
    return {"schema":"hcran-stage4z-real-f1-handover-v1",
            "source":"real-isolated-docker-rfsim-pcap",
            "rc_ric_required":False,
            "production_authorized":False,
            "production_decision":"NO_GO",
            "initial_pdu_ip":"10.45.0.2",
            "baseline":{"serving_du":"macro","packet_counts":copy.deepcopy(packets)},
            "transitions":[hop("macro","small",101,"a"),hop("small","macro",102,"b")]}


class F1HandoverEvidence(unittest.TestCase):
    def test_positive_fixture_only_checks_structure_not_3gpp_conformance(self):
        valid,reasons=f1.check(ok())
        self.assertTrue(valid,reasons)
        self.assertEqual(reasons,[])
        self.assertEqual(ok()["production_decision"],"NO_GO")

    def test_reject_false_mobility_proofs(self):
        mutations={
           "source_is_fixture": lambda d:d.update(source="mocked-fixture"),
           "ric_enabled": lambda d:d.update(rc_ric_required=True),
           "production_go": lambda d:d.update(production_authorized=True,production_decision="GO"),
           "no_baseline": lambda d:d.pop("baseline"),
           "no_baseline_gtpu":lambda d:d["baseline"]["packet_counts"].update(f1u_ul=0),
           "only_one_direction":lambda d:d["transitions"].pop(),
           "wrong_target":lambda d:d["transitions"][0].update(to="macro"),
           "same_source":lambda d:d["transitions"][1].update(**{"from":"macro"}),
           "wrong_pci":lambda d:d["transitions"][0].update(source_pci=102),
           "pdu_ip_changed":lambda d:d["transitions"][0].update(post_pdu_ip="10.45.0.3"),
           "no_target_f1u":lambda d:d["transitions"][0]["packet_counts"].update(f1u_dl=0),
           "no_target_ngu":lambda d:d["transitions"][1]["packet_counts"].update(ngu_ul=0),
           "request_not_ack":lambda d:d["transitions"][0].update(f1ap_context_update_confirmed=False),
           "handover_ack_no_source_release":lambda d:d["transitions"][1].update(source_context_release_confirmed=False),
           "no_ue_rrc_complete":lambda d:d["transitions"][0].update(rrc_reconfiguration_confirmed=False),
           "traffic_loss":lambda d:d["transitions"][0].update(icmp_received=4),
           "no_pcap":lambda d:d["transitions"][1].update(post_ho_pcap_sha256=""),
           "zero_ip":lambda d:d.update(initial_pdu_ip="0.0.0.0"),
           "tampered_schema":lambda d:d.update(schema="v0"),
           "false_count":lambda d:d["transitions"][0]["packet_counts"].update(ngu_dl=True),
        }
        for label,mutate in mutations.items():
            with self.subTest(label=label):
                d=ok()
                mutate(d)
                valid,reasons=f1.check(d)
                self.assertFalse(valid,label)
                self.assertTrue(reasons,label)

    def test_non_json_values_blocked(self):
        for val in [None, [], 44, "pass"]:
            with self.subTest(val=val):
                valid,reasons=f1.check(val)
                self.assertFalse(valid)
                self.assertTrue(reasons)


if __name__=="__main__":
    unittest.main()
