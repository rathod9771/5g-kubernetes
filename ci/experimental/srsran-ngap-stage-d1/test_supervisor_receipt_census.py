"""Reproduce the exact D2B completed-run receipt census without native execution.

Run 44460838788 had ten PASS native receipts and five toolchain helpers.
The old supervisor fed all fifteen into verify_complete, yielding a false
MISSING_PHASE_RECEIPT even though every required phase had passed.
"""
import json,tempfile,unittest,hashlib
from pathlib import Path
from build_policy import Blocked,PHASES
from build_supervisor import TOOLCHAIN_HELPER_RECEIPTS,PRE_COLLECTION_HELPER_RECEIPTS,COLLECTED_ACQUISITION_HELPER_RECEIPTS,select_native_phase_receipts,ACQUISITION_DIAGNOSTICS,resolve_retained_phase_output,verify_retained_phase_outputs

class ExactReceiptCensus(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worker=Path(temp.name)
        for phase in set(PHASES)|TOOLCHAIN_HELPER_RECEIPTS:
            (worker/(phase+'.receipt.json')).write_text(json.dumps({'phase':phase,'classification':'PASS'}))
        return worker
    def test_completed_runner_fifteen_receipts_resolve_to_ten_native_phases(self):
        worker=self.fixture()
        self.assertEqual(len(list(worker.glob('*.receipt.json'))),15)
        native=select_native_phase_receipts(worker)
        self.assertEqual(set(native),set(PHASES))
        self.assertEqual(len(native),10)
    def test_toolchain_helper_names_are_explicitly_frozen(self):
        self.assertEqual(TOOLCHAIN_HELPER_RECEIPTS,frozenset({
            'toolchain-verification','acquisition-toolchain-operation',
            'acquisition-toolchain-versions','acquisition-toolchain-database',
            'acquisition-toolchain-paths'}))
    def test_missing_native_phase_rejected(self):
        worker=self.fixture()
        (worker/'binary_inspection.receipt.json').unlink()
        with self.assertRaisesRegex(Blocked,'MISSING_NATIVE_PHASE_RECEIPT:binary_inspection'):
            select_native_phase_receipts(worker)
    def test_missing_precollection_verification_receipt_rejected(self):
        worker=self.fixture()
        (worker/'toolchain-verification.receipt.json').unlink()
        with self.assertRaisesRegex(Blocked,'MISSING_TOOLCHAIN_HELPER_RECEIPT'):
            select_native_phase_receipts(worker)
    def test_precollection_eleven_receipts_are_exactly_expected(self):
        worker=self.fixture()
        for name in COLLECTED_ACQUISITION_HELPER_RECEIPTS:
            (worker/(name+'.receipt.json')).unlink()
        self.assertEqual(len(list(worker.glob('*.receipt.json'))),11)
        self.assertEqual(set(select_native_phase_receipts(worker)),set(PHASES))
    def test_partially_collected_acquisition_receipts_fail_closed(self):
        worker=self.fixture()
        (worker/'acquisition-toolchain-database.receipt.json').unlink()
        with self.assertRaisesRegex(Blocked,'PARTIAL_ACQUISITION_HELPER_RECEIPTS'):
            select_native_phase_receipts(worker)
    def test_unreviewed_receipt_rejected(self):
        worker=self.fixture()
        (worker/'injected-extra.receipt.json').write_text('{}')
        with self.assertRaisesRegex(Blocked,'UNREVIEWED_PHASE_RECEIPT:injected-extra'):
            select_native_phase_receipts(worker)
    def test_symlink_receipt_rejected(self):
        worker=self.fixture()
        file=worker/'toolchain-verification.receipt.json'
        file.unlink()
        file.symlink_to(worker/'source_verification.receipt.json')
        with self.assertRaisesRegex(Blocked,'UNSAFE_PHASE_RECEIPT_FILE'):
            select_native_phase_receipts(worker)
    def test_corrupted_required_phase_json_rejected(self):
        worker=self.fixture()
        (worker/'meson_configuration.receipt.json').write_text('{broken')
        with self.assertRaises(json.JSONDecodeError):
            select_native_phase_receipts(worker)
    def test_supervisor_uses_selected_phase_receipts_not_glob_dict(self):
        source=(Path(__file__).parent/'build_supervisor.py').read_text()
        self.assertIn('receipts=select_native_phase_receipts(worker)',source)
        self.assertIn('verify_complete(receipts,',source)
        self.assertNotIn("receipts={f.name.removesuffix('.receipt.json'):",source)

if __name__=='__main__':unittest.main()

class AcquisitionSnapshotBinding(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.mount=self.root/'mount';self.worker=self.root/'worker'
        self.mount.mkdir();self.worker.mkdir()
        self.data={}
        for name in ACQUISITION_DIAGNOSTICS:
            value=('SAVED:'+name).encode()
            path=self.worker/('acquisition-'+name)
            path.write_bytes(value)
            self.data[name]=hashlib.sha256(value).hexdigest()
        # The later runtime progress is intentionally a different revision.
        (self.worker/'toolchain-progress.json').write_text('LATEST - NOT ACQUISITION')
        self.data['toolchain-lock.json']=hashlib.sha256(b'LOCK').hexdigest()
        (self.worker/'toolchain-lock.json').write_bytes(b'LOCK')
        self.receipts={'dependency_acquisition':{'outputs':self.data}}
    def test_all_ten_acquisition_outputs_use_snapshots(self):
        self.assertEqual(verify_retained_phase_outputs(self.receipts,self.mount,self.worker),11)
    def test_original_acquisition_progress_not_overwritten(self):
        snapshot=resolve_retained_phase_output('dependency_acquisition','toolchain-progress.json',self.mount,self.worker)
        self.assertEqual(snapshot.name,'acquisition-toolchain-progress.json')
        self.assertNotEqual(snapshot,self.worker/'toolchain-progress.json')
    def test_later_phases_do_not_remap_toolchain_files(self):
        later=resolve_retained_phase_output('source_verification','toolchain-progress.json',self.mount,self.worker)
        self.assertEqual(later,self.worker/'toolchain-progress.json')
    def test_archive_missing_fails_closed(self):
        (self.worker/'acquisition-toolchain-paths.log').unlink()
        with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION:dependency_acquisition:toolchain-paths.log'):
            verify_retained_phase_outputs(self.receipts,self.mount,self.worker)
    def test_archive_corruption_fails_closed(self):
        (self.worker/'acquisition-toolchain-selection.json').write_text('tampered')
        with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION:dependency_acquisition:toolchain-selection.json'):
            verify_retained_phase_outputs(self.receipts,self.mount,self.worker)
    def test_unreviewed_output_remains_relative_and_verified(self):
        self.receipts['dependency_acquisition']['outputs']['unexpected-toolchain.log']='0'*64
        with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION'):
            verify_retained_phase_outputs(self.receipts,self.mount,self.worker)
    def test_work_paths_preserved(self):
        expected=b'WORK_BINARY_BYTES'
        location=self.mount/'srs-build'/'test-file'
        location.parent.mkdir()
        location.write_bytes(expected)
        x={'open5gs_compilation':{'outputs':{'/work/srs-build/test-file':hashlib.sha256(expected).hexdigest()}}}
        self.assertEqual(verify_retained_phase_outputs(x,self.mount,self.worker),1)
    def test_escape_rejected(self):
        with self.assertRaisesRegex(Blocked,'UNEXPECTED_ARTIFACT_REFERENCE'):
            resolve_retained_phase_output('dependency_acquisition','../toolchain-operation.log',self.mount,self.worker)
        with self.assertRaisesRegex(Blocked,'UNEXPECTED_ARTIFACT_REFERENCE'):
            resolve_retained_phase_output('dependency_acquisition','/tmp/external.json',self.mount,self.worker)
    def test_static_supervisor_calls_exact_replay(self):
        source=(Path(__file__).parent/'build_supervisor.py').read_text()
        self.assertIn('verify_retained_phase_outputs(receipts,mount,worker,sources)',source)
        self.assertIn('from toolchain_lock import ACQUISITION_DIAGNOSTICS',source)

class LiveAcquisitionBoundary(unittest.TestCase):
    """Reproduce the live pre-collection state absent from previous tests."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.mount=self.root/'mount';self.mount.mkdir()
        self.worker=self.root/'worker';self.worker.mkdir()
        self.sources=self.root/'l3-d2a-acquired';self.sources.mkdir()
        self.outputs={}
        for name in ACQUISITION_DIAGNOSTICS:
            raw=('PINNED_ACQUIRED:'+name).encode()
            (self.sources/name).write_bytes(raw)
            self.outputs[name]=hashlib.sha256(raw).hexdigest()
        (self.worker/'toolchain-progress.json').write_text('later version - SHA differs')
        (self.worker/'toolchain-lock.json').write_text('unchanged lock')
        self.outputs['toolchain-lock.json']=hashlib.sha256(b'unchanged lock').hexdigest()
        self.receipts={'dependency_acquisition':{'outputs':self.outputs}}
    def test_live_supervisor_checks_original_acquired_bytes_not_future_copy(self):
        self.assertFalse(any(self.worker.glob('acquisition-*')))
        self.assertEqual(verify_retained_phase_outputs(self.receipts,self.mount,self.worker,self.sources),11)
        self.assertNotEqual(hashlib.sha256((self.worker/'toolchain-progress.json').read_bytes()).hexdigest(),
                            self.outputs['toolchain-progress.json'])
    def test_missing_source_snapshot_blocks_even_when_later_progress_exists(self):
        (self.sources/'toolchain-progress.json').unlink()
        with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION:dependency_acquisition:toolchain-progress.json'):
            verify_retained_phase_outputs(self.receipts,self.mount,self.worker,self.sources)
    def test_changed_source_receipt_blocks(self):
        (self.sources/'toolchain-operation.receipt.json').write_text('altered')
        with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION:dependency_acquisition:toolchain-operation.receipt.json'):
            verify_retained_phase_outputs(self.receipts,self.mount,self.worker,self.sources)
    def test_future_collector_alias_cannot_mask_broken_live_source(self):
        name='toolchain-versions.log'
        (self.worker/('acquisition-'+name)).write_bytes(('PINNED_ACQUIRED:'+name).encode())
        (self.sources/name).write_text('corrupted in live acquisition root')
        with self.assertRaisesRegex(Blocked,'ARTIFACT_CORRUPTION:dependency_acquisition:toolchain-versions.log'):
            verify_retained_phase_outputs(self.receipts,self.mount,self.worker,self.sources)
    def test_postcollection_offline_alias_remains_valid(self):
        for name in ACQUISITION_DIAGNOSTICS:
            (self.worker/('acquisition-'+name)).write_bytes((self.sources/name).read_bytes())
        self.assertEqual(verify_retained_phase_outputs(self.receipts,self.mount,self.worker),11)
    def test_path_traversal_still_denied_with_live_sources(self):
        with self.assertRaisesRegex(Blocked,'UNEXPECTED_ARTIFACT_REFERENCE'):
            resolve_retained_phase_output('dependency_acquisition','../toolchain-progress.json',
                                          self.mount,self.worker,self.sources)
