"""Offline binary format and receipt-bound partial export tests."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from build_policy import Blocked
from binary_inspection_preflight import preflight
import workflow_evidence as we

class BinaryFormats(unittest.TestCase):
 def fixture(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  root=Path(temp.name);paths=[]
  for i in range(5):
   p=root/('exe'+str(i));p.write_bytes(b'\x7fELF'+b'0'*32);paths.append(p)
  return root,paths
 def test_elf_and_archive_recognized_without_qualifying(self):
  root,paths=self.fixture();lib=root/'libx.a';lib.write_bytes(b'!<arch>\nabc')
  result=preflight(paths+[lib],root,root/'receipt.json')
  self.assertEqual(result[5]['format'],'AR')
  self.assertFalse(json.loads((root/'receipt.json').read_text())['native_build_qualified'])
 def test_thin_archive_recognized(self):
  root,paths=self.fixture();lib=root/'libx.a';lib.write_bytes(b'!<thin>\nabc')
  self.assertEqual(preflight(paths+[lib],root,root/'receipt.json')[5]['format'],'THIN_AR')
 def test_nonelf_shared_library_blocks_with_index(self):
  root,paths=self.fixture();lib=root/'libx.so';lib.write_text('INPUT(foo.so)')
  with self.assertRaisesRegex(Blocked,'UNRECOGNIZED_FORMAT'):preflight(paths+[lib],root,root/'receipt.json')
  self.assertEqual(json.loads((root/'receipt.json').read_text())['rejected_indices'],[5])
 def test_wrong_executable_blocks(self):
  root,paths=self.fixture();paths[0].write_text('NOT ELF')
  with self.assertRaises(Blocked):preflight(paths,root,root/'receipt.json')
 def test_symlink_and_empty_rejected(self):
  root,paths=self.fixture();lib=root/'libx.so';lib.symlink_to('/etc/passwd')
  with self.assertRaises(Blocked):preflight(paths+[lib],root,root/'receipt.json')
  lib.unlink();lib.touch()
  with self.assertRaises(Blocked):preflight(paths+[lib],root,root/'receipt.json')
 def test_candidate_count_bounded(self):
  root,paths=self.fixture()
  with self.assertRaisesRegex(Blocked,'COUNT'):preflight(paths*104,root,root/'receipt.json')

class PartialInspection(unittest.TestCase):
 def fixture(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  root=Path(temp.name);w=root/'worker';w.mkdir();path=w/'0-driver.readelf'
  path.write_text('synthetic inspection text');path.chmod(0o600)
  h=hashlib.sha256(path.read_bytes()).hexdigest()
  (w/'binary_inspection.receipt.json').write_text(json.dumps({'classification':'INCOMPLETE','outputs':{'/work/evidence/'+path.name:h}}))
  (root/'supervisor-final.json').write_text(json.dumps({'status':'INCOMPLETE','evidence_sha256':{'worker/'+path.name:h}}))
  return root,path
 def test_signed_partial_is_diagnostic_only(self):
  root,p=self.fixture()
  self.assertEqual(set(we.partial_inspection_manifest(root)),{'worker/'+p.name})
 def test_corrupt_partial_denied(self):
  root,p=self.fixture();p.write_text('corrupt')
  with self.assertRaises(Blocked):we.partial_inspection_manifest(root)
 def test_unbound_supervisor_denied(self):
  root,p=self.fixture();f=root/'supervisor-final.json';a=json.loads(f.read_text());a['evidence_sha256']={};f.write_text(json.dumps(a))
  with self.assertRaises(Blocked):we.partial_inspection_manifest(root)
 def test_success_cannot_use_partial(self):
  root,p=self.fixture();f=root/'supervisor-final.json';a=json.loads(f.read_text());a['status']='PASS';f.write_text(json.dumps(a))
  with self.assertRaises(Blocked):we.partial_inspection_manifest(root)
 def test_invalid_filename_denied(self):
  root,p=self.fixture();p.rename(p.with_name('999-unknown.readelf'))
  with self.assertRaises(Blocked):we.partial_inspection_manifest(root)
 def test_symlink_denied(self):
  root,p=self.fixture();p.unlink();p.symlink_to('/etc/passwd')
  with self.assertRaises(Blocked):we.partial_inspection_manifest(root)

if __name__=='__main__':unittest.main()

class InspectionPhaseReceipt(unittest.TestCase):
 def test_optional_failure_receipt_not_required_on_success(self):
  from build_receipts import Recorder
  from build_policy import environment
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   inventory=root/'binary-inventory.json'
   discovery=root/'binary-inspection-discovery.json'
   missing=root/'binary-inspection-failure.json'
   def done():
    inventory.write_text('SYNTHETIC_INVENTORY')
    discovery.write_text('SYNTHETIC_DISCOVERY')
   r=Recorder(root,{'PATH':'/usr/bin:/bin'})
   receipt=r.phase('binary_inspection',[],root,done,outputs=[inventory,discovery])
   self.assertNotIn(str(missing),r.receipts['binary_inspection']['outputs'])
   self.assertEqual(r.receipts['binary_inspection']['classification'],'PASS')
 def test_worker_records_actual_failure_but_does_not_require_absent_failure_receipt(self):
  source=(Path(__file__).parent/'build_worker.py').read_text()
  self.assertIn("inspection_outputs=[out/'binary-inventory.json',out/'binary-inspection-discovery.json']",source)
  self.assertIn("inspection_outputs.append(out/'binary-inspection-failure.json')",source)


class MesonSharedLibrarySelection(unittest.TestCase):
 def fake_tree(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  root=Path(tmp.name)
  paths=[]
  for soname in ['libprom.so','libogsapp.so.2.7.5','libogsngap.so.2.7.5',
                 'libogsasn1c-ngap.so.2.7.5','libogssbi-openapi.so.2.7.5']:
   library=root/soname
   library.write_bytes(b'\x7fELF'+b'0'*32)
   sidecar=root/(soname+'.p')/(soname+'.symbols')
   sidecar.parent.mkdir()
   sidecar.write_text(' 0x00000000000000 symbol\n')
   paths.append((library,sidecar))
  return root,paths
 def test_actual_meson_so_p_sidecar_layout_is_excluded(self):
  from binary_inspection_preflight import select_shared_libraries
  root,pairs=self.fake_tree()
  libraries,ignored=select_shared_libraries(root)
  self.assertEqual(set(libraries),{p[0] for p in pairs})
  self.assertEqual(set(ignored),{p[1] for p in pairs})
  self.assertTrue(all(p.name.endswith('.symbols') for p in ignored))
 def test_real_libraries_still_checked_as_elf(self):
  from binary_inspection_preflight import select_shared_libraries
  root,pairs=self.fake_tree()
  libs,ignored=select_shared_libraries(root)
  binaries=[]
  for idx in range(5):
   p=root/('executable'+str(idx));p.write_bytes(b'\x7fELF'+bytes(16));binaries.append(p)
  rows=preflight(binaries+libs,root,root/'discovery.json',ignored)
  self.assertTrue(all(row['recognized'] for row in rows))
  report=json.loads((root/'discovery.json').read_text())
  self.assertEqual(len(report['ignored_meson_symbol_sidecars']),5)
  self.assertEqual(report['classification'],'FORMATS_RECOGNIZED')
 def test_nonelf_so_with_reviewed_name_is_still_rejected(self):
  from binary_inspection_preflight import select_shared_libraries
  root,pairs=self.fake_tree()
  pairs[0][0].write_text('INPUT(/tmp/evil.so)')
  libs,_=select_shared_libraries(root)
  binaries=[]
  for idx in range(5):
   p=root/('executable'+str(idx));p.write_bytes(b'\x7fELF'+bytes(16));binaries.append(p)
  with self.assertRaisesRegex(Blocked,'UNRECOGNIZED_FORMAT'):
   preflight(binaries+libs,root,root/'discovery.json')
 def test_nonstandard_so_suffix_cannot_disappear_silently(self):
  from binary_inspection_preflight import select_shared_libraries
  root,pairs=self.fake_tree()
  (root/'libsurprise.so.backup').write_text('bad')
  with self.assertRaisesRegex(Blocked,'UNREVIEWED_SHARED_LIBRARY_CANDIDATE'):
   select_shared_libraries(root)
 def test_spoofed_symbols_sidecar_path_rejected(self):
  from binary_inspection_preflight import select_shared_libraries
  root,pairs=self.fake_tree()
  (root/'libsurprise.so.symbols').write_text('not inside reviewed .p')
  with self.assertRaisesRegex(Blocked,'UNREVIEWED_SHARED_LIBRARY_CANDIDATE'):
   select_shared_libraries(root)
 def test_case_matches_previous_15_sidecar_failure(self):
  from binary_inspection_preflight import select_shared_libraries
  root=Path(tempfile.mkdtemp());self.addCleanup(__import__('shutil').rmtree,root)
  sonames=['libprom.so','libogsapp.so.2.7.5','libogsproto.so.2.7.5',
   'libogsmetrics.so.2.7.5','libogsngap.so.2.7.5','libogsasn1c-ngap.so.2.7.5',
   'libogsasn1c-util.so.2.7.5','libogsasn1c-common.so.2.7.5',
   'libogscrypt.so.2.7.5','libogssbi.so.2.7.5',
   'libogssbi-openapi.so.2.7.5','libogsnas-common.so.2.7.5',
   'libogsnas-5gs.so.2.7.5','libogssctp.so.2.7.5','libogscore.so.2.7.5']
  for name in sonames:
   p=root/name;p.write_bytes(b'\x7fELF'+bytes(16))
   q=root/(name+'.p')/(name+'.symbols');q.parent.mkdir();q.write_text(' 0x0000 symbol\n')
  libs,excluded=select_shared_libraries(root)
  self.assertEqual(len(libs),15)
  self.assertEqual(len(excluded),15)
