"""Stage5Y offline contracts. Synthetic fixtures never count as RFsim acceptance."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
STREAM=ROOT/"experiments/oai-ngap-2026w13/stage5s/amf_ingress_stream_stage5y.py"
VERIFIER=ROOT/"experiments/oai-ngap-2026w13/stage5y/verify_live_amf_ingress.py"
OBSERVER=ROOT/"experiments/oai-ngap-2026w13/stage5s/run_protocol_trace.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"

def load(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class Stage5YContracts(unittest.TestCase):
    def test_open5gs_multiline_cell_ids_without_subscriber_contents(self):
        m=load(STREAM,"stage5y_stream")
        records=[
            "11/17 12:02:48: INFO: InitialUEMessage (ngap-handler.c:437)",
            "some normal unrelated line without subscriber data",
            "11/17 12:02:48: INFO: RAN_UE_NGAP_ID[23] CellID[0xe0101]",
            "11/17 12:02:49: INFO: InitialUEMessage (ngap-handler.c:437)",
            "11/17 12:02:49: INFO: RAN_UE_NGAP_ID[51] CellID[0xe0102]",
        ]
        self.assertEqual(m.find_initial_ue_cells(records),[(0,0xe0101),(3,0xe0102)])

    def test_do_not_join_ambiguous_or_unrelated_cells(self):
        m=load(STREAM,"stage5y_ambiguous")
        lines=["InitialUEMessage","RAN_UE_NGAP_ID[1] CellID[0xe0101]",
               "RAN_UE_NGAP_ID[2] CellID[0xe0102]",
               "InitialUEMessage","no matching tagged cell"]
        self.assertEqual(m.find_initial_ue_cells(lines),[])
        self.assertEqual(m.find_initial_ue_cells(
            ["InitialUEMessage","InitialUEMessage",
             "RAN_UE_NGAP_ID[2] CellID[0xe0102]"]),[(1,0xe0102)])

    def test_source_bounds_enforced(self):
        m=load(STREAM,"stage5y_bounds")
        sample=m.TOKEN+"\t/tmp/stage4k-log/amf-a.log\t2\n"
        sample+="InitialUEMessage\nRAN_UE_NGAP_ID[1] CellID[0xe0101]\n"
        z=m.decode_bounded_snapshot(sample)
        self.assertEqual(z[0][1],2)
        self.assertEqual(m.find_initial_ue_cells(z[0][2]),[(0,0xe0101)])
        with self.assertRaises(ValueError):
            m.decode_bounded_snapshot("X"*(m.MAX_SNAPSHOT_CHARS+1))
        with self.assertRaises(ValueError):
            m.decode_bounded_snapshot(m.TOKEN+"\t/etc/passwd\t1\nxyz\n")

    def test_core_observer_polls_real_amf_files_redacted(self):
        src=STREAM.read_text()
        obs=OBSERVER.read_text()
        for required in (
            "MAX_SNAPSHOT_CHARS = 2 * 1024 * 1024",
            "MAX_LINES_PER_FILE = 600",
            "POLL_SECONDS = 1.5",
            'tail -n 600 "$f"',
            '"amf_initial_ue_cell"',
            '"cell_id": cell_id',
            '"core-amf-files"',
            "SourceTailGap",
        ):
            self.assertIn(required,src)
        self.assertIn("observe_amf_files",obs)
        self.assertIn("target=observe_amf_files",obs)
        self.assertNotIn('metadata["imsi"]',src)
        self.assertNotIn('metadata["supi"]',src)

    def test_live_amf_evidence_gate_fail_closed_and_negative_controls_unchanged(self):
        v=load(VERIFIER,"stage5y_accept")
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self.assertFalse(v.check(root)[0])
            rows=[]
            for cycle in range(1,4):
                roles={}
                for role in ("ue","ue-small"):
                    roles[role]={
                        "ue_service_request_markers":2,
                        "ue_fresh_pdu_accept_markers":1,
                        "cu_rrc_setup_complete_cell_markers":1,
                        "amf_initial_ue_cell_markers":1,
                        "ue_bwp_deferral_markers":0,
                        "ue_nas_retries_exhausted":0,
                        "gap_classification":"FRESH_PDU_OBSERVED",
                    }
                rows.append({"cycle":cycle,"genuine_recovery_status":"PASS",
                             "amf_restart_observed":True,
                             "new_ngsetup_response_observed":True,
                             "ue":roles})
            def write(name,obj):
                (root/name).write_text(json.dumps(obj))
            write("stage5x-f1c-amf-ingress-gap.json",{
                "schema":"hcran-stage5x-cu-f1c-amf-ingress-v1",
                "status":"DIAGNOSTIC_ONLY","trace_complete":True,
                "live_cu_amf_streams_present":True,
                "production_authorized":False,"production_decision":"NO_GO",
                "cycles":rows
            })
            write("stage5p-multicycle-summary.json",{
                "status":"PASS","requested_cycles":3,
                "production_authorized":False,"cycles":[{}, {}, {}]
            })
            write("stage5r-protocol-timeline.json",{
                "collection_issues":[],"collection_limit_reached":False,
                "wire_header_sampling":{"required_ue_events_truncated":False},
                "category_counts":{"core":{"amf_initial_ue_cell":6}}
            })
            write("stage5u-negative-control-results.json",{
                "status":"PASS",
                "negative_controls":{f"case-{i}":{"rejected":True} for i in range(9)}
            })
            ok,errors=v.check(root)
            self.assertTrue(ok,errors)
            rows[1]["ue"]["ue-small"]["amf_initial_ue_cell_markers"]=0
            write("stage5x-f1c-amf-ingress-gap.json",{
                "schema":"hcran-stage5x-cu-f1c-amf-ingress-v1",
                "status":"DIAGNOSTIC_ONLY","trace_complete":True,
                "live_cu_amf_streams_present":True,
                "production_authorized":False,"production_decision":"NO_GO",
                "cycles":rows
            })
            self.assertFalse(v.check(root)[0])

    def test_2x3_pinned_research_workflow(self):
        w=WORKFLOW.read_text()
        for s in ("runs-on: ubuntu-24.04","trial: [1, 2]",
                  "hcran_build_pinned_w45_ue_stage5t.sh",
                  "hcran_build_pinned_stage3_ngap_cu.sh",
                  "--cycles 3 --verify-gtpu",
                  "stage5u/audit_repeatability.py",
                  "stage5x/analyze_f1c_amf_ingress.py",
                  "stage5y/verify_live_amf_ingress.py"):
            self.assertIn(s,w)
        for s in ("runs-on: self-hosted","kubectl apply","helm upgrade","osm ns-"):
            self.assertNotIn(s,w)

if __name__=="__main__":
    unittest.main()
