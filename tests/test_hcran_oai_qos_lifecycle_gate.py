"""Synthetic negative/positive contract fixtures only; NOT genuine protocol evidence."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"scripts/experiments/hcran_oai_qos_lifecycle_gate.py"
spec=importlib.util.spec_from_file_location("hcran_oai_qos_lifecycle_gate",P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def receipt():
    def ue(cell,du,base):
        events={k:{"frame":base+n+1,"decoded":True} for n,k in enumerate(m.REQUIRED_PROCEDURES)}
        traffic={leg:{"packets":12,"teid":1000+base+n}
                 for n,leg in enumerate(("f1u_ul","f1u_dl","ngu_ul","ngu_dl"))}
        return {"cell":cell,"du_id":du,"pdu_ipv4_before":f"10.45.0.{du-98}",
                "pdu_ipv4_after":f"10.45.0.{du-98}",
                "events":events,"traffic":traffic,
                "qos":{"five_qi":9,"qfi":1,"drb_id":1,"mapping_decoded":True},
                "traffic_after_release":0}
    return {"schema":"hcran-oai-qos-lifecycle-wire-v1",
            "source":{"architecture":"oai-hcran","experiment":"ISOLATED_GITHUB_RUNTIME",
                      "independent_capture":True,"run_id":123,
                      "pcap_sha256":"a"*64},
            "synthetic_fixture":False,"negative_controls_passed":True,
            "ues":[ue("macro",100,0),ue("small",101,50)]}

class LifecycleContract(unittest.TestCase):
    def test_structurally_complete_fixture_parses_but_is_not_a_live_run(self):
        result=m.verify(receipt())
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(result["production_decision"],"NO_GO")
    def test_synthetic_marked_receipt_rejected(self):
        r=receipt();r["synthetic_fixture"]=True
        with self.assertRaises(m.EvidenceError):m.verify(r)
    def test_missing_nas_qos_and_gtpu_rejected(self):
        for mutation in ("nas","qfi","gtpu","negative"):
            with self.subTest(mutation=mutation):
                r=receipt()
                if mutation=="nas":del r["ues"][0]["events"]["pdu_session_release_complete"]
                elif mutation=="qfi":r["ues"][1]["qos"]["mapping_decoded"]=False
                elif mutation=="gtpu":r["ues"][0]["traffic"]["f1u_ul"]["teid"]=0
                else:r["negative_controls_passed"]=False
                with self.assertRaises(m.EvidenceError):m.verify(r)
    def test_duplicate_or_wrong_du_fails(self):
        for new_id in (100,777):
            r=receipt();r["ues"][1]["du_id"]=new_id
            with self.assertRaises(m.EvidenceError):m.verify(r)
    def test_missing_provenance_or_wrong_order_rejected(self):
        r=receipt();r["source"]["independent_capture"]=False
        with self.assertRaises(m.EvidenceError):m.verify(r)
        r=receipt()
        r["ues"][0]["events"]["pdu_session_release_complete"]["frame"]=r["ues"][0]["events"]["pdu_session_release_command"]["frame"]-1
        with self.assertRaises(m.EvidenceError):m.verify(r)
    def test_cannot_claim_live_observation_through_unit_tests(self):
        # The assertions operate exclusively on manufactured in-process dictionaries.
        # No Docker, UE, RF, packet file, Kubernetes or AMF is started.
        self.assertIn("NO_GO",P.read_text())
if __name__=="__main__":unittest.main()
