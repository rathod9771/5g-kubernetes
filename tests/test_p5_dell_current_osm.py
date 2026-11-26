"""Adversarial, fully offline tests for the Dell current P5 observer."""
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/experiments/p5'))
import p5_dell_current_osm as obs

UID = '11111111-1111-4111-8111-111111111111'
NSID = '22222222-2222-4222-8222-222222222222'
VIM = '33333333-3333-4333-8333-333333333333'
CLUSTER = '44444444-4444-4444-8444-444444444444'
PROJECT = '55555555-5555-4555-8555-555555555555'


def sample():
    return {'namespace_uid': UID,
        'service': {'metadata': {'name': 'nbi'}, 'spec': {'type':'ClusterIP', 'ports': [{'port': 9999, 'protocol':'TCP'}]}},
        'slices': [{'metadata': {'labels': {'kubernetes.io/service-name': 'nbi'}},
                    'ports':[{'port':9999,'protocol':'TCP'}],
                    'endpoints':[{'conditions': {'ready':True},'addresses':['10.244.0.57']}]}],
        'ingresses': [{'spec': {'rules': [{'host':obs.NBI_HOST,
            'http': {'paths':[{'backend':{'service':{'name':'nbi','port':{'number':9999}}}}]}}]}}],
        'pods': [{'spec': {'containers': [{'name':'nbi'}]},
                  'status': {'phase':'Running','containerStatuses':[{'name':'nbi','ready':True}]}}]}


def raw_inventory():
    return {
        '/admin/v1/projects': [{'_id':PROJECT,'name':'admin','secret':'DO_NOT_EXPORT'}],
        '/admin/v1/vim_accounts': [{'_id':VIM,'name':'dell-vim','vim_password':'DO_NOT_EXPORT'}],
        '/admin/v1/k8sclusters': [{'_id':CLUSTER,'name':'dell-k8s','k8scluster_creds':'DO_NOT_EXPORT'}],
        '/nslcm/v1/ns_instances': [{'_id':NSID,'name':'research-ns','nsState':'READY','vimAccountId':VIM,
                                   'config': {'token':'DO_NOT_EXPORT'}}],
    }


def locked():
    return {'existing_policy_locked':True, 'deployment_authorized':False}


class DellObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token_file = Path(self.tmp.name) / 'token'
        self.token_file.write_text('simulated-token-123456789012')
        self.token_file.chmod(0o600)

    def fake_run(self, *, bad_server=False, bad_uid=False):
        calls = []
        def run(cmd):
            calls.append(cmd)
            if cmd == ['kubectl','config','current-context']:
                return obs.CONTEXT
            if cmd == ['kubectl','config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}']:
                return obs.SERVER if not bad_server else 'https://172.30.0.20:6443'
            name = cmd[cmd.index('get') + 1]
            if name == 'namespace/kube-system':
                data = {'metadata':{'name':'kube-system', 'uid':('bad' if bad_uid else obs.KUBE_SYSTEM_UID)},
                        'status':{'phase':'Active'}}
            elif name == 'node/' + obs.NODE:
                data = {'metadata': {'name': obs.NODE}, 'status': {'conditions':[{'type':'Ready','status':'True'}]}}
            elif name == 'namespace/osm':
                data = {'metadata':{'name':'osm','uid':UID},'status':{'phase':'Active'}}
            elif name == 'service/nbi':
                data = sample()['service']
            elif name == 'endpointslices':
                data = {'items':sample()['slices']}
            elif name == 'ingress':
                data = {'items':sample()['ingresses']}
            elif name == 'pods':
                data = {'items':sample()['pods']}
            else:
                raise AssertionError('unexpected command ' + repr(cmd))
            return json.dumps(data)
        return run, calls

    def test_current_dell_identity_exact_and_kubectl_read_only(self):
        run, calls = self.fake_run()
        result = obs.collect(run=run, hostname=lambda:obs.HOST)
        self.assertEqual(obs.evaluate(result)['nbi_ready_endpoints'], 1)
        self.assertEqual(len(calls), 9)
        self.assertFalse(any('apply' in x or 'delete' in x or 'patch' in x for cmd in calls for x in cmd))
        self.assertTrue(all(cmd[0]=='kubectl' for cmd in calls))
        self.assertEqual(calls[2][2], obs.CONTEXT)

    def test_wrong_host_refused_before_any_command(self):
        calls=[]
        with self.assertRaisesRegex(obs.Refused,'WRONG_HOST'):
            obs.collect(run=lambda argv:calls.append(argv), hostname=lambda:'HP')
        self.assertEqual(calls,[])

    def test_wrong_context_refused_before_kubernetes_get(self):
        with self.assertRaisesRegex(obs.Refused,'WRONG_CONTEXT'):
            obs.collect(run=lambda _: 'other-context', hostname=lambda:obs.HOST)

    def test_wrong_server_refused_before_kubernetes_get(self):
        run,calls = self.fake_run(bad_server=True)
        with self.assertRaisesRegex(obs.Refused,'WRONG_API_SERVER'):
            obs.collect(run=run, hostname=lambda:obs.HOST)
        self.assertEqual(len(calls),2)

    def test_kube_system_uid_precedes_osm_queries(self):
        run,calls = self.fake_run(bad_uid=True)
        with self.assertRaisesRegex(obs.Refused,'WRONG_KUBERNETES_CLUSTER_UID'):
            obs.collect(run=run, hostname=lambda:obs.HOST)
        self.assertEqual(len(calls),3)

    def test_preflight_is_not_deploy_permission(self):
        r = obs.observation(collect_fn=sample, policy_fn=locked)
        self.assertEqual(r['status'],'OBSERVED')
        self.assertFalse(r['nbi_authenticated'])
        for field in ('deployment_authorized','catalog_publication_authorized',
                      'remediation_authorized','osm_ns_vim_binding_verified','mutations_performed'):
            self.assertFalse(r[field],field)
        self.assertEqual(r['api_server'],obs.SERVER)

    def test_no_cluster_query_before_closed_p4_gate(self):
        with self.assertRaisesRegex(obs.Refused,'P5_POLICY_NOT_LOCKED'):
            obs.observation(policy_fn=lambda:{'existing_policy_locked':False,'deployment_authorized':False},
                            collect_fn=lambda: self.fail('no kubectl'))

    def test_missing_token_refused_after_verified_preflight(self):
        with self.assertRaisesRegex(obs.Refused,'TOKEN_REQUIRED'):
            obs.observation(inventory=True, collect_fn=sample, policy_fn=locked)

    def test_bad_preflight_never_reads_token(self):
        s=sample();s['slices'][0]['endpoints'][0]['conditions']['ready']=False
        with self.assertRaisesRegex(obs.Refused,'NBI_NO_READY_ENDPOINT'):
            obs.observation(inventory=True,token_file=self.token_file, collect_fn=lambda:s,
                            token_fn=lambda _:self.fail('token accessed'),policy_fn=locked)

    def test_wrong_nbi_ingress_refused(self):
        s=sample();s['ingresses'][0]['spec']['rules'][0]['host']='nbi.other.invalid'
        with self.assertRaisesRegex(obs.Refused,'NBI_INGRESS_BINDING_MISMATCH'):
            obs.evaluate(s)

    def test_wrong_endpoint_service_refused(self):
        s=sample();s['slices'][0]['metadata']['labels']['kubernetes.io/service-name']='other'
        with self.assertRaisesRegex(obs.Refused,'NBI_ENDPOINTSLICE_MISMATCH'):
            obs.evaluate(s)

    def test_pods_not_all_ready_refused(self):
        s=sample();s['pods'][0]['status']['containerStatuses'][0]['ready']=False
        with self.assertRaisesRegex(obs.Refused,'OSM_PODS_NOT_ALL_READY'):
            obs.evaluate(s)

    def test_wrong_nbi_service_port_refused(self):
        s=sample();s['service']['spec']['ports'][0]['port']=8080
        with self.assertRaisesRegex(obs.Refused,'NBI_SERVICE_PORT_MISMATCH'):
            obs.evaluate(s)

    def test_symlink_and_world_readable_token_refused(self):
        self.assertEqual(obs.private_token(self.token_file),'simulated-token-123456789012')
        link=Path(self.tmp.name)/'token-symlink';link.symlink_to(self.token_file)
        with self.assertRaisesRegex(obs.Refused,'TOKEN_NOT_AVAILABLE'):
            obs.private_token(link)
        self.token_file.chmod(0o644)
        with self.assertRaisesRegex(obs.Refused,'TOKEN_NOT_PRIVATE'):
            obs.private_token(self.token_file)

    def test_inventory_success_remains_blocked_and_scrubs_credentials(self):
        events=[]
        @contextmanager
        def fake_tunnel():
            events.append('opened')
            try:
                yield 22345
            finally:
                events.append('closed')
        def fake_getter(token,port):
            self.assertEqual(token,'simulated-token-123456789012')
            self.assertEqual(port,22345)
            return raw_inventory().__getitem__
        result=obs.observation(inventory=True,token_file=self.token_file,collect_fn=sample,
                policy_fn=locked,tunnel_fn=fake_tunnel,getter_fn=fake_getter)
        self.assertEqual(events,['opened','closed'])
        self.assertTrue(result['nbi_authenticated'])
        self.assertEqual(result['inventory']['ns_instances'][0]['id'],NSID)
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(result))
        self.assertFalse(result['osm_ns_vim_binding_verified'])
        self.assertFalse(result['deployment_authorized'])
        self.assertIn('NS_VIM_BINDING_SELECTION_AND_VERIFICATION_PENDING',result['blockers'])

    def test_duplicate_ns_identity_refused(self):
        data=raw_inventory();data['/nslcm/v1/ns_instances'].append(copy.deepcopy(data['/nslcm/v1/ns_instances'][0]))
        with self.assertRaisesRegex(obs.Refused,'NBI_DUPLICATE_IDENTITY'):
            obs.sanitized_inventory(data.__getitem__)

    def test_invalid_ns_name_cannot_be_output(self):
        data=raw_inventory();data['/nslcm/v1/ns_instances'][0]['name']='password:unsafe'
        with self.assertRaisesRegex(obs.Refused,'NBI_INVENTORY_IDENTITY_INVALID'):
            obs.sanitized_inventory(data.__getitem__)

    def test_getter_is_exact_get_only_loopback_no_proxy(self):
        response=mock.MagicMock()
        response.__enter__.return_value.status=200
        response.__enter__.return_value.headers={}
        response.__enter__.return_value.read.return_value=b'[]'
        fake=mock.Mock();fake.open.return_value=response
        g=obs.getter('simulated-token-123456789012',29870,opener_factory=lambda *args:fake)
        self.assertEqual(g('/admin/v1/projects'),[])
        req=fake.open.call_args.args[0]
        self.assertEqual(req.full_url,'http://127.0.0.1:29870/osm/admin/v1/projects')
        self.assertEqual(req.get_method(),'GET')
        with self.assertRaisesRegex(obs.Refused,'NBI_PATH_NOT_ALLOWED'):
            g('/nslcm/v1/ns_instances/action')
        self.assertEqual(fake.open.call_count,1)

    def test_nbi_duplicate_yaml_keys_are_rejected(self):
        response=mock.MagicMock()
        response.__enter__.return_value.status=200
        response.__enter__.return_value.headers={}
        response.__enter__.return_value.read.return_value=(
            b'- _id: 11111111-1111-4111-8111-111111111111\n'
            b'  name: safe\n'
            b'  name: overwritten\n')
        fake=mock.Mock();fake.open.return_value=response
        g=obs.getter('simulated-token-123456789012',29870,opener_factory=lambda *args:fake)
        with self.assertRaisesRegex(obs.Refused,'NBI_DUPLICATE_YAML_KEY'):
            g('/admin/v1/projects')

    def test_response_redirect_never_allowed(self):
        self.assertIsNone(obs.NoRedirect().redirect_request(None,None,None,None,None,None,None))

    def test_invalid_forward_port_refused(self):
        for port in [0,80,70000,True,'22222',None]:
            with self.subTest(port=port),self.assertRaisesRegex(obs.Refused,'PORT_FORWARD_FAILED'):
                obs.getter('fake',port)

    def test_port_forward_has_only_loopback_and_cleanup(self):
        process=mock.Mock();process.stdout=io.StringIO('Forwarding from 127.0.0.1:22345 -> 9999\n')
        process.poll.return_value=None
        with mock.patch.object(obs.subprocess,'Popen',return_value=process) as popen, \
             mock.patch.object(obs.select,'select',return_value=([process.stdout],[],[])):
            with obs.tunnel() as port:self.assertEqual(port,22345)
        args,kwargs=popen.call_args
        self.assertEqual(args[0],['kubectl','--context',obs.CONTEXT,'--namespace','osm',
                                  'port-forward','--address','127.0.0.1','service/nbi',':9999'])
        self.assertNotIn('shell',kwargs)
        process.terminate.assert_called_once()

    def test_public_port_forward_refused_and_closed(self):
        process=mock.Mock();process.stdout=io.StringIO('Forwarding from 0.0.0.0:22345 -> 9999\n')
        process.poll.return_value=None
        with mock.patch.object(obs.subprocess,'Popen',return_value=process), \
             mock.patch.object(obs.select,'select',return_value=([process.stdout],[],[])):
            with self.assertRaisesRegex(obs.Refused,'PORT_FORWARD_FAILED'):
                with obs.tunnel():pass
        process.terminate.assert_called_once()

    def test_private_evidence_atomic_file_permissions(self):
        out=Path(self.tmp.name)/'evidence.json'
        obs.save_private(out,{'deployment_authorized':False})
        self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o600)
        with self.assertRaisesRegex(obs.Refused,'INVALID_OUTPUT_LOCATION'):
            obs.save_private(out,{'deployment_authorized':False})

    def test_strict_json_duplicate_and_nonfinite_rejected(self):
        for raw in ['{"a":1,"a":2}','{"x":NaN}']:
            with self.subTest(raw=raw),self.assertRaises(obs.Refused):
                obs.strict_json(raw)

    def test_cli_refuses_missing_token_without_writing_evidence(self):
        with mock.patch.object(obs,'observation',side_effect=obs.Refused('TOKEN_REQUIRED')):
            self.assertEqual(obs.main(['--inventory','--token-file',str(self.token_file)]),2)

if __name__ == '__main__':
    unittest.main()
