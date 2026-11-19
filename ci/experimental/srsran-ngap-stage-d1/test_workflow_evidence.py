"""Synthetic evidence only; no pip, native configuration, downloads or production access."""
import copy, datetime, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import workflow_evidence as we
import finalize_build_evidence as fin
from build_policy import Blocked,digest
from offline_python import inventory,PIN

def auxiliary_fixture(root):
    root=Path(root);a=root/'auxiliary';a.mkdir()
    import shutil
    (root/'inputs').mkdir()
    for name in we.INPUT_FILES:shutil.copyfile(we.P/name,root/'inputs'/name)
    base=datetime.datetime(2026,11,16,tzinfo=datetime.timezone.utc)
    for n,phase in enumerate(we.AUX):
        folder='l3-wheel-acquisition' if n==0 else 'l3-python-setup';(a/folder).mkdir()
        start=(base+datetime.timedelta(seconds=n*2)).isoformat();end=(base+datetime.timedelta(seconds=n*2+1)).isoformat()
        c={'argv':we.aux_argv(phase,we.P,Path('/tmp/SYNTHETIC_RUNNER_TEMP')), 'started_at':start,'ended_at':end,'elapsed_seconds':1,'exit_code':0,'signal':None,'timeout':False,'forced':False,'log_budget_exceeded':False,'classification':'PASS'}
        (a/(phase+'.log')).write_text('SYNTHETIC_PUBLIC_DIAGNOSTIC\n');c.update(log=phase+'.log',log_sha256=digest(a/(phase+'.log')))
        data={'started_at':start,'ended_at':end,'elapsed_seconds':1,'status':we.expected_inner(phase)[1]}
        hashes={x['filename']:x['sha256'] for x in inventory()}
        if n==0:data.update(hashes=hashes,requests=['https://pypi.org/SYNTHETIC']*16,retries=0,redirects=0)
        else:
            import re
            data.update(wheel_hashes=hashes,installed={re.sub('[-_.]+','-',x['name'].lower()):x['version'] for x in inventory()[:-1]},installer_installed=False,commands=[])
            for i in range(5):
                log=a/folder/(str(i)+'.log');log.write_text('SYNTHETIC_SETUP_COMMAND\n');entry=dict(c,ended_at=start,elapsed_seconds=0,log=str(log),log_sha256=digest(log));data['commands'].append(entry)
        (a/we.expected_inner(phase)[0]).write_text(json.dumps(data))
        files={str(f.relative_to(a)):digest(f) for f in (a/folder).iterdir()};files[phase+'.log']=c['log_sha256']
        receipt={'schema':1,'phase':phase,'classification':'PASS','started_at':start,'ended_at':end,'elapsed_seconds':1,'command':c,'interpreter':{'version':[3,10,12],'implementation':'CPython'},'source_sha256':digest(we.P/('wheelhouse_acquire.py' if n==0 else 'offline_python.py')),'installer':PIN,'policy':we.POLICY,'resource_evidence':{'scope':we.POLICY['scope'],'native_cgroup_claimed':False,'deadline_enforced':True,'self_and_reaped_children':{'child_user_seconds':0.1,'child_system_seconds':0.1,'child_maxrss_kib':1024,'self_user_seconds':0.1,'self_system_seconds':0.1}},'artifacts':files}
        (a/(phase+'.receipt.json')).write_text(json.dumps(receipt))
    (root/'preservation.json').write_text(json.dumps({'classification':'PASS','errors':[]}))

def inspection_fixture(root):
    """Realistic names and receipt bindings; text is explicitly synthetic, not ELF output."""
    root=Path(root);w=root/'worker';inventory={};outputs={}
    binaries=['srs-build/l3-d1-driver/l3_ngap_protocol_runtime','srs-build/apps/cu_cp/srscucp','srs-build/tests/unittests/cu_cp/cu_cp_test','ogs-build/src/amf/open5gs-amfd','ogs-build/src/nrf/open5gs-nrfd','srs-build/lib/cu_cp/libsrsran_cu_cp.a']
    for i,binary in enumerate(binaries):
        row={'sha256':'a'*64,'inspection_artifacts':{}}
        for ext in we.INSPECTION_EXTENSIONS:
            name=str(i)+'-'+Path(binary).name+'.'+ext;f=w/name
            f.write_text('SYNTHETIC_NOT_NATIVE_'+ext+'\n');f.chmod(0o600);h=digest(f)
            row[ext+'_sha256']=h;row['inspection_artifacts'][ext]={'path':name,'sha256':h};outputs['/work/evidence/'+name]=h
        inventory[binary]=row
    (w/'binary-inventory.json').write_text(json.dumps(inventory))
    receipt=w/'binary_inspection.receipt.json';r=json.loads(receipt.read_text());r['outputs'].update(outputs);r['outputs']['/work/evidence/binary-inventory.json']=digest(w/'binary-inventory.json');receipt.write_text(json.dumps(r))

