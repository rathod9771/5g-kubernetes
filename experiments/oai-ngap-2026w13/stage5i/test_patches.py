"""Offline Stage5I fail-closed patcher contract; NOT real radio qualification."""
from importlib import util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parent

def load(module):
    spec=util.spec_from_file_location(module, ROOT/(module+".py"))
    obj=util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

class PatchContracts(unittest.TestCase):
    def test_cu_no_amf_failure_releases_exact_once(self):
        m=load("patch_no_amf_release")
        changed=m.patch("prefix\n"+m.ANCHOR+"\nsuffix")
        self.assertIn("NGAP_UE_CONTEXT_RELEASE_COMMAND(release).gNB_ue_ngap_id = ue_id;",changed)
        self.assertIn(r'(research only)\n", ue_id);', changed)
        self.assertNotIn('(research only)\n", ue_id);', changed)
        self.assertIn("free_byte_array(NGAP_NAS_FIRST_REQ(received_msg).nas_pdu);",changed)
        with self.assertRaises(ValueError):m.patch(changed)
        with self.assertRaises(ValueError):m.patch(m.ANCHOR+m.ANCHOR)
        with self.assertRaises(ValueError):m.patch("different upstream")

    def test_ue_rearms_only_on_interrupted_service_request(self):
        m=load("patch_ue_idle_rearm")
        changed=m.patch("prefix\n"+m.ANCHOR+"\nsuffix")
        self.assertIn("stage5i_interrupted_sr",changed)
        self.assertIn("if (stage5i_interrupted_sr)",changed)
        self.assertIn("NAS_TUN_START_IDLE_LISTENER",changed)
        self.assertIn(r'listener rearmed\n");',changed)
        self.assertNotIn('listener rearmed\n");',changed)
        with self.assertRaises(ValueError):m.patch(changed)
        with self.assertRaises(ValueError):m.patch("no Stage5G anchor")

if __name__=="__main__":
    unittest.main()
