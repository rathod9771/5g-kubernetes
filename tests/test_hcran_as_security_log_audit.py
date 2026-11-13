"""Negative semantic tests for AS security log provenance audit."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("as_audit",ROOT/"scripts/hcran_as_security_log_audit.py")
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

CU="""[NR_RRC] Selected security algorithms: ciphering 2, integrity 2
[NR_RRC] UE 1 Generate SecurityModeCommand
[NR_RRC] Received Security Mode Complete
[NR_RRC] Received RRCReconfigurationComplete
"""
UE="""[NR_RRC] Received securityModeCommand
[NR_RRC] encoding securityModeComplete
[NAS] Received Registration Accept
[NAS] Received PDU Session Establishment Accept
"""

class ASSecurityProof(unittest.TestCase):
    def test_positive_static_fixture_never_authorizes_production(self):
        x=audit.inspect(CU,UE)
        self.assertTrue(x["as_security_mode_observed"])
        self.assertIs(x["production_authorized"],False)
        self.assertEqual(x["production_decision"],"NO_GO")
        self.assertIs(x["nas_ciphering_or_integrity_proven"],False)
        self.assertIs(x["onwire_ciphertext_proven"],False)

    def test_false_security_proofs_rejected(self):
        corrupt={
            "nea0":(CU.replace("ciphering 2","ciphering 0"),UE),
            "nia0":(CU.replace("integrity 2","integrity 0"),UE),
            "no_selection":(CU.replace("Selected security algorithms","not-selected"),UE),
            "no_cu_smc":(CU.replace("Generate SecurityModeCommand","No SMC"),UE),
            "no_cu_complete":(CU.replace("Received Security Mode Complete","no response"),UE),
            "no_ue_smc":(CU,UE.replace("Received securityModeCommand","No SMC")),
            "no_ue_complete":(CU,UE.replace("encoding securityModeComplete","no response")),
            "no_registration":(CU,UE.replace("Received Registration Accept","reject")),
            "no_pdu":(CU,UE.replace("Received PDU Session Establishment Accept","no PDU")),
            "bad_order":(CU.replace("Received Security Mode Complete","X").replace(
                "Generate SecurityModeCommand","Received Security Mode Complete").replace(
                "X","Generate SecurityModeCommand"), UE),
        }
        for label,(cu,ue) in corrupt.items():
            with self.subTest(case=label):
                x=audit.inspect(cu,ue)
                self.assertFalse(x["as_security_mode_observed"])
                self.assertEqual(x["production_decision"],"NO_GO")

if __name__=="__main__":
    unittest.main()