def toolchain_fixture(root):
    """Synthetic timing/receipt evidence; no query or toolchain scan."""
    root=Path(root);w=root/'worker'
    base={'argv':['SYNTHETIC_NOT_EXECUTED'],'started_at':'2026-11-16T12:00:00+00:00','ended_at':'2026-11-16T12:00:01+00:00','elapsed_seconds':1,'classification':'PASS','exit_code':0,'signal':None,'timeout':False,'forced':False,'log_budget_exceeded':False}
    source=json.loads((w/'dependency_acquisition.receipt.json').read_text())
    (w/'toolchain-lock.json').write_text(json.dumps({'schema':2,'policy_sha256':we.TOOLCHAIN_POLICY_SHA256,'approved_selection':True,'selected_packages':{'SYNTHETIC_NOT_REAL':'1'}}))
    source['outputs']['toolchain-lock.json']=digest(w/'toolchain-lock.json')
    for phase in ('toolchain-operation','toolchain-versions','toolchain-database','toolchain-paths'):
        log=w/('acquisition-'+phase+'.log');log.write_text('SYNTHETIC_PUBLIC_PACKAGE_DIAGNOSTIC')
        (w/('acquisition-'+phase+'.receipt.json')).write_text(json.dumps(dict(base,log=phase+'.log',log_sha256=digest(log))))
    (w/'acquisition-toolchain-progress.json').write_text(json.dumps({'classification':'PASS','stage':'completed','policy':we.TOOLCHAIN_POLICY}))
    (w/'acquisition-toolchain-selection.json').write_text(json.dumps({'policy_sha256':we.TOOLCHAIN_POLICY_SHA256,'selected_paths':['SYNTHETIC_ONLY']}))
    for name in we.ACQUISITION_DIAGNOSTICS:source['outputs'][name]=digest(w/('acquisition-'+name))
    (w/'dependency_acquisition.receipt.json').write_text(json.dumps(source))
    log=w/'toolchain-verification.log';log.write_text('SYNTHETIC_VERIFICATION')
    (w/'toolchain-verification.receipt.json').write_text(json.dumps(dict(base,log=log.name,log_sha256=digest(log))))
    (w/'toolchain-progress.json').write_text(json.dumps({'classification':'PASS','stage':'completed','policy':we.TOOLCHAIN_POLICY}))

class WorkflowEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        from test_publication import Publication,synthetic_native_pins
        synthetic_native_pins(self)
        Publication().evidence(self.root)
    def edit(self,path,change):
        f=self.root/path;r=json.loads(f.read_text());change(r);f.write_text(json.dumps(r))
    def rejects(self):
        with self.assertRaises((Blocked,KeyError,ValueError)):fin.validate(self.root)
    def test_complete_whole_evidence(self):self.assertEqual(fin.validate(self.root)['classification'],'PASS_BUILD_ONLY');self.assertFalse(fin.validate(self.root)['upload_verified'])
    def test_missing_setup_receipt(self):(self.root/'auxiliary/python_setup.receipt.json').unlink();self.rejects()
    def test_missing_acquisition_receipt(self):(self.root/'auxiliary/wheel_acquisition.receipt.json').unlink();self.rejects()
    def test_missing_setup_log(self):(self.root/'auxiliary/l3-python-setup/0.log').unlink();self.rejects()
    def test_missing_acquisition_log(self):(self.root/'auxiliary/wheel_acquisition.log').unlink();self.rejects()
    def test_corrupt_setup_log(self):(self.root/'auxiliary/l3-python-setup/0.log').write_text('CORRUPT');self.rejects()
    def test_corrupt_acquisition_log(self):(self.root/'auxiliary/wheel_acquisition.log').write_text('CORRUPT');self.rejects()
    def test_native_success_setup_failure(self):self.edit('auxiliary/python_setup.receipt.json',lambda r:r.update(classification='FAIL'));self.rejects()
    def test_native_success_no_auxiliary(self):
        import shutil
        shutil.rmtree(self.root/'auxiliary');self.assertEqual(fin.validate_native(self.root)['classification'],'PASS_BUILD_ONLY');self.rejects()
    def test_acquisition_timeout(self):self.edit('auxiliary/wheel_acquisition.receipt.json',lambda r:r['command'].update(timeout=True));self.rejects()
    def test_pip_hash_invalid(self):self.edit('auxiliary/python_setup.receipt.json',lambda r:r.update(installer=dict(PIN,sha256='0'*64)));self.rejects()
    def test_upload_incomplete(self):
        for outcome,id,h,local in [('failure','1','a'*64,True),('cancelled','1','a'*64,True),('success','','a'*64,True),('success','1','',True),('success','1','a'*64,False)]:
            with self.subTest(outcome=outcome,id=id,h=h,local=local),self.assertRaises(Blocked):we.upload_admission(outcome,id,h,local)
    def test_upload_success(self):self.assertEqual(we.upload_admission('success','123','sha256:'+'a'*64,True)['classification'],'PASS_BUILD_ONLY_EVIDENCE_UPLOADED')
    def test_upload_rejects_bare_sha256(self):
        with self.assertRaisesRegex(Blocked,'UPLOAD_INCOMPLETE'):
            we.upload_admission('success','123','a'*64,True)
    def test_upload_rejects_wrong_digest_algorithm(self):
        with self.assertRaises(Blocked):
            we.upload_admission('success','123','sha512:'+'a'*64,True)
    def test_upload_rejects_malformed_prefixed_digest(self):
        with self.assertRaises(Blocked):
            we.upload_admission('success','123','sha256:not-a-sha',True)
    def test_upload_action_pins_digest_output(self):
        source=(we.P/'workflow_evidence.py').read_text()
        self.assertIn("fullmatch('sha256:[a-f0-9]{64}'",source)
    def test_false_overall_pass(self):
        (self.root/'ci-evidence-status.json').write_text('{"classification":"PASS_BUILD_ONLY"}')
        (self.root/'auxiliary/wheel_acquisition.receipt.json').unlink();self.rejects()
    def test_phase_order(self):self.edit('auxiliary/python_setup.receipt.json',lambda r:r.update(started_at='2026-11-15T00:00:00+00:00',ended_at='2026-11-15T00:00:01+00:00'));self.rejects()
    def test_no_fabricated_native_resource_claim(self):self.edit('auxiliary/python_setup.receipt.json',lambda r:r['resource_evidence'].update(native_cgroup_claimed=True));self.rejects()
    def test_schema_extra_key(self):self.edit('auxiliary/python_setup.receipt.json',lambda r:r.update(unreviewed=True));self.rejects()
    def test_preservation_incomplete(self):self.edit('preservation.json',lambda r:r.update(classification='INCOMPLETE'));self.rejects()
    def test_collector_partial_preserves_available(self):
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);src=t/'l3-aux-evidence';src.mkdir();(src/'wheel_acquisition.log').write_text('PARTIAL')
            self.assertFalse(we.collect(t,t/'results'));self.assertEqual((t/'results/auxiliary/wheel_acquisition.log').read_text(),'PARTIAL')
    def test_collector_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);src=t/'l3-aux-evidence';src.mkdir();(src/'wheel_acquisition.log').symlink_to('/etc/passwd');self.assertFalse(we.collect(t,t/'results'));self.assertFalse((t/'results/auxiliary/wheel_acquisition.log').exists())
    def test_missing_inner_receipt(self):
        (self.root/'auxiliary/l3-wheel-acquisition/acquisition.json').unlink();self.rejects()
    def test_source_identity_drift(self):
        self.edit('auxiliary/wheel_acquisition.receipt.json',lambda r:r.update(source_sha256='0'*64));self.rejects()
    def test_incomplete_export_not_qualified(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'bundle';self.assertFalse(we.export_bundle(self.root,target))
            with self.assertRaises(Blocked):we.qualify_upload(target,'success','1','a'*64)
    def test_unknown_export_file_not_uploaded(self):
        (self.root/'SECRET_UNREVIEWED').write_text('SYNTHETIC_SECRET')
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'bundle';self.assertFalse(we.export_bundle(self.root,target));self.assertFalse((target/'SECRET_UNREVIEWED').exists())
    def test_export_partial_native_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'partial';source.mkdir();(source/'supervisor-control.jsonl').write_text('SYNTHETIC_PARTIAL')
            target=Path(d)/'bundle';self.assertTrue(we.export_bundle(source,target));self.assertEqual((target/'supervisor-control.jsonl').read_text(),'SYNTHETIC_PARTIAL')
            with self.assertRaises(Blocked):we.qualify_upload(target,'success','1','a'*64)
    def test_auxiliary_schema(self):
        import jsonschema
        schema=json.loads((we.P/'auxiliary-receipt.schema.json').read_text())
        for phase in we.AUX:jsonschema.validate(json.loads((self.root/'auxiliary'/(phase+'.receipt.json')).read_text()),schema)
    def test_upload_prior_failure_guard(self):
        text=(we.P/'workflow_evidence.py').read_text();workflow=(we.P/'native-build.WORKFLOW.PROPOSED.yml').read_text()
        self.assertIn("os.environ.get('PRIOR_JOB_STATUS')!='success'",text);self.assertIn('PRIOR_JOB_STATUS: ${{ job.status }}',workflow)
    def test_export_and_upload_complete_synthetic(self):
        # Normalize the old fixture's two synthetic log names to reviewed real names.
        w=self.root/'worker'
        for before,after in [('phase.log','source_verification.log'),('command.log','cmake_configuration.0.log')]:
            (w/before).rename(w/after)
            for f in w.glob('*.receipt.json'):
                text=f.read_text().replace('/'+before,'/'+after).replace('"'+before+'"','"'+after+'"');f.write_text(text)
        from test_publication import Publication
        final=json.loads((self.root/'supervisor-final.json').read_text());Publication().rehash(self.root,final)
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'bundle';self.assertTrue(we.export_bundle(self.root,target))
            self.assertEqual(we.qualify_upload(target,'success','1','sha256:'+'a'*64)['classification'],'PASS_BUILD_ONLY_EVIDENCE_UPLOADED')
    def test_missing_aux_accounting(self):
        self.edit('auxiliary/python_setup.receipt.json',lambda r:r['resource_evidence'].update(self_and_reaped_children={}));self.rejects()
    def test_contradictory_aux_timeout(self):
        self.edit('auxiliary/python_setup.receipt.json',lambda r:r['command'].update(timed_out=True));self.rejects()
    def test_failed_source_acquisition_partial_log_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);src=t/'l3-d2a-acquired';src.mkdir();(src/'dependency_acquisition.0.log').write_text('SYNTHETIC_FAILED_SOURCE_COMMAND')
            self.assertFalse(we.collect(t,t/'results'));self.assertEqual((t/'results/worker/dependency_acquisition.0.log').read_text(),'SYNTHETIC_FAILED_SOURCE_COMMAND')
    def test_classify_failed_setup(self):
        self.edit('auxiliary/python_setup.receipt.json',lambda r:r.update(classification='FAIL'));self.assertEqual(fin.failure_classification(self.root,Blocked('AUX_NOT_PASS')),'FAIL')
    def test_classify_timed_out_acquisition(self):
        self.edit('auxiliary/wheel_acquisition.receipt.json',lambda r:r['command'].update(timeout=True));self.assertEqual(fin.failure_classification(self.root,Blocked('AUX_NOT_PASS')),'INCOMPLETE')
    def test_classify_missing_evidence(self):self.assertEqual(fin.failure_classification(self.root,Blocked('MISSING_EVIDENCE')),'INCOMPLETE')
    def test_classify_policy_blocked(self):self.assertEqual(fin.failure_classification(self.root,Blocked('INSTALLER_OR_POLICY_DRIFT')),'BLOCKED')
    def test_required_input_missing(self):
        (self.root/'inputs/installer-pin.PROPOSED.json').unlink();self.rejects()
    def test_required_input_corrupted(self):
        (self.root/'inputs/installer-pin.PROPOSED.json').write_text('{}');self.rejects()
    def inspection_file(self):return self.root/'worker/0-l3_ngap_protocol_runtime.readelf'
    def assert_inspection_rejected(self,reason=None):
        with self.assertRaisesRegex(Blocked,reason or '.'):we.inspection_manifest(self.root)
        self.rejects()
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'bundle';self.assertFalse(we.export_bundle(self.root,target))
            with self.assertRaises(Blocked):we.qualify_upload(target,'success','1','a'*64)
    def test_inspection_exact_eighteen_outputs(self):
        result=we.inspection_manifest(self.root);self.assertEqual(len(result),18)
        self.assertEqual({Path(n).suffix for n in result},{'.readelf','.objdump','.symbols'})
    def test_inspection_missing(self):self.inspection_file().unlink();self.assert_inspection_rejected()
    def test_inspection_corrupted(self):self.inspection_file().write_text('CORRUPTED');self.assert_inspection_rejected()
    def test_inspection_unexpected(self):
        (self.root/'worker/999-extra.symbols').write_text('UNREVIEWED');self.assert_inspection_rejected()
    def test_inspection_oversized(self):
        with self.inspection_file().open('wb') as f:f.truncate(we.LIMIT+1)
        self.assert_inspection_rejected()
    def test_inspection_symlink(self):
        f=self.inspection_file();f.unlink();f.symlink_to(self.root/'worker/binary-inventory.json');self.assert_inspection_rejected()
    def test_inspection_incorrect_owner(self):
        with patch.object(we.os,'geteuid',return_value=123456789):self.assert_inspection_rejected()
    def test_inspection_group_writable(self):self.inspection_file().chmod(0o620);self.assert_inspection_rejected()
    def test_inspection_hardlink(self):
        import os
        os.link(self.inspection_file(),self.root/'hardlinked');self.assert_inspection_rejected()
    def test_inspection_total_budget(self):
        with patch.object(we,'INSPECTION_TOTAL_LIMIT',1):self.assert_inspection_rejected()
    def test_inspection_count_budget(self):
        with patch.object(we,'INSPECTION_MAX_BINARIES',5):self.assert_inspection_rejected()
    def edit_inspection_inventory(self,change):
        self.edit('worker/binary-inventory.json',change)
        self.edit('worker/binary_inspection.receipt.json',lambda r:r['outputs'].update({'/work/evidence/binary-inventory.json':digest(self.root/'worker/binary-inventory.json')}))
        from test_publication import Publication
        final=json.loads((self.root/'supervisor-final.json').read_text());Publication().rehash(self.root,final)
    def test_inspection_path_traversal(self):
        self.edit_inspection_inventory(lambda r:r[next(iter(r))]['inspection_artifacts']['readelf'].update(path='../escape.readelf'));self.assert_inspection_rejected('INSPECTION_ARTIFACT_PATH')
    def test_inspection_missing_symbols_hash(self):
        self.edit_inspection_inventory(lambda r:r[next(iter(r))].pop('symbols_sha256'));self.assert_inspection_rejected('INSPECTION_BINARY_SCHEMA')
    def test_inspection_unbound_phase(self):
        self.edit('worker/binary_inspection.receipt.json',lambda r:r['outputs'].pop('/work/evidence/0-l3_ngap_protocol_runtime.readelf'))
        from test_publication import Publication
        final=json.loads((self.root/'supervisor-final.json').read_text());Publication().rehash(self.root,final);self.assert_inspection_rejected()
    def test_inspection_unbound_supervisor(self):
        self.edit('supervisor-final.json',lambda r:r['evidence_sha256'].pop('worker/0-l3_ngap_protocol_runtime.readelf'));self.assert_inspection_rejected()
    def test_mandatory_commands_five(self):
        self.edit('auxiliary/python_setup.receipt.json',lambda r:None)
        f=self.root/'auxiliary/l3-python-setup/setup.json';r=json.loads(f.read_text());r['commands'].pop();f.write_text(json.dumps(r))
        self.edit('auxiliary/python_setup.receipt.json',lambda r:r['artifacts'].update({'l3-python-setup/setup.json':digest(f)}));self.rejects()
    def test_workflow_preserve_before_finalize_always_upload(self):
        import yaml
        w=yaml.load((we.P/'native-build.WORKFLOW.PROPOSED.yml').read_text(),Loader=yaml.BaseLoader);steps=w['jobs']['build-only']['steps']
        i=next(i for i,s in enumerate(steps) if 'workflow_evidence.py" collect' in s.get('run',''))
        j=next(i for i,s in enumerate(steps) if 'finalize_build_evidence.py' in s.get('run',''))
        self.assertLess(i,j);self.assertEqual(steps[i]['if'],'always()');self.assertEqual(steps[j]['if'],'always()');self.assertEqual(next(s for s in steps if s.get('id')=='evidence_upload')['if'],'always()')
if __name__=='__main__':unittest.main()
