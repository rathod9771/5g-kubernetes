"""Adversarial structural Stage5H evidence checks; fixture is NOT live proof."""
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location(
 "stage5h_contract",
 ROOT/"experiments/oai-ngap-2026w13/stage5h/evidence_contract.py")
m=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fixture(path):
    folder=Path(path)
    trace=b"isolated synthetic PCAP PLACEHOLDER" * 10
    (folder/"after-cause9-re-registration-gtpu.pcap").write_bytes(trace)
    payload={
        "schema":"stage5h-real-cause9-one-UE-F1-server",
        "source":"real-disposable-docker-RFSIM",
        "mode":"on-demand-Re16-24.501-cause9-SUCI-initial-registration",
        "original_ue_process_unchanged":True,
        "manual_f1_handover_after_fault":False,
        "native_spontaneous_recovery_proven":False,
        "recovery_timing_slo_proven":False,
        "production_authorized":False,
        "production_decision":"NO_GO",
        "pre_fault_pdu_ip":"10.45.0.2",
        "post_fault_pdu_ip":"10.45.0.4",
        "actual_postfault_gtpu":{"f1u_ul":12,"f1u_dl":12,"ngu_ul":12,"ngu_dl":12},
        "post_fault_pcap_sha256":hashlib.sha256(trace).hexdigest(),
        "nas_event_chain":list(m.FLOW),
    }
    (folder/"stage5h-cause9-runtime.json").write_text(json.dumps(payload))
    (folder/"results.json").write_text(json.dumps(
        [{"stage":x,"status":"PASS"} for x in m.EVENTS]))
    (folder/"cleanup-ue.log").write_text("\n".join(m.FLOW)+"\n")
    (folder/"stage5h-postfault-protocol-trace.json").write_text(json.dumps({
      "schema":"hcran-stage5h-protocol-trace-v1",
      "scope":"postfault only, selected NAS/RRC/NGAP markers",
      "production_decision":"NO_GO",
      "ue":{"offset_chars":900,"total_filtered":len(m.FLOW),"lines":list(m.FLOW)},
      "cu":{"offset_chars":600,"total_filtered":1,
            "lines":["Received NGSetupResponse from AMF"]},
    }))

    return payload


class Contract(unittest.TestCase):
    def test_fixture_is_structural_only(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d)
            ok,reasons=m.check(d)
            self.assertTrue(ok,reasons)
            self.assertFalse(json.loads((Path(d)/"stage5h-cause9-runtime.json").read_text())["production_authorized"])

    def test_tampering_cannot_pass(self):
        alterations={
          "reused_old_pcap":lambda d,x:(d/"after-cause9-re-registration-gtpu.pcap").write_bytes(b"old evidence"*60),
          "corrupt_digest":lambda d,x:x.update(post_fault_pcap_sha256="0"*64),
          "low_uplink":lambda d,x:x["actual_postfault_gtpu"].update(f1u_ul=0),
          "missing_downlink":lambda d,x:x["actual_postfault_gtpu"].pop("ngu_dl"),
          "ue_restarted":lambda d,x:x.update(original_ue_process_unchanged=False),
          "manual_ho_after_fault":lambda d,x:x.update(manual_f1_handover_after_fault=True),
          "production_go":lambda d,x:x.update(production_authorized=True,production_decision="GO"),
          "false_slo":lambda d,x:x.update(recovery_timing_slo_proven=True),
          "wrong_schema":lambda d,x:x.update(schema="v0"),
          "pdu_ip_invalid":lambda d,x:x.update(post_fault_pdu_ip="0.0.0.0"),
          "out_of_order_nas":lambda d,x:(d/"stage5h-postfault-protocol-trace.json").write_text(json.dumps({"schema":"hcran-stage5h-protocol-trace-v1","ue":{"offset_chars":900,"lines":list(reversed(m.FLOW))}})),
          "missing_postfault_trace":lambda d,x:(d/"stage5h-postfault-protocol-trace.json").unlink(),
          "no_postfault_offset":lambda d,x:(d/"stage5h-postfault-protocol-trace.json").write_text(json.dumps({"schema":"hcran-stage5h-protocol-trace-v1","ue":{"offset_chars":0,"lines":list(m.FLOW)}})),
          "stale_runtime_chain":lambda d,x:x.update(nas_event_chain=list(reversed(m.FLOW))),
          "missing_event":lambda d,x:(d/"results.json").write_text("[]"),
          "missing_pcap":lambda d,x:(d/"after-cause9-re-registration-gtpu.pcap").unlink(),
        }
        for label,action in alterations.items():
            with self.subTest(label=label),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                payload=fixture(root)
                action(root,payload)
                (root/"stage5h-cause9-runtime.json").write_text(json.dumps(payload))
                ok,reasons=m.check(root)
                self.assertFalse(ok,label)
                self.assertTrue(reasons,label)


if __name__=="__main__":
    unittest.main()
