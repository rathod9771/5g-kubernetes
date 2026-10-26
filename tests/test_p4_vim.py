"""Offline selected-VIM diagnostics; no cluster or NBI access."""
import json
import unittest
from test_p4_experiment import Harness
from p4_vim import diagnose_vim


class DiagnosticTests(unittest.TestCase):
    def test_known_decryption_error(self):
        self.assertEqual(diagnose_vim({'_admin': {'detailed-status': 'Cannot decrypt information.'}})['status'], 'FAILED')

    def test_commonkey_message_is_only_a_reason_code(self):
        v = diagnose_vim({'_admin': {'deployed': {'RO': {'error_msg': 'Are you using same COMMONKEY in all OSM components? password=private-token'}}}})
        self.assertEqual(v['reason'], 'RO_VIM_CREDENTIAL_DECRYPTION_FAILED')
        self.assertNotIn('private-token', json.dumps(v))
        self.assertNotIn('COMMONKEY', json.dumps(v))

    def test_disabled_fails_closed(self):
        for state in ('DISABLED', 'ERROR', 'FAILED'):
            self.assertEqual(diagnose_vim({'_admin': {'operationalState': state}})['status'], 'FAILED')

    def test_enabled_not_proof(self):
        self.assertEqual(diagnose_vim({'_admin': {'operationalState': 'ENABLED'}})['status'], 'INCONCLUSIVE')

    def test_missing_or_malformed_is_inconclusive(self):
        for record in (None, [], {}, {'_admin': None}, {'_admin': {'deployed': []}}):
            self.assertEqual(diagnose_vim(record)['status'], 'INCONCLUSIVE')

    def test_credentials_are_not_traversed(self):
        self.assertEqual(diagnose_vim({'config': {'error': 'Cannot decrypt information.'}, 'vim_password': 'secret'})['reason'], 'CONNECTOR_HEALTH_NOT_PROVEN')

    def test_oversized_text_is_explicitly_inconclusive(self):
        v = diagnose_vim({'error': 'x'*100000 + 'Cannot decrypt information.'})
        self.assertEqual(v['reason'], 'STATUS_TEXT_TRUNCATED')

    def test_known_failure_before_truncation_remains_failed(self):
        self.assertEqual(diagnose_vim({'error': 'Cannot decrypt information.' + 'x'*5000})['status'], 'FAILED')

    def test_other_error_not_misdiagnosed_as_decryption(self):
        self.assertEqual(diagnose_vim({'error': 'Connection timeout'})['status'], 'INCONCLUSIVE')

    def test_loaded_message_not_proof(self):
        self.assertEqual(diagnose_vim({'status': 'Connector loaded for vim:example, plugin=rovim_dummy'})['status'], 'INCONCLUSIVE')


class PreflightTests(Harness):
    def test_broken_vim_stops_before_any_mutation(self):
        self.target.vim['_admin'] = {'detailed-status': 'Cannot decrypt information. password=never-retain'}
        exp = self.experiment()
        result = exp.run(('preflight', 'onboard'))
        self.assertTrue(any(c['passed'] is False for c in result['checks']))
        self.assertEqual(self.target.mutations, [])
        self.assertNotIn('never-retain', json.dumps(result))
        self.assertTrue(list((self.work/'evidence'/'preflight').glob('vim-health.json')))

    def test_inconclusive_vim_is_recorded_separately(self):
        result = self.experiment().run(('preflight',))
        self.assertFalse(any(c['passed'] is False for c in result['checks']))
        self.assertTrue(any(c.get('observed', {}).get('status') == 'INCONCLUSIVE' for c in result['checks']))
        self.assertEqual(self.target.mutations, [])


