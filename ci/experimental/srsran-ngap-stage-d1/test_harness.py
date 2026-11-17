"""Synthetic/offline preparation tests only. Never execute native code or network probes."""
import unittest,copy,json,time,hashlib,xml.etree.ElementTree as ET,ast
from pathlib import Path
from unittest.mock import patch
from jsonschema import Draft202012Validator
import yaml
import preflight,protocol_evidence as pe,runtime_launcher as launch,assess
P=Path(__file__).parent
class Tests(unittest.TestCase):
 def good(self):
  return dict.fromkeys(['github_hosted','fresh_netns','loopback_only','loopback_up','loopback_addresses_bound','no_external_routes','capture_available','capture_capability','sources_pinned','tools_available','resource_limits','config_verified','sctp_socket'],True)|{'cpu_count':2,'memory_available':2*1024**3,'memory_physical_available':3*1024**3,'disk_available':1024**3}
 def test_runtime_admission_positive_mock(self):self.assertEqual(preflight.admit(self.good())['status'],'PASS')
 def test_runtime_admission_negative_each_gate(self):
  for key,value in self.good().items():
   with self.subTest(key=key):
    p=self.good();p[key]=False if isinstance(value,bool) else 0
    with self.assertRaises(preflight.Blocked):preflight.admit(p)
 def test_incomplete_preflight(self):
  p=self.good();del p['sctp_socket']
  with self.assertRaises(preflight.Blocked):preflight.admit(p)
 def test_runtime_collection_not_executed(self):
  with patch('preflight.os.stat') as stat:
   stat.return_value.st_ino=123
   with self.assertRaises(preflight.Blocked):preflight.collect(123,True,True,True)
 def approval(self):
  return {'scope':'D1_ISOLATED_NGAP_RUNTIME','nonce':'00000000-0000-4000-8000-000000000001','approved_by':'UNIT_TEST_NOT_APPROVAL','created':1000,'expires':1900,'package_sha256':'a'*64,'srsran_revision':launch.SRS,'open5gs_revision':launch.OGS,'test_id':'D0-T2','seconds':20,'binaries':dict.fromkeys(['cu','amf','nrf'],{}),'source_manifest_sha256':'b'*64}
 def test_approval_pure_valid_fixture(self):launch.approval(self.approval(),1100,'a'*64)
 def test_approval_expired(self):
  with self.assertRaises(launch.Blocked):launch.approval(self.approval(),1900,'a'*64)
 def test_approval_insufficient_lifetime(self):
  with self.assertRaises(launch.Blocked):launch.approval(self.approval(),1850,'a'*64)
 def test_approval_source_mismatch(self):
  a=self.approval();a['open5gs_revision']='0'*40
  with self.assertRaises(launch.Blocked):launch.approval(a,1100,'a'*64)
 def test_approval_changed_package(self):
  with self.assertRaises(launch.Blocked):launch.approval(self.approval(),1100,'c'*64)
 def test_stale_race_cannot_be_authorized(self):
  a=self.approval();a['test_id']='D0-T6'
  with self.assertRaises(launch.Blocked):launch.approval(a,1100,'a'*64)
 def test_lifecycle_not_implicitly_authorized(self):
  for k in ['D0-T4','D0-T5']:
   a=self.approval();a['test_id']=k
   with self.assertRaises(launch.Blocked):launch.approval(a,1100,'a'*64)
 def test_cli_disarmed(self):
  with patch('sys.argv',['runtime_launcher.py']):
   with self.assertRaises(launch.Blocked):launch.main()
 def test_capture_exact_scope(self):
  c=pe.capture_command('/tmp/NOT_EXECUTED','runner');self.assertIn('lo',c);self.assertIn('-c',c);self.assertIn('2048',c);self.assertEqual(c[-1],'sctp and port 38412 and host 127.0.0.10 and host 127.0.0.20')
 def packet(self,kind='Response'):
  root=ET.Element('pdml');packet=ET.SubElement(root,'packet');f={'frame.len':('100',None),'frame.cap_len':('100',None),'frame.number':('1',None),'frame.time_epoch':('2.000001',None),'ip.src':('127.0.0.20',None),'ip.dst':('127.0.0.10',None),'sctp.srcport':('38412',None),'sctp.dstport':('41000',None),'sctp.assoc_index':('1',None),'sctp.chunk_type':('0',None),'ngap.procedureCode':('21',None),'ngap.NGSetup'+kind+'_element':('',None),'ngap.AMFName':('l3-d0-amf-01',None),'ngap.PLMNIdentity':('00101','00f110')}
  for name,(show,value) in f.items():
   a={'name':name,'show':show}
   if value:a['value']=value
   ET.SubElement(packet,'field',a)
  return root
 def wire(self,p):return ET.tostring(p)
 def test_independent_decoded_success(self):self.assertEqual(pe.project_pdml(self.wire(self.packet()))[0]['event'],'NG_SETUP_RESPONSE')
 def test_wrong_amf_name_rejected(self):
  p=self.packet();next(x for x in p.iter('field') if x.get('name')=='ngap.AMFName').set('show','WRONG')
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_wrong_plmn_rejected(self):
  p=self.packet();next(x for x in p.iter('field') if x.get('name')=='ngap.PLMNIdentity').set('value','00f120')
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_unexpected_peer_rejected(self):
  p=self.packet();next(x for x in p.iter('field') if x.get('name')=='ip.src').set('show','192.0.2.1')
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_other_ngap_procedure_rejected(self):
  p=self.packet();next(x for x in p.iter('field') if x.get('name')=='ngap.procedureCode').set('show','15')
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_transport_cannot_become_ng_setup(self):
  p=self.packet();packet=p.find('packet')
  for f in list(packet):
   if f.get('name','').startswith('ngap.'):packet.remove(f)
  next(x for x in packet if x.get('name')=='sctp.chunk_type').set('show','1')
  self.assertEqual(pe.project_pdml(self.wire(p))[0]['event'],'SCTP_TRANSPORT_ONLY')
 def test_undecoded_data_rejected(self):
  p=self.packet();packet=p.find('packet')
  for f in list(packet):
   if f.get('name','').startswith('ngap.'):packet.remove(f)
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_pdml_entities_rejected(self):
  with self.assertRaises(ValueError):pe.project_pdml(b'<!DOCTYPE x><pdml/>')
 def test_field_inventory_missing_fails(self):
  with self.assertRaises(ValueError):pe.available_fields('F\tFRAME\tframe.number\n')
 def test_log_projection_does_not_retain_text(self):
  x=pe.project_log('amf','SUPI=DO_NOT_RETAIN AMF initialize...done',1,1);self.assertEqual(set(x[0]),{'source','marker','wall_ns','monotonic_ns'});self.assertNotIn('SUPI',json.dumps(x))
 def test_raw_logs_not_exposed_on_unknown_line(self):self.assertEqual(pe.project_log('cu','arbitrary sensitive data',1,1),[])
 def test_synthetic_not_runtime_pass(self):self.assertEqual(assess.verdict('D0-T2',[],[],[],synthetic=True)[0],'NOT_ASSESSED')
 def setup_records(self):
  samples=[{'phase':'OBSERVE','valid':True,'complete':True,'application_state':'UP','observed_amf_name':'l3-d0-amf-01','producer_wall_ns':3,'observer_monotonic_ns':4,'producer_monotonic_ns':3,'process_incarnation':'a'*32,'generation':1,'sequence':1,'amf_index':0,'peer_id':'l3-d0-amf-01@n2-loopback','local_nf_id':'l3-d0-cucp-01'}]
  packets=[{'event':'NG_SETUP_REQUEST','association':'1','wall_ns':1,'src_port':41000},{'event':'NG_SETUP_RESPONSE','association':'1','wall_ns':2,'dst_port':41000,'amf_name':'l3-d0-amf-01'}]
  markers=[{'source':'amf','marker':'AMF_INITIALIZED'},{'source':'cu','marker':'NG_SETUP_ACCEPTED'}]
  return samples,packets,markers
 def test_setup_requires_wire_and_logs(self):
  s,p,m=self.setup_records();self.assertEqual(assess.check_setup(s,p,m),('PASS',None));self.assertEqual(assess.check_setup(s,[],m)[0],'INCONCLUSIVE');self.assertEqual(assess.check_setup(s,p,[])[0],'INCONCLUSIVE')
 def test_setup_stale_sample_rejected(self):
  s,p,m=self.setup_records();s[0]['observer_monotonic_ns']=30_000_000_004;self.assertEqual(assess.check_setup(s,p,m)[0],'INCONCLUSIVE')
 def test_association_mismatch_rejected(self):
  s,p,m=self.setup_records();p[0]['association']='2';self.assertEqual(assess.check_setup(s,p,m)[0],'INCONCLUSIVE')
 def test_later_disconnect_blocks_up(self):
  s,p,m=self.setup_records();p.append({'event':'SCTP_DISCONNECT','association':'1','wall_ns':3});self.assertEqual(assess.check_setup(s,p,m)[0],'INCONCLUSIVE')
 def test_later_planned_shutdown_does_not_invalidate_prior_up(self):
  s,p,m=self.setup_records();p.append({'event':'SCTP_DISCONNECT','association':'1','wall_ns':5});self.assertEqual(assess.check_setup(s,p,m),('PASS',None))
 def test_rejection_never_up(self):
  s,_,_=self.setup_records();self.assertEqual(assess.verdict('D0-T3',s,[],[],False,{'status':'DRIVER_COMPLETED_NOT_PROTOCOL_PASS','test_id':'D0-T3','timing':{'uncertainty_ns':1000}})[0],'FAIL')
 def test_shutdown_strict(self):
  s,p,m=self.setup_records();s[0]['phase']='AFTER_STOP';self.assertEqual(assess.verdict('D0-T7',s,p,m,False,{'status':'DRIVER_COMPLETED_NOT_PROTOCOL_PASS','test_id':'D0-T7','timing':{'uncertainty_ns':1000}})[0],'FAIL')
 def test_schema_synthetic_blocks_pass(self):
  schema=json.loads((P/'event.schema.json').read_text());Draft202012Validator.check_schema(schema);self.assertTrue(any("synthetic" in json.dumps(x) for x in schema['allOf']))
 def test_configs_match_and_rejection_deliberate(self):
  amf=yaml.safe_load((P/'amf.yaml').read_text())['amf'];nrf=yaml.safe_load((P/'nrf.yaml').read_text())['nrf'];rej=yaml.safe_load((P/'amf-reject.yaml').read_text())['amf']
  self.assertEqual(amf['amf_name'],'l3-d0-amf-01');self.assertEqual(amf['tai'][0]['tac'],7);self.assertEqual(rej['tai'][0]['tac'],8);self.assertEqual(amf['guami'][0]['plmn_id'],nrf['serving'][0]['plmn_id']);self.assertNotIn('db_uri',amf)
 def test_native_real_transport_and_default_off_preserved(self):
  src=(P/'runtime_driver.cpp').read_text();self.assertIn('create_n2_connection_client',src);self.assertIn('cfg.ngap.no_core=false',src);self.assertNotIn('ngap_snapshot::source::create',src)
  self.assertIn('ngap_snapshot::config{}',(P/'activation.patch').read_text())
 def test_no_clock_test_macro_in_native_sources(self):
  for p in [*P.glob('*.cpp'),*P.glob('*.h'),P/'CMakeLists.txt']:self.assertNotIn('SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK',p.read_text())
 def test_build_workflow_never_runs_runtime(self):
  w=yaml.load((P/'native-build.WORKFLOW.PROPOSED.yml').read_text(),Loader=yaml.BaseLoader);self.assertEqual(set(w['on']),{'workflow_dispatch'});self.assertIn('refs/heads/feature/layer3-srsran-ngap-native-build-d2b',w['jobs']['build-only']['if']);self.assertIn("github.event_name == 'workflow_dispatch'",w['jobs']['build-only']['if'])
  text=(P/'native-build.WORKFLOW.PROPOSED.yml').read_text();self.assertNotIn('runtime_launcher',text);self.assertIn('github-build-only',text);self.assertIn('contents: read',text)
 def producer_fixture(self):
  return {'phase':'PRE_START','valid':True,'complete':True,'application_state':'UNKNOWN','reason':'no_evidence','generation':0,'sequence':1,'revision':0,'producer_wall_ns':1,'producer_monotonic_ns':1,'observer_wall_ns':2,'observer_monotonic_ns':2,'process_incarnation':'a'*32,'local_nf_id':'l3-d0-cucp-01','peer_id':'l3-d0-amf-01@n2-loopback','amf_index':0,'expected_amf_name':'l3-d0-amf-01','observed_amf_name':'','transition_known':False,'transition_wall_ns':None,'transition_monotonic_ns':None}
 def context_fixture(self):
  prov={k:'0'*64 for k in ['instrumentation_patch_sha256','experiment_patch_sha256','cu_binary_sha256','amf_binary_sha256','config_sha256']};prov.update(srsran_revision=launch.SRS,amf_source_revision=launch.OGS,network_namespace_inode='UNIT_TEST',boot_id='UNIT_TEST')
  return {'synthetic':True,'experiment_id':'00000000-0000-4000-8000-000000000001','test_id':'D0-T1','provenance':prov,'timing_uncertainty_ns':None,'uncertainty_reason':'UNIT_TEST_ONLY','expected_states':['UNKNOWN']}
 def test_producer_normalizes_to_reviewed_schema(self):
  event=assess.normalize(self.producer_fixture(),self.context_fixture());self.assertEqual(event['classification'],'NOT_ASSESSED');self.assertTrue(event['synthetic'])
 def test_invalid_producer_never_normalizes_up(self):
  sample=self.producer_fixture();sample.update(valid=False,complete=False,application_state='UP');self.assertEqual(assess.normalize(sample,self.context_fixture())['actual_state'],'UNKNOWN')
 def test_private_file_symlink_rejected(self):
  import tempfile
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);(base/'f').write_text('{}');(base/'link').symlink_to(base/'f')
   with self.assertRaises(OSError):launch.private_file(base/'link',__import__('os').getuid())
 def test_source_manifest_exact_upstreams(self):
  m=json.loads((P/'source-manifest.json').read_text());self.assertEqual(m['open5gs_revision'],launch.OGS);self.assertEqual(m['srsran_revision'],launch.SRS);self.assertEqual(len(m['stage_a_files']),10)
 def test_missing_clock_evidence_blocks_classification(self):
  s,p,m=self.setup_records();self.assertEqual(assess.verdict('D0-T2',s,p,m,False)[0],'INCONCLUSIVE')
 def test_clock_step_rejected(self):
  anchors=[{'wall_before_ns':100,'wall_after_ns':101,'monotonic_ns':10},{'wall_before_ns':200000000,'wall_after_ns':200000001,'monotonic_ns':20}];self.assertIsNone(assess.clock_uncertainty([],anchors))
 def test_clock_consistent_mock(self):
  anchors=[{'wall_before_ns':100,'wall_after_ns':101,'monotonic_ns':10},{'wall_before_ns':110,'wall_after_ns':111,'monotonic_ns':20}];self.assertEqual(assess.clock_uncertainty([],anchors),1001)
 def test_ambiguous_reconnection_not_first_setup_pass(self):
  s,p,m=self.setup_records();p.append(dict(p[1],association='2'));self.assertEqual(assess.check_setup(s,p,m)[0],'INCONCLUSIVE')
 def test_snapshot_wrong_peer_not_up_proof(self):
  s,p,m=self.setup_records();s[0]['peer_id']='WRONG';self.assertEqual(assess.check_setup(s,p,m)[0],'INCONCLUSIVE')
 def test_timing_uncertainty_admission(self):
  s,p,m=self.setup_records();self.assertEqual(assess.check_setup(s,p,m,2)[0],'INCONCLUSIVE')
 def test_privileged_imports_follow_integrity_gate(self):
  tree=ast.parse((P/'runtime_launcher.py').read_text());self.assertFalse(any(isinstance(x,ast.ImportFrom) and x.module in ('preflight','protocol_evidence','assess') for x in tree.body))
  src=(P/'runtime_launcher.py').read_text();run=src[src.index('def run('):];self.assertLess(run.index('verify_inputs('),run.index('from protocol_evidence import'))
 def test_truncated_capture_rejected(self):
  p=self.packet();next(x for x in p.iter('field') if x.get('name')=='frame.cap_len').set('show','99')
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_missing_procedure_rejected(self):
  p=self.packet();packet=p.find('packet');packet.remove(next(x for x in packet if x.get('name')=='ngap.procedureCode'))
  with self.assertRaises(ValueError):pe.project_pdml(self.wire(p))
 def test_cgroup_headroom_prevents_false_resource_pass(self):
  p=self.good();p['memory_available']=512*1024**2
  with self.assertRaises(preflight.Blocked):preflight.admit(p)
 def test_invalid_unknown_is_not_rejection_pass(self):
  s,p,m=self.setup_records();s[0].update(valid=False,complete=False,application_state='UNKNOWN');p[1]['event']='NG_SETUP_FAILURE'
  execution={'status':'DRIVER_COMPLETED_NOT_PROTOCOL_PASS','test_id':'D0-T3','timing':{'uncertainty_ns':0}}
  self.assertEqual(assess.verdict('D0-T3',s,p,m,False,execution)[0],'INCONCLUSIVE')
 def test_shutdown_requires_previously_valid_retained_ticket(self):
  s,p,m=self.setup_records();s[0].update(phase='AFTER_STOP',valid=False,complete=False,application_state='UNKNOWN')
  execution={'status':'DRIVER_COMPLETED_NOT_PROTOCOL_PASS','test_id':'D0-T7','timing':{'uncertainty_ns':0}}
  self.assertEqual(assess.verdict('D0-T7',s,p,m,False,execution)[0],'INCONCLUSIVE')
 def test_all_python_syntax(self):
  for p in P.glob('*.py'):ast.parse(p.read_text())
if __name__=='__main__':unittest.main(verbosity=2)
