"""No-false-positive tests for pinned OAI one-layer initialization repair."""
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/"scripts/experiments/hcransrs_pinned_oai_pdsch_baseline.py"
spec=importlib.util.spec_from_file_location("hcransrs_pinned_oai_pdsch_baseline",path)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

MAIN=("void nr_ue_init_mac(NR_UE_MAC_INST_t *mac)\n"
      "{\n  nr_ue_reset_sync_state(mac, false);\n"
      +m.OLD+"\n  mac->p_Max = INT_MIN;\n}\n")
CONFIG=("void handle_mac_uecap_info(void)\n{\n  "+m.CAPABILITY_ASSIGNMENT+"\n}\n")
DCI=("if (sc_info->maxMIMO_Layers_PDSCH)\n"
     "  max_mimo_layers = *sc_info->maxMIMO_Layers_PDSCH;\n"
     "else\n  max_mimo_layers = mac->uecap_maxMIMO_PDSCH_layers;\n"
     "AssertFatal(max_mimo_layers > 0, \"Invalid max MIMO layers\");\n")

class PinnedLayerDefaultTests(unittest.TestCase):
    def test_one_line_baseline_is_source_justified_and_preserves_dci(self):
        revised=m.checked_text(MAIN,CONFIG,DCI,"apply")
        self.assertEqual(revised,MAIN.replace(m.OLD,m.NEW))
        self.assertEqual(revised.count(m.NEW),1)
        self.assertEqual(m.checked_text(revised,CONFIG,DCI,"verify"),revised)
        self.assertIn("AssertFatal(max_mimo_layers > 0",DCI)
    def test_reject_unexpected_initializer_source(self):
        with self.assertRaises(m.SourceMismatch):
            m.checked_text(MAIN.replace(m.OLD,"  mac->uecap_maxMIMO_PDSCH_layers = 5;"),
                           CONFIG,DCI,"apply")
    def test_reject_unexpected_capability_or_dci_fallback(self):
        with self.assertRaises(m.SourceMismatch):
            m.checked_text(MAIN,"void f(void){}\n",DCI,"apply")
        with self.assertRaises(m.SourceMismatch):
            m.checked_text(MAIN,CONFIG,"void f(void){}\n","apply")
    def test_reject_unapproved_already_patched_version(self):
        with self.assertRaises(m.SourceMismatch):
            m.checked_text(MAIN.replace(m.OLD,m.NEW),CONFIG,DCI,"check")
    def test_blob_hash_is_true_git_blob_not_arbitrary_digest(self):
        raw=b"abc\n"
        import hashlib
        self.assertEqual(m.git_blob_sha(raw),
                         hashlib.sha1(b"blob 4\0"+raw).hexdigest())
    def test_no_other_ue_capability_changes(self):
        revised=m.checked_text(MAIN,CONFIG,DCI,"apply")
        self.assertEqual(len(revised.splitlines()),len(MAIN.splitlines()))
        self.assertEqual(m.PIN,"dfb296ab867050a7bde81b393fa31b757ada2c83")

if __name__ == "__main__":
    unittest.main()
