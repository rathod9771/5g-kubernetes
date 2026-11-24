"""H6 actual-source binary NAS authentication pilot negative-control contracts."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SRC=Path(__file__).resolve().parents[1]/"scripts/experiments"
sys.path.insert(0,str(SRC))
spec=importlib.util.spec_from_file_location("hcransrs_h6_real_nas_auth",
                                           SRC/"hcransrs_h6_real_nas_auth.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class H6AuthContracts(unittest.TestCase):
    def test_isolation_gate_has_explicit_marker(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(m.h1.EvidenceError):m.check_runner()
        env={"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted"}
        with patch.dict(os.environ,env,clear=True):
            with self.assertRaises(m.h1.EvidenceError):m.check_runner()
        env["HCRANSRS_H6_ISOLATED_AUTH"]="SYNTHETIC_ONE_UE_NO_DEPLOY"
        with patch.dict(os.environ,env,clear=True):m.check_runner()
    def test_authentication_qualification_fails_closed_without_h4_and_backend(self):
        evidence={"h4_candidate_qualification":"ORDERED_F1AP_RRC_AND_5GS_NAS_REGISTRATION_REQUEST_CANDIDATE_ONLY",
                  "initial_nas_wire":{"registration_request_ngap_frame_numbers":[21]}}
        wire={"independent_nas_decoder":"NGAP_NAS_PDU_FIRST_THREE_OCTETS_ONLY",
              "status":"AUTH_REQUEST_AND_RESPONSE_ON_REAL_NGAP_WIRE",
              "authentication_request_frames":[30],
              "authentication_response_frames":[35]}
        self.assertEqual(m.qualify(evidence,wire,True),
                         "REAL_NAS_AUTH_REQUEST_AND_RESPONSE_WIRE_CANDIDATE_ONLY")
        self.assertEqual(m.qualify(evidence,wire,False),"BLOCKED_ISOLATED_AUTH_BACKEND")
        self.assertEqual(m.qualify({},wire,True),"BLOCKED_H4_INITIAL_REGISTRATION")
        wire["authentication_response_frames"]=[]
        self.assertEqual(m.qualify(evidence,wire,True),"BLOCKED_AUTH_NAS_FRAME_ORDER")
    def test_authenticator_rejects_out_of_order_response(self):
        h4={"h4_candidate_qualification":"ORDERED_F1AP_RRC_AND_5GS_NAS_REGISTRATION_REQUEST_CANDIDATE_ONLY",
            "initial_nas_wire":{"registration_request_ngap_frame_numbers":[33]}}
        nas={"independent_nas_decoder":"NGAP_NAS_PDU_FIRST_THREE_OCTETS_ONLY",
             "status":"AUTH_REQUEST_AND_RESPONSE_ON_REAL_NGAP_WIRE",
             "authentication_request_frames":[30],
             "authentication_response_frames":[39]}
        self.assertEqual(m.qualify(h4,nas,True),"BLOCKED_AUTH_NAS_FRAME_ORDER")
    def test_real_wire_scrubs_nas_payload_and_rejects_one_way(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"wire.pcap"
            path.write_bytes(b"irrelevant data"+"x".encode()*240)
            commands=[]
            def fake(*args,**kwargs):
                commands.append(" ".join(map(str,args)))
                if "DownlinkNASTransport" in args[5]:
                    return "15\t7e0056" + "ab"*19 + "\n"
                if "UplinkNASTransport" in args[5]:
                    return "20\t7e0057" + "cd"*14 + "\n"
                return ""
            result=m.nas_control_events(path,exec_fn=fake)
            self.assertEqual(result["authentication_request_frames"],[15])
            self.assertEqual(result["authentication_response_frames"],[20])
            self.assertNotIn("ab"*19,str(result))
            self.assertEqual(result["status"],"AUTH_REQUEST_AND_RESPONSE_ON_REAL_NGAP_WIRE")
            self.assertEqual(len(commands),2)
    def test_missing_capture_never_claims_authentication(self):
        with tempfile.TemporaryDirectory() as td:
            result=m.nas_control_events(Path(td)/"nonexistent.pcap")
        self.assertEqual(result["status"],"NO_BOUNDED_REAL_SCTP_CAPTURE")
        self.assertEqual(result["authentication_response_frames"],[])
    def test_h6_trial_monkeypatch_methods_are_callable_and_restored(self):
        with tempfile.TemporaryDirectory() as td:
            bridge=m.CoreBridge(td)
            original_launch=m.h1.Trial.launch
            original_close=m.h1.Trial.close
            with patch.object(bridge,"launch",return_value=None) as launcher, \
                 patch.object(bridge,"close",return_value=None) as closer:
                try:
                    bridge.install()
                    dummy=object()
                    m.h1.Trial.launch(dummy,"nrf","image","bin","/tmp/config")
                    m.h1.Trial.close(dummy)
                    self.assertEqual(launcher.call_args.args[0],dummy)
                    self.assertEqual(closer.call_args.args[0],dummy)
                finally:
                    bridge.restore()
            self.assertIs(m.h1.Trial.launch,original_launch)
            self.assertIs(m.h1.Trial.close,original_close)
            bridge.credentials.clear()
    def test_uicc_credentials_match_ephemeral_subscriber_and_not_canonical(self):
        with tempfile.TemporaryDirectory() as td:
            bridge=m.CoreBridge(td)
            args=bridge.ue_command("/tmp/nr-uesoftmodem")
            self.assertIn("--uicc0.key",args)
            self.assertIn("--uicc0.opc",args)
            self.assertEqual(args[args.index("--uicc0.key")+1],bridge.credentials["security"]["k"])
            self.assertEqual(args[args.index("--uicc0.opc")+1],bridge.credentials["security"]["opc"])
            self.assertEqual(bridge.credentials["imsi"],m.h5.IMSI)
            self.assertNotIn("kubectl"," ".join(args))
            self.assertIn("NAS_REGISTRATION_ACCEPT",m.NOT_VALIDATED)
            bridge.credentials.clear()

if __name__=="__main__":
    unittest.main()
