"""Live UE discovery covers generated and legacy OAI labels without deployment."""
import shlex
import subprocess
import unittest
from unittest import mock
import test_pending_adoption as adoption

backend, ROOT = adoption.backend, adoption.ROOT
SELECTOR = 'app in (oai-nr-ue,oai-nr-ue-cran)'
TUNNEL = '3: oaitun_ue1 inet 10.45.0.2/24 scope global oaitun_ue1\n'


class UEDiscoveryTests(unittest.TestCase):
    def test_live_api_generated_and_legacy_tunnel(self):
        for label in ('oai-nr-ue-cran', 'oai-nr-ue'):
            with self.subTest(label=label):
                pod = label + '-hash-pod'
                def run(command):
                    args = shlex.split(command)
                    if args[:3] == ['kubectl', 'get', 'pods']:
                        self.assertEqual(args[args.index('-l') + 1], SELECTOR)
                        # Kubernetes matches either app label with this set selector.
                        self.assertIn(label, SELECTOR.partition('(')[2].rstrip(')').split(','))
                        return 0, pod, ''
                    self.assertEqual(args[:2], ['kubectl', 'exec'])
                    self.assertIn(pod, args)
                    self.assertEqual(args[args.index('--') + 1:][:6],
                                     ['ip', '-4', '-o', 'addr', 'show', 'oaitun_ue1'])
                    return 0, TUNNEL, ''
                with mock.patch.object(backend, '_namespace', return_value='test'), \
                     mock.patch.object(backend, 'run', side_effect=run), \
                     mock.patch.object(backend, '_prom_query', return_value=None):
                    response = backend.app.test_client().get('/api/ue-status')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['pod'], pod)
                self.assertEqual(response.json['tun'], '10.45.0.2')
                self.assertTrue(response.json['registered'])
                self.assertTrue(response.json['pdu_session'])

    def test_validation_generated_and_legacy_tunnel(self):
        source = (ROOT / 'scripts/validate.sh').read_text()
        block = source.split('# ---- 10/11/12/13.')[1].split('# ---- 15/16.')[0]
        block = block[block.index('\n'):]
        for label in ('oai-nr-ue-cran', 'oai-nr-ue'):
            with self.subTest(label=label):
                script = '''
declare -A RESULT
CORE_NS=test
k_() {
  if [[ "$1 $2" == "get pods" ]]; then
    if [[ "$6" == 'app in (oai-nr-ue,oai-nr-ue-cran)' ]]; then
      printf '%s-hash-pod 1/1 Running 0 1m\\n' "$TEST_LABEL"
    elif [[ "$6" != component=ue ]]; then
      exit 99
    fi
  elif [[ "$1" == exec && "$4" == "$TEST_LABEL-hash-pod" ]]; then
    echo '3: oaitun_ue1 inet 10.45.0.2/24 scope global oaitun_ue1'
  else
    exit 98
  fi
}
evidence() { :; }
print_check() { :; }
''' + block + '''
[[ "$UE_POD" == "$TEST_LABEL-hash-pod" && "${RESULT[ue_reg]}" == READY && "${RESULT[ue_pdu]}" == READY ]]
'''
                result = subprocess.run(['bash', '-c', 'TEST_LABEL=' + shlex.quote(label) + '\n' + script],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_absent_ue_stays_inactive(self):
        with mock.patch.object(backend, 'run', return_value=(0, '', '')), \
             mock.patch.object(backend, '_namespace', return_value='test'), \
             mock.patch.object(backend, '_prom_query', return_value=None):
            result = backend._ue_status_payload()
        self.assertFalse(result['registered'])
        self.assertFalse(result['pdu_session'])
        self.assertEqual(result['pod'], '')

    def test_dashboard_uses_live_ue_api(self):
        self.assertIn("/api/ue-status", (ROOT / 'ran-selector/index.html').read_text())
