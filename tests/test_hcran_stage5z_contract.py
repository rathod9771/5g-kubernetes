"""Stage5Z synthetic observer contracts. Never accepted as live RFsim proof."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

def load(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class Stage5Z(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cu=load(ROOT/'experiments/oai-ngap-2026w13/stage5s/cu_amf_selection_stage5z.py','stage5zcu')
        cls.an=load(ROOT/'experiments/oai-ngap-2026w13/stage5z/analyze_amf_selection_stage5z.py','stage5zan')

    def test_cu_adjacent_selection_warning_and_success(self):
        c=self.cu.CUSelectionObserver()
        example='[NR_RRC] [UL] (cellID 3804001, UE ID 4 RNTI test) Received RRCSetupComplete (RRC_CONNECTED reached)'
        self.assertEqual(c.process(example),[])
        c.process('5g_s_TMSI: masked')
        c.process('Selected PLMN without stored identity')
        self.assertEqual(c.process('No matching AMF found for PLMN (MCC=208 MNC=93) and AMF SetID=1'),
            [('cu_amf_selection_no_match',{'cell_id':3804001,'correlation':'adjacent_cu_log_only'})])
        self.assertEqual(c.process('No AMF is associated to the gNB'),[('cu_amf_no_association',{})])
        c.process(example.replace('3804001','3804002'))
        c.process('5g_s_TMSI: masked');c.process('Selected PLMN')
        self.assertEqual(c.process("UE 1: Selected AMF 'synthetic' (assoc_id 11)"),
            [('cu_amf_selection_selected',{'cell_id':3804002,'correlation':'adjacent_cu_log_only'})])

    def test_gap_or_other_cell_never_inherits_stale_context(self):
        c=self.cu.CUSelectionObserver()
        c.process('[UL] (cellID 3804001, UE ID 3 RNTI xxx) Received RRCSetupComplete')
        for _ in range(6): c.process('unrelated text')
        self.assertEqual(c.process('No matching AMF found for PLMN'), [('cu_amf_selection_no_match',{})])
        c.process('[UL] (cellID 999999, UE ID 2 RNTI xxx) Received RRCSetupComplete')
        self.assertEqual(c.process('No matching AMF found for PLMN'), [('cu_amf_selection_no_match',{})])

    def test_phase_partition_keeps_failure_as_failure(self):
        ev=[]
        def add(t,source,event,**meta): ev.append({'elapsed_seconds':t,'source':source,'event':event,**meta})
        def mark(t,stage,status='PASS',**kw):add(t,'harness','milestone',stage=stage,status=status,**kw)
        mark(1,'STAGE5P-CYCLE-START','OBSERVED',cycle=1)
        mark(2,'T31-AMF-actually-stopped')
        add(3,'cu','cu_rrc_setup_complete',cell_id=3804001)
        add(4,'cu','cu_amf_selection_no_match',cell_id=3804001)
        add(4.5,'cu','cu_amf_no_association')
        mark(5,'T33-AMF-process-restarted')
        mark(6,'T34-new-NGSetupResponse-after-AMF-restart')
        add(7,'ue','nas_service_request')
        add(8,'cu','cu_rrc_setup_complete',cell_id=3804002)
        add(9,'cu','cu_amf_selection_selected',cell_id=3804002)
        add(10,'core','amf_initial_ue_cell',cell_id=0xe0102)
        mark(12,'STAGE5P-CYCLE','FAIL',cycle=1)
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            (p/'stage5r-protocol-timeline.json').write_text(json.dumps({
                'schema':'hcran-stage5r-bounded-protocol-observation-v1','events':ev,
                'collection_issues':[],'collection_limit_reached':False}))
            result=self.an.build_report(p)
            self.assertEqual(result['status'],'DIAGNOSTIC_ONLY')
            self.assertEqual(result['cycles'][0]['genuine_recovery_status'],'FAIL')
            phases=result['cycles'][0]['phase_observations']
            self.assertEqual(phases['amf_unavailable_or_reconnecting']['cells']['macro']['cu_cell_markers']['cu_amf_selection_no_match'],1)
            self.assertEqual(phases['after_ngsetup']['cells']['small']['amf_cell_markers']['amf_initial_ue_cell'],1)
            self.assertEqual(phases['after_ngsetup']['cells']['macro']['amf_cell_markers']['amf_initial_ue_cell'],0)
            self.assertEqual(result['production_decision'],'NO_GO')
            self.assertNotIn('RNTI', json.dumps(result))

    def test_incomplete_evidence_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.an.build_report(t)
            self.assertEqual(r['status'],'INCOMPLETE_OBSERVATION')
            self.assertEqual(r['production_authorized'],False)

if __name__=='__main__':unittest.main()
