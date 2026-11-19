"""Reproduce the exact D2B completed-run receipt census without native execution.

Run 44460838788 had ten PASS native receipts and five toolchain helpers.
The old supervisor fed all fifteen into verify_complete, yielding a false
MISSING_PHASE_RECEIPT even though every required phase had passed.
"""
import json,tempfile,unittest
from pathlib import Path
from build_policy import Blocked,PHASES
from build_supervisor import TOOLCHAIN_HELPER_RECEIPTS,select_native_phase_receipts

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
    def test_missing_helper_receipt_rejected(self):
        worker=self.fixture()
        (worker/'acquisition-toolchain-database.receipt.json').unlink()
        with self.assertRaisesRegex(Blocked,'MISSING_TOOLCHAIN_HELPER_RECEIPT'):
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
