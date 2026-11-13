"""Negative-case Stage5G source-patcher contract; synthetics, NO runtime claim."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATCH=ROOT/"experiments/oai-ngap-2026w13/stage5g/patch_oai_ue_cause9.py"
HANDLER='static void handle_service_reject(nr_ue_nas_t *nas, const byte_array_t *buffer)\n{'
REJECT=('  /* Retrying the Service Request would loop forever, and re-registering is not supported:\n'
        '   * terminate as after a Registration Reject. */\n'
        '  LOG_E(NAS, "Service Reject: cannot connect, exiting\\\n");')
IDLE='        nas->fiveGMM_mode = FGS_IDLE;\n        // TODO handle connection release'


def source():
    return ('/* self-contained synthetic source for source-transform contract only */\n'
            +HANDLER+'\n'
            +'  abort_service_request(nas);\n'+REJECT+'\n'
            +'  nas->fiveGMM_state = FGS_DEREGISTERED;\n'
            +'  nas->termination_procedure = true;\n'
            +'  send_nas_detach_req(nas, false);\n'
            +'}\nvoid rrcrelease(void) {\n'
            +IDLE+'\n}\n')


class Cause9Patch(unittest.TestCase):
    def apply(self,text):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"nr_nas_msg.c"
            p.write_text(text)
            done=subprocess.run([sys.executable,str(PATCH),str(p)],
                                capture_output=True,text=True,timeout=12)
            return done,p.read_text()

    def test_patched_case9_only_on_rrc_idle(self):
        result,src=self.apply(source())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('msg.cause == UE_identity_cannot_be_derived_by_the_network',src)
        self.assertIn('nas->guti = NULL;',src)
        self.assertIn('nas->ksi = NULL;',src)
        self.assertIn('stream_security_container_delete(nas->security_container)',src)
        self.assertIn('stage5g_pending_initial_registration[nas->UE_id] = true;',src)
        self.assertIn('nas->fiveGMM_mode = FGS_IDLE;',src)
        self.assertIn('generateRegistrationRequest(&registration, nas, false);',src)
        self.assertIn('send_nas_initial_ul_transfer_req(nas, &registration);',src)
        self.assertIn('stage5g_pending_initial_registration[nas->UE_id] = false;',src)
        self.assertIn('nas->termination_procedure = true;',src)
        self.assertIn('send_nas_detach_req(nas, false);',src)
        self.assertNotIn('near_ric_ip_addr',src)

    def test_refuse_missing_duplicate_or_modified_anchors(self):
        original=source()
        cases={
          'empty':'',
          'remove_reject':original.replace('Service Reject: cannot connect, exiting','other'),
          'remove_idle':original.replace('// TODO handle connection release','// removed'),
          'duplicate_handler':original+HANDLER+'\n',
          'duplicate_idle':original+IDLE+'\n',
          'duplicate_reject':original+REJECT+'\n',
          'already_patched':original+'stage5g_pending_initial_registration',
        }
        for label,text in cases.items():
            with self.subTest(label=label):
                result,actual=self.apply(text)
                self.assertNotEqual(result.returncode,0,label)
                self.assertEqual(actual,text,label)

    def test_upstream_source_fingerprint_documented(self):
        builder=(ROOT/"scripts/hcran_build_pinned_w45_ue_cause9.sh").read_text()
        self.assertIn('UPSTREAM=dfb296ab867050a7bde81b393fa31b757ada2c83',builder)
        self.assertIn('UPSTREAM_NAS_BLOB=60f009118ef1db9ba29f6f85075ffee0f73d459d',builder)
        self.assertIn('git diff --check',builder)
        self.assertIn('test "$(git -C "$OAI_SOURCE" diff --name-only)"',builder)
        self.assertNotIn('kubectl apply',builder)
        self.assertNotIn('osm ns-',builder)


if __name__=="__main__":
    unittest.main()
