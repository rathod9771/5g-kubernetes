"""Local-only O-RAN chart, package and acceptance evidence regressions."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import osm_packages as packages
import validate_oran_oai as acceptance
from runtime_images import chart_values, verify_rendered
from scenario_registry import dashboard_scenarios, load_registry, select_scenarios

HEADER = 'ASSOC SOCK STY SST ST HBKT ASSOC-ID TX_QUEUE RX_QUEUE UID INODE LPORT RPORT LADDRS <-> RADDRS\n'
CU_TABLE = HEADER + ('a b 2 1 3 0 1 0 0 0 42 43252 38412 10.244.0.10 <-> *10.100.0.1\n'
                     'c d 2 1 3 0 2 0 0 0 43 38472 57335 10.244.0.10 <-> *10.244.0.11\n')
DU_TABLE = HEADER + 'e f 2 1 3 0 3 0 0 0 44 57335 38472 10.244.0.11 <-> *10.100.0.2\n'


class ChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = select_scenarios(load_registry(), ['oran-oai'])[0]

    def render(self, role, e2=False, address='10.107.153.48', resources=None):
        chart = next(c for c in self.scenario['charts'] if c['kdu'] == role)
        with tempfile.TemporaryDirectory() as d:
            values = chart_values(ROOT, chart)
            if resources:
                values['resources'] = resources
            path = Path(d) / 'approved.yaml'
            path.write_text(yaml.safe_dump(values))
            command = [os.environ.get('HELM_BIN', 'helm'), 'template', chart['release_name'], str(ROOT / chart['source']), '-f', str(path)]
            if e2:
                command += ['-f', str(ROOT / chart['source'] / 'values-e2.yaml'),
                            '--set-string', 'e2.ricAddress=' + address]
            result = subprocess.run(command, capture_output=True, text=True,
                                    env=dict(os.environ, KUBECONFIG='/dev/null'), timeout=20)
            return result, chart

    def test_default_is_canonical_rfsim_without_e2_dependency(self):
        catalog = dashboard_scenarios(load_registry())['oran-oai']
        self.assertEqual(catalog['pods'], ['oran-oai-cu', 'oran-oai-du'])
        self.assertEqual([r[0] for r in catalog['releases']], catalog['pods'])
        for role, tag in [('cu', '2026.w13'), ('du', '2026.w25')]:
            result, chart = self.render(role)
            self.assertEqual(result.returncode, 0, result.stderr)
            docs = list(yaml.safe_load_all(result.stdout))
            verify_rendered(ROOT, chart, docs)
            deployment = next(d for d in docs if d['kind'] == 'Deployment')
            self.assertEqual(deployment['metadata']['name'], 'oran-oai-' + role)
            pod = deployment['spec']['template']
            self.assertEqual(pod['metadata']['labels']['ran-type'], 'oran')
            self.assertEqual(pod['metadata']['annotations']['sidecar.istio.io/inject'], 'false')
            self.assertNotIn('initContainers', pod['spec'])
            self.assertEqual(pod['spec']['containers'][0]['image'], 'oaisoftwarealliance/oai-gnb:' + tag)
            self.assertNotIn('e2_agent', result.stdout)
            if role == 'cu':
                self.assertIn('getent hosts amf-ngap-stable', result.stdout)
            else:
                self.assertIn('getent hosts oran-oai-cu', result.stdout)
                self.assertIn('--rfsim', result.stdout)

    def test_e2_isolated_plugins_and_resources_use_approved_images(self):
        resources = {'requests': {'cpu': '250m', 'memory': '256Mi'},
                     'limits': {'cpu': '1', 'memory': '1Gi'}}
        for role in ('cu', 'du'):
            result, chart = self.render(role, e2=True, resources=resources)
            self.assertEqual(result.returncode, 0, result.stderr)
            docs = list(yaml.safe_load_all(result.stdout))
            verify_rendered(ROOT, chart, docs)
            deployment = next(d for d in docs if d['kind'] == 'Deployment')
            pod = deployment['spec']['template']['spec']
            self.assertEqual(pod['containers'][0]['resources'], resources)
            init = pod['initContainers'][0]
            self.assertEqual(init['image'], pod['containers'][0]['image'])
            self.assertEqual(init['imagePullPolicy'], pod['containers'][0]['imagePullPolicy'])
            self.assertEqual(init['command'][2],
                             'cp /usr/local/lib/flexric/libkpm_sm.so /usr/local/lib/flexric/librc_sm.so /sm-out/')
            self.assertIn({'name': 'e2-sm', 'emptyDir': {}}, pod['volumes'])
            self.assertIn({'name': 'e2-sm', 'mountPath': '/tmp/flexric-sm-kpm-rc', 'readOnly': True},
                          pod['containers'][0]['volumeMounts'])
            self.assertIn('sm_dir = "/tmp/flexric-sm-kpm-rc/"', result.stdout)
            self.assertNotIn('sm_dir = "/usr/local/lib/flexric/"', result.stdout)

    def test_e2_endpoint_rejects_dns_placeholder_missing_or_invalid_ipv4(self):
        for role in ('cu', 'du'):
            for address in ['', 'flexric.default.svc.cluster.local', 'REPLACE_WITH_FLEXRIC_SERVICE_IP',
                            '10.1.2.999', '10.01.2.3', '::1']:
                with self.subTest(role=role, address=address):
                    result, _ = self.render(role, e2=True, address=address)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('IPv4', result.stderr)

    def test_generated_package_references_and_profile_are_valid(self):
        artifacts = packages.expected_artifacts(ROOT, self.scenario)
        packages.check_helm(ROOT, self.scenario, artifacts)
        vnfd = json.loads(artifacts['oran_oai_knf/oran_oai_knf_vnfd.yaml'])['vnfd']
        nsd = json.loads(artifacts['oran_oai_ns/oran_oai_ns_nsd.yaml'])['nsd']['nsd'][0]
        self.assertEqual(vnfd['kdu'], [{'name': 'cu', 'helm-chart': 'cu'}, {'name': 'du', 'helm-chart': 'du'}])
        self.assertEqual(nsd['vnfd-id'], ['oran_oai_knf'])
        self.assertEqual(nsd['df'][0]['vnf-profile'][0]['id'], 'oran-oai')
        for role in ('cu', 'du'):
            prefix = 'oran_oai_knf/helm-chart-v3s/' + role + '/'
            values = json.loads(artifacts[prefix + 'values.yaml'])
            self.assertFalse(values['e2']['enabled'])
            self.assertIn(prefix + 'values-e2.yaml', artifacts)
        self.assertEqual(json.loads(artifacts['oran_oai_knf/helm-chart-v3s/du/values.yaml'])['rf']['mode'], 'rfsim')


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.pair, self.evidence = {}, {}
        for role, address, tag in [('cu', '10.244.0.10', '2026.w13'), ('du', '10.244.0.11', '2026.w25')]:
            identity = 'oran-oai-' + role
            self.pair[role] = {
                'metadata': {'name': identity + '-fixture', 'uid': role + '-uid',
                             'labels': {'app': identity, 'ran-type': 'oran', 'component': role}},
                'spec': {'containers': [{'name': role, 'image': 'oaisoftwarealliance/oai-gnb:' + tag}]},
                'status': {'phase': 'Running', 'podIP': address,
                           'containerStatuses': [{'name': role, 'ready': True, 'restartCount': 0,
                                                  'state': {'running': {'startedAt': '2026-10-05T00:00:00Z'}}}]}}
            logs = ('Received NGSetupResponse from AMF\nReceived F1 Setup Request from gNB_DU' if role == 'cu'
                    else 'received F1 Setup Response from CU')
            self.evidence[role] = {'assocs': CU_TABLE if role == 'cu' else DU_TABLE, 'config': '', 'logs': logs,
                                   'cmdline': 'nr-softmodem -O /tmp/conf/du.conf --rfsim'}

    def check(self, e2=False):
        return acceptance.evaluate(self.pair, self.evidence, '10.100.0.1', '10.100.0.2',
                                   '10.107.153.48' if e2 else None)

    def enable_e2(self):
        for role in ('cu', 'du'):
            self.evidence[role].update(
                config='e2_agent = { near_ric_ip_addr = "10.107.153.48"; sm_dir = "/tmp/flexric-sm-kpm-rc/"; };',
                plugin_files='libkpm_sm.so\nlibrc_sm.so\n',
                maps='a /tmp/flexric-sm-kpm-rc/libkpm_sm.so\nb /tmp/flexric-sm-kpm-rc/librc_sm.so\n')

    def test_initial_acceptance_requires_no_ue_or_pdu(self):
        self.assertTrue(all(self.check().values()))

    def test_sctp_transport_alone_does_not_prove_ngap_or_f1_setup(self):
        self.evidence['cu']['logs'] = ''
        checks = self.check()
        self.assertTrue(checks['ngap_cu_amf'])
        self.assertFalse(checks['ngap_setup_response'])
        self.assertFalse(checks['f1_setup_exchange'])

    def test_wrong_identity_image_not_ready_or_restart_fails(self):
        for role in ('cu', 'du'):
            original = copy.deepcopy(self.pair)
            mutations = [lambda p: p['metadata']['labels'].update(app='oai-' + role),
                         lambda p: p['spec']['containers'][0].update(image='unapproved/image:latest'),
                         lambda p: p['status']['containerStatuses'][0].update(ready=False),
                         lambda p: p['status']['containerStatuses'][0].update(restartCount=1),
                         lambda p: p['status']['containerStatuses'][0].update(state={'waiting': {'reason': 'CrashLoopBackOff'}})]
            for mutate in mutations:
                self.pair = copy.deepcopy(original)
                mutate(self.pair[role])
                self.assertFalse(all(self.check().values()))
            self.pair = original

    def test_ngap_f1_need_established_state_and_correct_peers(self):
        original = copy.deepcopy(self.evidence)
        for role in ('cu', 'du'):
            for old, new in [('2 1 3', '3 3 1'), ('38472', '9999'),
                             ('*10.244.0.11', '*10.244.0.111') if role == 'cu' else ('*10.100.0.2', '*10.100.0.20')]:
                self.evidence = copy.deepcopy(original)
                self.evidence[role]['assocs'] = original[role]['assocs'].replace(old, new)
                self.assertFalse(all(self.check().values()))
        self.evidence = original
        self.evidence['cu']['assocs'] = CU_TABLE.replace('43252 38412', '38412 43252')
        self.assertFalse(self.check()['ngap_cu_amf'])

    def test_explicit_setup_request_tx_accepts_without_response_or_association(self):
        self.enable_e2()
        self.evidence['du']['logs'] += '\n2026-10-05T00:00:01Z [E2-AGENT]: E2 SETUP-REQUEST tx\n'
        self.assertTrue(all(self.check(e2=True).values()))
        for logs in ['E2 Setup Request', 'E2 Setup Request attempt', 'E2 SETUP-REQUEST rx', 'E2 Setup Response']:
            self.evidence['du']['logs'] = logs
            self.assertFalse(self.check(e2=True)['e2_association_or_setup_request_tx'])

    def test_established_e2_association_is_an_alternative(self):
        self.enable_e2()
        self.evidence['du']['assocs'] += 'a b 2 1 3 0 9 0 0 0 42 44000 36421 10.244.0.11 <-> *10.107.153.48\n'
        self.assertTrue(all(self.check(e2=True).values()))
        self.evidence['du']['assocs'] = self.evidence['du']['assocs'].replace('*10.107.153.48', '*10.107.153.49')
        self.assertFalse(self.check(e2=True)['e2_association_or_setup_request_tx'])

    def test_file_presence_does_not_prove_plugin_load_and_extra_plugin_fails(self):
        self.enable_e2()
        self.evidence['du']['logs'] = '[E2-AGENT]: E2 SETUP-REQUEST tx'
        self.evidence['cu']['maps'] = ''
        self.assertFalse(self.check(e2=True)['cu_kpm_rc_loaded'])
        self.evidence['du']['plugin_files'] += 'libmac_sm.so\n'
        self.assertFalse(self.check(e2=True)['du_only_kpm_rc_exposed'])
        self.evidence['cu']['maps'] = '/tmp/flexric-sm-kpm-rc/libkpm_sm.so\n/tmp/flexric-sm-kpm-rc/librc_sm.so\n/usr/local/lib/flexric/libmac_sm.so\n'
        self.assertFalse(self.check(e2=True)['cu_kpm_rc_loaded'])
        self.evidence['du']['logs'] += '\nAssertion something failed'
        self.assertFalse(self.check(e2=True)['du_no_fatal_log'])

    def test_actual_service_ip_and_read_only_cli(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({'spec': {'clusterIP': '10.107.153.48'}}))
        with mock.patch.object(acceptance.subprocess, 'run', side_effect=run), mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            result = acceptance.main(['--kubeconfig', '/tmp/fixture', '--namespace', 'isolated', '--ric-address'])
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), '10.107.153.48\n')
        self.assertEqual(commands, [['kubectl', '--kubeconfig', '/tmp/fixture', '--request-timeout=10s', '-n', 'isolated',
                                     'get', 'service', 'flexric', '-o', 'json']])
        for address in ['None', 'flexric.default.svc.cluster.local', '::1', '0.0.0.0']:
            with self.assertRaises(ValueError):
                acceptance.service_ipv4({'spec': {'clusterIP': address}})

    def test_cli_acceptance_rechecks_same_pods_and_uses_only_read_commands(self):
        self.enable_e2()
        self.evidence['du']['logs'] += '\n[E2-AGENT]: E2 SETUP-REQUEST tx'
        commands, samples = [], []
        def run(command, **kwargs):
            commands.append(command)
            operation = command[6:]
            self.assertIn(operation[0], {'get', 'exec', 'logs'})
            if operation[:2] == ['get', 'pods']:
                pair = copy.deepcopy(self.pair)
                if samples and change_uid[0]:
                    pair['cu']['metadata']['uid'] = 'replaced'
                samples.append(pair)
                output = json.dumps({'items': list(pair.values())})
            elif operation[:2] == ['get', 'service']:
                address = {'amf-ngap-stable': '10.100.0.1', 'oran-oai-cu': '10.100.0.2',
                           'flexric': '10.107.153.48'}[operation[2]]
                output = json.dumps({'spec': {'clusterIP': address}})
            else:
                role = operation[1].split('-')[2]
                if operation[0] == 'logs':
                    self.assertIn('--since-time=2026-10-05T00:00:00Z', operation)
                    output = self.evidence[role]['logs']
                else:
                    path = operation[-1]
                    key = {'/proc/net/sctp/assocs': 'assocs', '/proc/1/maps': 'maps',
                           '/proc/1/cmdline': 'cmdline', '/tmp/conf/' + role + '.conf': 'config',
                           acceptance.SM_DIR: 'plugin_files'}[path]
                    output = self.evidence[role][key]
            return subprocess.CompletedProcess(command, 0, output)
        for changed in (False, True):
            change_uid = [changed]
            samples.clear()
            with mock.patch.object(acceptance.subprocess, 'run', side_effect=run), mock.patch.object(acceptance.time, 'sleep') as sleep, mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                result = acceptance.main(['--kubeconfig', '/tmp/fixture', '--namespace', 'isolated', '--e2'])
                sleep.assert_called_once_with(30)
            self.assertEqual(len(samples), 2)
            self.assertEqual(result, 1 if changed else 0)
            self.assertIn('ORAN_OAI_E2_' + ('NOT_ACCEPTED' if changed else 'ACCEPTED'), output.getvalue())

    def test_missing_or_duplicate_canonical_pair_is_rejected(self):
        for items in [[], [self.pair['cu']], [self.pair['cu'], self.pair['cu'], self.pair['du']]]:
            with self.assertRaises(ValueError):
                acceptance.select_pair({'items': items})


if __name__ == '__main__':
    unittest.main()
