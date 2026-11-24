"""Offline negative controls for H2 source-built OAI UE / srsRAN ZMQ research."""
import importlib.util
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/"scripts/experiments"
import sys
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location("hcransrs_real_ue_zmq_h2",BASE/"hcransrs_real_ue_zmq_h2.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class H2ZMQContracts(unittest.TestCase):
    def test_overlay_is_macro_only_and_does_not_change_other_config(self):
        cfg={"ru_sdr":{"device_args":"tx_port=tcp://127.0.0.1:2000,rx_port=tcp://127.0.0.1:2001"}}
        self.assertEqual(m.overlay_config(cfg,"macro")["ru_sdr"]["device_args"],
            "tx_port=tcp://0.0.0.0:2000,rx_port=tcp://172.29.78.1:2001")
        for role in ("cu","small"):
            other={"ru_sdr":{"device_args":"keep"}}
            self.assertIs(m.overlay_config(other,role),other)
            self.assertEqual(other["ru_sdr"]["device_args"],"keep")
    def test_unreviewed_macro_args_fail_closed(self):
        for s in ("tx_port=tcp://192.0.2.1:2000,rx_port=tcp://127.0.0.1:2001",""):
            with self.subTest(s=s),self.assertRaises(Exception):
                m.overlay_config({"ru_sdr":{"device_args":s}},"macro")
    def test_actual_oai_binary_command_uses_no_production_address(self):
        args=m.ue_command(Path("/tmp/pinned-oai-nrue"))
        joined=" ".join(args)
        self.assertEqual(args[0],"/tmp/pinned-oai-nrue")
        self.assertIn("--device.name oai_zmqdevif",joined)
        self.assertIn("tcp://172.29.78.30:2000",joined)
        self.assertIn("tcp://0.0.0.0:2001",joined)
        self.assertIn("208930100001100",joined)
        self.assertNotIn("kubectl",joined)
        self.assertNotIn("osm",joined)
        self.assertIn("NR_SIB1_DECODE",m.NOT_VALIDATED)
    def test_never_claims_protocol_from_large_tcp_payload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # No PCAP means no wire success, not a synthetic positive.
            evidence=m.wire_summary(Path(td)/"absent.pcap")
            self.assertEqual(evidence["wire_evidence"],"UNQUALIFIED_OR_ONE_WAY_ZMQ_TCP_PAYLOAD")
            self.assertEqual(evidence["actual_du_to_ue_large_tcp_payload_frames"],0)
            self.assertEqual(evidence["actual_ue_to_du_large_tcp_payload_frames"],0)
    def test_h3_rejects_transport_only_synthetic_success(self):
        r={"status":"REAL_OAI_UE_SRS_DU_ZMQ_TRANSPORT_CANDIDATE_ONLY"}
        self.assertEqual(m.assess_h3(r),"BLOCKED_NO_INDEPENDENT_CONTROL_WIRE")
    def test_h3_requires_independent_sib1_and_both_f1_rrc_directions(self):
        r={
            "status":"REAL_OAI_UE_SRS_DU_ZMQ_TRANSPORT_CANDIDATE_ONLY",
            "protocol_wire":{
                "evidence_kind":"INDEPENDENT_SCTP_PACKET_DECODING",
                "frame_counts":{
                    "macro_f1ap_ul_rrc_message_transfer":1,
                    "macro_f1ap_dl_rrc_message_transfer":1,
                },
            },
            "ue":{"bounded_synthetic_ue_log_markers":["SIB1_LOG_CANDIDATE"]},
        }
        self.assertEqual(m.assess_h3(r),
            "SIB1_LOG_AND_BIDIRECTIONAL_F1AP_RRC_TRANSFER_CANDIDATES")
        r["ue"]["bounded_synthetic_ue_log_markers"]=[]
        self.assertEqual(m.assess_h3(r),"BLOCKED_SIB1_LOG_EVIDENCE")
        r["ue"]["bounded_synthetic_ue_log_markers"]=["SIB1_LOG_CANDIDATE"]
        r["protocol_wire"]["frame_counts"]["macro_f1ap_ul_rrc_message_transfer"]=0
        self.assertEqual(m.assess_h3(r),"BLOCKED_F1AP_UL_RRC_EVIDENCE")
        r["protocol_wire"]["frame_counts"]["macro_f1ap_ul_rrc_message_transfer"]=1
        r["protocol_wire"]["frame_counts"]["macro_f1ap_dl_rrc_message_transfer"]=0
        self.assertEqual(m.assess_h3(r),"BLOCKED_F1AP_DL_RRC_EVIDENCE")
    def test_no_control_wire_capture_cannot_pass_protocol_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p=m.protocol_wire_summary(Path(td)/"missing.pcap")
            self.assertEqual(p["status"],"NO_CONTROL_WIRE_CAPTURE")
            self.assertEqual(p["protocol_validation"],"NOT_ESTABLISHED")
            self.assertEqual(sum(p["frame_counts"].values()),0)

    def test_h4_fails_closed_without_actual_nas_control_wire(self):
        receipt={"h3_candidate_qualification":"SIB1_LOG_AND_BIDIRECTIONAL_F1AP_RRC_TRANSFER_CANDIDATES"}
        self.assertEqual(m.assess_h4(receipt),"BLOCKED_NO_INDEPENDENT_NAS_DECODER")
        receipt["initial_nas_wire"]={"evidence_kind":"INDEPENDENT_NGAP_NAS_PDU_WIRE_DISSECTION"}
        self.assertEqual(m.assess_h4(receipt),"BLOCKED_F1AP_INITIAL_UL_RRC")
    def test_h4_requires_ordered_real_f1ap_and_nas_request(self):
        receipt={
            "h3_candidate_qualification":"SIB1_LOG_AND_BIDIRECTIONAL_F1AP_RRC_TRANSFER_CANDIDATES",
            "protocol_wire":{"first_frame_numbers":{
                "macro_f1ap_dl_rrc_message_transfer":12,
                "macro_f1ap_ul_rrc_message_transfer":14,
                "ngap_initial_ue_message":19}},
            "initial_nas_wire":{
                "evidence_kind":"INDEPENDENT_NGAP_NAS_PDU_WIRE_DISSECTION",
                "initial_ul_f1ap_frame_numbers":[10],
                "registration_request_ngap_frame_numbers":[19],
                "redacted_nas_header_match_count":1,
                "registration_accept_observed":False,
                "registration_complete_observed":False,
            }
        }
        self.assertEqual(m.assess_h4(receipt),
            "ORDERED_F1AP_RRC_AND_5GS_NAS_REGISTRATION_REQUEST_CANDIDATE_ONLY")
        receipt["initial_nas_wire"]["registration_request_ngap_frame_numbers"]=[]
        self.assertEqual(m.assess_h4(receipt),"BLOCKED_NAS_REGISTRATION_REQUEST_PDU")
        receipt["initial_nas_wire"]["registration_request_ngap_frame_numbers"]=[19]
        receipt["protocol_wire"]["first_frame_numbers"]["macro_f1ap_ul_rrc_message_transfer"]=11
        self.assertEqual(m.assess_h4(receipt),"BLOCKED_INCORRECT_INITIAL_RRC_NAS_FRAME_ORDER")
        receipt["protocol_wire"]["first_frame_numbers"]["macro_f1ap_ul_rrc_message_transfer"]=14
        receipt["initial_nas_wire"]["registration_accept_observed"]=True
        self.assertEqual(m.assess_h4(receipt),"BLOCKED_UNSUPPORTED_COMPLETED_NAS_CLAIM")
    def test_h4_never_saves_nas_payload_without_capture(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result=m.initial_nas_wire_summary(Path(td)/"no_capture.pcap")
            self.assertEqual(result["status"],"NO_5GS_REGISTRATION_REQUEST_WIRE")
            self.assertEqual(result["redacted_nas_header_match_count"],0)
            self.assertNotIn("nas_payload",result)
            self.assertFalse(result["registration_accept_observed"])

if __name__=="__main__":unittest.main()
