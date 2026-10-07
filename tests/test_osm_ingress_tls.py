"""Narrow existing-installation ingress certificate reconciliation."""
import copy
import json
import unittest
from unittest import mock
import test_reference_installer as fixtures
from runtime_config import ConfigError

class IngressTLSTests(unittest.TestCase):
    def setUp(self):
        self.installer = object.__new__(fixtures.Installer)
        self.installer.cfg = {'OSM_HOST':'https://gui.192.0.2.1.nip.io:30843', 'OSM_BASE_DOMAIN':'192.0.2.1.nip.io','OSM_NAMESPACE':'osm'}
        self.documents = {name:{'metadata':{'generation':1},'spec':{'secretName':name+'-cert','dnsNames':[host], 'usages':['client auth'], 'issuerRef':{'name':'ca-issuer','kind':'Issuer'}},'status':{'conditions':[{'type':'Ready','status':'True','observedGeneration':1}]}} for name,host in [('ngui','192.0.2.1.nip.io'),('nbi','nbi.192.0.2.1.nip.io')]}
        self.installer.kjson = mock.Mock(side_effect=lambda *args:copy.deepcopy(self.documents[args[2]]))
        def patch(*args,**kwargs):
            obj=self.documents[args[2]];obj['spec'].update(json.loads(args[-1])['spec']);obj['metadata']['generation']+=1;obj['status']['conditions'][0]['observedGeneration']=obj['metadata']['generation']
        self.installer.kubectl=mock.Mock(side_effect=patch)

    def test_minimal_correction_and_idempotent_rerun(self):
        self.installer.reconcile_ingress_certificates()
        self.assertEqual(self.installer.kubectl.call_count,2)
        for name in ['ngui','nbi']:
            self.assertIn('server auth',self.documents[name]['spec']['usages'])
            self.assertIn('client auth',self.documents[name]['spec']['usages'])
        self.assertEqual(self.documents['ngui']['spec']['dnsNames'],['gui.192.0.2.1.nip.io'])
        for call in self.installer.kubectl.call_args_list:
            self.assertEqual(set(json.loads(call.args[-1])['spec']),{'dnsNames','usages'})
        self.installer.reconcile_ingress_certificates()
        self.assertEqual(self.installer.kubectl.call_count,2)

    def test_unexpected_issuer_or_secret_rejected_without_mutation(self):
        for field,value in [('secretName','other'),('issuerRef',{'name':'other'})]:
            with self.subTest(field=field):
                original=copy.deepcopy(self.documents['ngui']);self.documents['ngui']['spec'][field]=value
                with self.assertRaises(ConfigError):self.installer.reconcile_ingress_certificates()
                self.installer.kubectl.assert_not_called();self.documents['ngui']=original

    def test_stale_ready_does_not_mask_reissuance_failure(self):
        def stale(*args,**kwargs):self.documents[args[2]]['metadata']['generation']+=1
        self.installer.kubectl.side_effect=stale
        with mock.patch('installer.install.time.sleep'),self.assertRaisesRegex(ConfigError,'current generation'):
            self.installer.reconcile_ingress_certificates()