class RoTests(unittest.TestCase):
    VIM = '061f7fef-9e47-48cc-a0e6-764f4d5aff8d'
    NOW = 1792800000.

    def record(self, message, age=0):
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(self.NOW-age, timezone.utc).isoformat()
        return stamp+' '+message

    def diagnose(self, text):
        from p4_vim import diagnose_ro
        return diagnose_ro(text, self.VIM, self.NOW)

    def test_exact_dell_multiline_failure(self):
        text = self.record('Cannot load vim: '+self.VIM+'\nDecrypting password database exception\nCannot decrypt information.\nAre you using same COMMONKEY in all OSM components?\n\'FailingConnector\' object is not subscriptable.')
        value = self.diagnose(text)
        self.assertEqual(value['reason'], 'RO_VIM_CREDENTIAL_DECRYPTION_FAILED')
        self.assertEqual(value['status'], 'FAILED')
        self.assertNotIn('COMMONKEY', json.dumps(value))

    def test_failing_connector_alone_not_claimed_decryption(self):
        value = self.diagnose(self.record(self.VIM+" 'FailingConnector' object is not subscriptable."))
        self.assertEqual(value['reason'], 'RO_VIM_CONNECTOR_FAILED')

    def test_other_vim_not_failed(self):
        self.assertEqual(self.diagnose(self.record('Cannot decrypt information. '+ 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'))['status'], 'INCONCLUSIVE')

    def test_unbound_traceback_not_failed(self):
        self.assertEqual(self.diagnose(self.record('Cannot decrypt information.'))['status'], 'INCONCLUSIVE')

    def test_historical_failure_not_current(self):
        self.assertEqual(self.diagnose(self.record('Cannot decrypt information. '+self.VIM, 121))['status'], 'INCONCLUSIVE')

    def test_missing_timestamp_not_current(self):
        self.assertEqual(self.diagnose('Cannot decrypt information. '+self.VIM)['status'], 'INCONCLUSIVE')

    def test_repair_load_supersedes_old_failure(self):
        text = self.record('Cannot decrypt information. '+self.VIM, 60)+'\n'+self.record('Connector loaded for vim:'+self.VIM+', plugin=rovim_dummy')
        self.assertEqual(self.diagnose(text)['status'], 'HEALTHY')

    def test_failure_after_load(self):
        text = self.record('Connector loaded for vim:'+self.VIM, 60)+'\n'+self.record('Cannot load vim: '+self.VIM)
        self.assertEqual(self.diagnose(text)['status'], 'FAILED')

    def test_empty_not_healthy(self):
        self.assertEqual(self.diagnose('')['status'], 'INCONCLUSIVE')

    def test_future_not_current(self):
        self.assertEqual(self.diagnose(self.record('Cannot load vim: '+self.VIM, -1))['status'], 'INCONCLUSIVE')

    def test_ambiguous_block_not_failed(self):
        self.assertEqual(self.diagnose(self.record('Cannot decrypt information. '+self.VIM+' aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'))['status'], 'INCONCLUSIVE')

    def test_output_bound(self):
        self.assertEqual(self.diagnose('x'*65537)['status'], 'INCONCLUSIVE')

    def test_reader_fixed_bounded_read_only_command(self):
        from unittest import mock
        from p4_vim import read_ro
        with mock.patch('subprocess.Popen', side_effect=OSError('secret-error')) as launch:
            result = read_ro({'kubectl':'kubectl','kubeconfig':'/fixture','ro_diagnostics':{'pod':'ro-123','container':'ro'}},self.VIM,self.NOW)
        argv = launch.call_args.args[0]
        self.assertIn('logs', argv)
        self.assertIn('--limit-bytes=65536',argv)
        self.assertIn('--since=120s',argv)
        self.assertEqual(result['status'],'INCONCLUSIVE')
        self.assertNotIn('secret-error',json.dumps(result))

    def test_not_configured_never_launches(self):
        from unittest import mock
        from p4_vim import read_ro
        with mock.patch('subprocess.Popen') as launch:
            self.assertEqual(read_ro({},self.VIM,self.NOW)['status'],'INCONCLUSIVE')
            launch.assert_not_called()

    def test_ro_failure_reaches_preflight_gate(self):
        from unittest import mock
        case = PreflightTests('test_inconclusive_vim_is_recorded_separately')
        case.setUp()
        try:
            with mock.patch('p4_experiment.read_ro',return_value=self.diagnose(self.record('Cannot load vim: '+self.VIM+'\nCannot decrypt information.'))):
                result = case.experiment().run(('preflight','onboard'))
            self.assertGreater(result['failed'],0)
            self.assertEqual(case.target.mutations,[])
        finally:
            case.doCleanups()
