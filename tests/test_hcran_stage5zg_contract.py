"""Stage5ZG pinned RRC SIB scheduling handoff — synthetic fail-closed contracts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"experiments/oai-ngap-2026w13"
PATCH=BASE/"stage5zg/patch_rrc_mac_si_handoff_observe.py"
CLASS=BASE/"stage5s/rrc_mac_handoff_stage5zg.py"
ANALYZE=BASE/"stage5zg/analyze_rrc_mac_sib_handoff.py"
WORKFLOW=ROOT/".github/workflows/hcran-stage5y-amf-ingress-observer.yml"
BUILDER=ROOT/"scripts/experiments/hcran_build_pinned_w45_ue_stage5zg.sh"

def mod(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class Stage5ZGContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=mod(PATCH,"stage5zg_patch")
        cls.c=mod(CLASS,"stage5zg_classify")
        cls.a=mod(ANALYZE,"stage5zg_analyze")

    def test_source_pinned_log_only_two_callsites_fail_closed(self):
        p=self.p
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            r=root/p.RRC
            l=root/p.L2
            r.parent.mkdir(parents=True,exist_ok=True)
            r.write_text('get_sib = check_si_status(SI_info);\n'
                         'SI_info->sib_pending = true;\n'
                         'Stage5ZF: si_decision\n' + p.RRC_OLD)
            l.write_text('void process_msg_rcc_to_mac() {\n'+p.L2_OLD+'}\n')
            p.patch(root)
            self.assertEqual(r.read_text().count('Stage5ZG: sib_schedule_sent'),1)
            self.assertEqual(l.read_text().count('Stage5ZG: sib_schedule_dispatched'),1)
            self.assertIn('nr_rrc_send_msg_to_mac(rrc, &sib_msg);',r.read_text())
            self.assertIn('nr_rrc_mac_sched_sib(instance_id, msg->payload.sched_sib.get_sib);',l.read_text())
            with self.assertRaises(RuntimeError):
                p.patch(root)
            r.write_text('drift')
            with self.assertRaises(RuntimeError):
                p.patch(root)

    def test_allowlisted_categories_discard_identity_and_invalid_values(self):
        c=self.c
        self.assertEqual(c.classify('ue','subscriber_secret Stage5ZG: sib_schedule_sent get_sib=1 sib_pending=1'),
                         (('ue_rrc_sib_request_sent',{'get_sib':1,'sib_pending':1}),))
        self.assertEqual(c.classify('ue-small','Stage5ZG: sib_schedule_dispatched get_sib=1'),
                         (('ue_mac_sib_request_dispatched',{'get_sib':1}),))
        self.assertEqual(c.classify('cu','Stage5ZG: sib_schedule_dispatched get_sib=1'),())
        self.assertEqual(c.classify('ue','Stage5ZG: sib_schedule_sent get_sib=999 sib_pending=1'),())

    def test_missing_mac_dispatch_is_not_proof_of_loss_and_never_passes(self):
        a=self.a
        def ev(t,s,k,**kw):
            return dict(elapsed_seconds=t,source=s,event=k,**kw)
        def m(t,stage,status,**kw):
            return ev(t,'harness','milestone',stage=stage,status=status,**kw)
        events=[
          m(1,'STAGE5P-CYCLE-START','OBSERVED',cycle=1),
          m(2,'T34-new-NGSetupResponse-after-AMF-restart','PASS'),
          ev(3,'ue-small','ue_rrc_si_schedule_decision',get_sib=1),
          ev(4,'ue-small','ue_rrc_sib_request_sent',get_sib=1,sib_pending=1),
          ev(5,'ue-small','ue_mac_mib_sampled',ul_bwp=0,dl_bwp=0),
          ev(6,'ue-small','ue_ra_deferred_until_sib1'),
          ev(7,'ue','nas_pdu_accept'),
          m(8,'STAGE5P-CYCLE','FAIL',cycle=1),
        ]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            def save(rows,more=None):
                d={'schema':'hcran-stage5r-bounded-protocol-observation-v1',
                   'stage5zg_rrc_mac_sib_message_categories':True,
                   'collection_limit_reached':False,'collection_issues':[],
                   'events':rows}
                d.update(more or {})
                (p/'stage5r-protocol-timeline.json').write_text(json.dumps(d))
            save(events)
            out=a.build(p)
            self.assertEqual(out['status'],'DIAGNOSTIC_ONLY')
            self.assertEqual(out['cycles'][0]['genuine_recovery_status'],'FAIL')
            self.assertFalse(out['production_authorized'])
            self.assertEqual(out['cycles'][0]['cells']['small']['diagnostic_boundary'],
                'RRC_SIB_SENT_NO_LATER_MAC_DISPATCH_MARKER_WITH_BWP_GAP')
            self.assertEqual(out['cycles'][0]['cells']['macro']['diagnostic_boundary'],
                             'FRESH_PDU_OBSERVED')
            self.assertNotIn('subscriber_secret',json.dumps(out))
            save(events,{'collection_limit_reached':True})
            self.assertEqual(a.build(p)['status'],'INCOMPLETE_OBSERVATION')

    def test_recovered_MAC_dispatch_does_not_trigger_unmatched_alert(self):
        a=self.a
        def e(t,s,k,**kw):return dict(elapsed_seconds=t,source=s,event=k,**kw)
        events=[
          e(1,'harness','milestone',stage='STAGE5P-CYCLE-START',cycle=1,status='OBSERVED'),
          e(2,'harness','milestone',stage='T34-new-NGSetupResponse-after-AMF-restart',status='PASS'),
          e(3,'ue-small','ue_rrc_sib_request_sent',get_sib=1),
          e(4,'ue-small','ue_mac_sib_request_dispatched',get_sib=1),
          e(5,'ue-small','ue_sib1_scheduled'),
          e(6,'ue-small','nas_pdu_accept'),
          e(7,'harness','milestone',stage='STAGE5P-CYCLE',cycle=1,status='PASS'),
        ]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'stage5r-protocol-timeline.json').write_text(json.dumps({
                'schema':'hcran-stage5r-bounded-protocol-observation-v1',
                'stage5zg_rrc_mac_sib_message_categories':True,
                'collection_limit_reached':False,'collection_issues':[],
                'events':events}))
            out=a.build(p)
            cell=out['cycles'][0]['cells']['small']
            self.assertEqual(cell['unmatched_rrc_send_marker_count'],0)
            self.assertEqual(cell['unmatched_mac_dispatch_marker_count'],0)
            self.assertEqual(cell['diagnostic_boundary'],'FRESH_PDU_OBSERVED')

    def test_research_builder_and_real_gates_preserved(self):
        src=BUILDER.read_text()
        self.assertIn('dfb296ab867050a7bde81b393fa31b757ada2c83',src)
        self.assertIn('stage5zf/patch_rrc_si_decision_observe.py',src)
        self.assertIn('stage5zg/patch_rrc_mac_si_handoff_observe.py',src)
        self.assertIn('openair2/RRC/NR_UE/L2_interface_ue.c',src)
        for x in ('--noavx512 --noavx2', 'git -C "$OAI_SOURCE" diff --check'):
            self.assertIn(x,src)
        w=WORKFLOW.read_text()
        for item in ('experiment/hcran-stage5zg-sib1-message-handoff-20261119',
                    'hcran_build_pinned_w45_ue_stage5t.sh',
                    'hcran_build_pinned_w45_ue_stage5zd.sh',
                    'hcran_build_pinned_w45_ue_stage5zg.sh',
                    '--cycles 3 --verify-gtpu',
                    'stage5u/audit_repeatability.py',
                    'stage5y/verify_live_amf_ingress.py',
                    'stage5zg/analyze_rrc_mac_sib_handoff.py'):
            self.assertIn(item,w)
        self.assertNotIn('runs-on: self-hosted',w)

if __name__=='__main__': unittest.main()
