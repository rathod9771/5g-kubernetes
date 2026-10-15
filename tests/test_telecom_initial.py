"""Offline NBI entry-point and prebuilt onboarding regression; no live IO."""
import copy
import json
import os
import time
import unittest
from unittest import mock
import yaml
import test_telecom_execution as fixtures
from telecom.initial import InitialDeployment
from telecom.security import Principal, Denied
from telecom.contract import identity
from telecom.pipeline import scoped_catalog

NS='11111111-2222-4333-8444-555555555555'
OP='22222222-2222-4333-8444-555555555555'
UID='33333333-2222-4333-8444-555555555555'

class InitialTests(unittest.TestCase):
    tearDown=fixtures.ExecutionTests.tearDown
    registry=fixtures.ExecutionTests.registry
    complete=fixtures.PipelineTests.complete
    catalog_setup=fixtures.PipelineTests.catalog_setup
    def setUp(self):
        fixtures.PipelineTests.setUp(self)
        publication,approval,job=self.catalog_setup()
        release=self.service.journal.get(self.principal,'releases',job['release_id'])
        scenario=release['package']['scenario']
        from telecom.service import SimulatedCatalog
        class Client(SimulatedCatalog):
            def request(self,method,path,data=None,content_type='application/yaml'):
                if method=='POST':self.pending=('knf',scenario['knf_package']) if 'vnfpkgm' in path else ('ns',scenario['nsd_package'])
                return super().request(method,path,data,content_type)
        self.publication=self.pipeline.publish(self.principal,publication['id'],approval['id'],Client)
        self.target={'scope':'DISPOSABLE_RESEARCH_NAMESPACE','namespace':'research-alice-demo',
                     'namespace_uid':UID,'cluster_uid':NS,'vim_id':OP,'tenant':'alice','enabled':True,
                     'kubeconfig':'/not-a-real-kubeconfig'}
        self.policy.value['deployment_targets']={'alice':self.target}
        self.adapter=InitialDeployment(self.service,self.policy)
        self.item=self.adapter.request(self.principal,self.publication['id'],'initial-request')
        self.approval=self.adapter.approve(self.reviewer,self.item['id'],'initial-approval')
        self.client=mock.Mock()
        self.client.verify.return_value=self.publication['diagnostic']['catalog_ids']
        self.calls=[]
        def nbi(method,path,data=None,*args):
            self.calls.append((method,path,data))
            if method=='GET' and path.endswith('ns_lcm_op_occs'):return b'[]'
            if method=='POST':return json.dumps({'id':NS,'nslcmop_id':OP}).encode()
            if '/ns_instances/' in path:return json.dumps({'_id':NS,'name':self.item['ns_name'],
                'nsd-id':self.publication['diagnostic']['catalog_ids']['ns'],
                'nsState':'READY','operational-status':'running','config-status':'configured'}).encode()
            return json.dumps({'nsInstanceId':NS,'lcmOperationType':'INSTANTIATE','operationState':'COMPLETED'}).encode()
        self.client.request.side_effect=nbi
        self.transport=mock.patch('telecom.initial.scoped_catalog',return_value=self.client)
        self.transport.start();self.addCleanup(self.transport.stop)
        self.snapshot=mock.patch.object(self.adapter,'snapshot',return_value=[])
        self.reader=self.snapshot.start();self.addCleanup(self.snapshot.stop)
    def submit(self):return self.adapter.submit(self.principal,self.item['id'],self.approval['id'])
    def test_prebuilt_to_initial_entrypoint_and_digest_status(self):
        result=self.submit();self.assertEqual(result['state'],'SUBMITTED')
        self.assertEqual([v['state'] for v in result['history']],['AWAITING_APPROVAL','SUBMITTING','SUBMITTED'])
        posts=[c for c in self.calls if c[0]=='POST'];self.assertEqual(len(posts),1)
        payload=yaml.safe_load(posts[0][2]);self.assertTrue(posts[0][1].endswith('ns_instances_content'))
        self.assertEqual(payload['additionalParamsForVnf'][0]['additionalParamsForKdu'][0]['k8s-namespace'],self.target['namespace'])
        release=self.service.journal.get(self.principal,'releases',self.item['release_id'])
        self.reader.return_value=[{'metadata':{'namespace':self.target['namespace']},
          'spec':{'containers':[{'image':release['image']['repository']+'@'+release['image']['digest']}]},
          'status':{'containerStatuses':[{'ready':True,'imageID':'docker-pullable://registry.example/nf@'+release['image']['digest']}]}}]
        value=self.adapter.refresh(self.principal,self.item['id'],'observe')
        self.assertEqual(value['state'],'WORKLOAD_READY');self.assertEqual(value['observation']['application_health'],'UNVERIFIED')
        self.assertFalse(value['production_eligible']);self.assertTrue(self.service.journal.verify_audit())
    def test_no_retry_or_second_namespace_submission(self):
        self.submit()
        with self.assertRaises(Denied):self.submit()
        other=self.adapter.request(self.principal,self.publication['id'],'second-request')
        approval=self.adapter.approve(self.reviewer,other['id'],'second-approval')
        with self.assertRaises(Denied):self.adapter.submit(self.principal,other['id'],approval['id'])
        self.assertEqual(sum(c[0]=='POST' for c in self.calls),1)
    def test_unknown_result_sanitized_and_durable(self):
        self.client.request.side_effect=[b'[]',RuntimeError('secret-password')]
        value=self.submit();self.assertEqual(value['state'],'UNKNOWN_OUTCOME')
        self.assertNotIn('secret-password',json.dumps(value))
        with self.assertRaises(Denied):self.submit()
    def test_pending_operation_blocks_create(self):
        self.client.request.side_effect=lambda *a:b'[{"operationState":"PROCESSING"}]'
        with self.assertRaises(Denied):self.submit()
        self.assertEqual(self.client.request.call_count,1)
    def test_namespace_not_empty_blocks(self):
        self.reader.return_value=[{'unrelated':'workload'}]
        with self.assertRaises(Denied):self.submit()
        self.client.request.assert_not_called()
    def test_missing_approval_and_cross_tenant(self):
        with self.assertRaises(Denied):self.adapter.submit(self.principal,self.item['id'],'missing')
        other=Principal('researcher','other',self.principal.roles)
        with self.assertRaises(Denied):self.adapter.request(other,self.publication['id'],'cross')
        with self.assertRaises(Denied):self.adapter.approve(self.principal,self.item['id'],'self')
    def test_expired_approval_and_target_drift(self):
        with mock.patch('telecom.initial.time.time',return_value=time.time()+400):
            with self.assertRaises(Denied):self.submit()
        self.target['vim_id']=UID
        with self.assertRaises(Denied):self.submit()
        self.client.request.assert_not_called()
    def test_disabled_target_and_protected_namespace(self):
        self.target['enabled']=False
        with self.assertRaises(Denied):self.submit()
        self.target['namespace']='default'
        with self.assertRaises(Denied):self.adapter.request(self.principal,self.publication['id'],'default')
    def test_catalog_identity_drift(self):
        self.client.verify.return_value={'knf':UID,'ns':UID}
        with self.assertRaises(Denied):self.submit()
        self.client.request.assert_not_called()
    def test_missing_or_wrong_image_is_not_healthy(self):
        self.submit();self.reader.return_value=[{'status':{}}]
        value=self.adapter.refresh(self.principal,self.item['id'],'missing')
        self.assertEqual(value['state'],'VERIFYING');self.assertFalse(value['observation']['image_digest_verified'])
    def test_status_error_is_unavailable_and_never_retries(self):
        self.submit();self.client.request.side_effect=RuntimeError('private-detail')
        value=self.adapter.refresh(self.principal,self.item['id'],'error')
        self.assertEqual(value['state'],'UNAVAILABLE');self.assertNotIn('private-detail',json.dumps(value))
    def test_scoped_existing_authentication(self):
        target=dict(self.policy.value['catalog_targets']['alice'],project_id=UID)
        client=mock.Mock();client.request.return_value=json.dumps({'_id':UID,'name':'research-alice'}).encode()
        with mock.patch('telecom.pipeline.Catalog',return_value=client),mock.patch.object(self.policy,'secret',return_value=json.dumps({'username':'alice-research','password':'synthetic-offline-only-value'})):
            self.assertIs(scoped_catalog(self.policy,self.principal,target),client)
            client.authenticate.assert_called_once_with('alice-research','synthetic-offline-only-value',UID)
    def test_research_server_routes_registration_history_and_initial_approval(self):
        import hashlib
        import importlib.util
        from pathlib import Path
        module_path=fixtures.ROOT/'docs/telecom/p6/research_only.py'
        spec=importlib.util.spec_from_file_location('p8_research_server',module_path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        auth=self.root/'auth.json';credentials=[]
        for who,token in ((self.principal,'offline-user-token-not-a-production-credential'),(self.reviewer,'offline-reviewer-token-not-a-production-credential'),
                          (Principal('stranger','other',self.principal.roles),'offline-other-token-not-a-production-credential')):
            credentials.append({'subject':who.subject,'tenant':who.tenant,'roles':sorted(who.roles),
                                'sha256':hashlib.sha256(token.encode()).hexdigest()})
        auth.write_text(json.dumps(credentials));auth.chmod(0o600)
        env={'TELECOM_AUTH_FILE':str(auth),'TELECOM_RUNTIME_DIR':str(self.service.journal.directory),
             'TELECOM_WORKSPACE':str(self.root),'HELM_BIN':fixtures.HELM}
        with mock.patch.dict(os.environ,env),mock.patch('telecom.execution.Policy.environment',return_value=self.policy),mock.patch('telecom.initial.InitialDeployment.snapshot',return_value=[]):
            client=module.create_app().test_client()
            for route in ('/','/telecom','/telecom.js'):
                with client.get(route) as response:self.assertEqual(response.status_code,200)
            user={'Authorization':'Bearer offline-user-token-not-a-production-credential'};review={'Authorization':'Bearer offline-reviewer-token-not-a-production-credential'}
            other={'Authorization':'Bearer offline-other-token-not-a-production-credential'}
            for kind in ('workloads','capabilities','pipeline-jobs','releases','publication-requests','deployments'):
                self.assertEqual(client.get('/api/telecom/'+kind,headers=user).status_code,200)
            item=client.post('/api/telecom/deployment-requests',headers=user,
                json={'publication_id':self.publication['id'],'event_id':'http-initial'}).json
            denied=client.post('/api/telecom/deployment-approvals',headers=user,
                json={'request_id':item['id'],'event_id':'http-self'})
            self.assertEqual(denied.status_code,403)
            approved=client.post('/api/telecom/deployment-approvals',headers=review,
                json={'request_id':item['id'],'event_id':'http-approve'})
            self.assertEqual(approved.status_code,200,approved.json)
            cross=client.post('/api/telecom/initial-deployments',headers=other,
                json={'request_id':item['id'],'approval_id':approved.json['id']})
            self.assertEqual(cross.status_code,403)
            sent=client.post('/api/telecom/initial-deployments',headers=user,
                json={'request_id':item['id'],'approval_id':approved.json['id']})
            self.assertEqual(sent.status_code,200,sent.json);self.assertEqual(sent.json['state'],'SUBMITTED')
            self.assertEqual(client.get('/api/telecom/deployments',headers=other).json['items'],[])
            self.assertEqual(client.post('/api/telecom/deployment-requests',headers=user,
                json={'publication_id':self.publication['id'],'event_id':'extra','namespace':'default'}).status_code,409)

    def test_prebuilt_pipeline_never_builds_or_pushes(self):
        with mock.patch('telecom.pipeline.build',side_effect=AssertionError('BuildKit forbidden')),mock.patch('telecom.registry.Publisher.publish',side_effect=AssertionError('push forbidden')):
            job=self.pipeline.queue(self.principal,self.workload['id'],'chart','prebuilt-again')
            _,transport=self.registry()
            with mock.patch('urllib.request.OpenerDirector.open',side_effect=transport):result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'COMPLETED',result)

class BoundaryTests(unittest.TestCase):
    def test_harmless_application_health_offline(self):
        import importlib.util
        import threading
        import urllib.request
        from http.server import HTTPServer
        path=fixtures.ROOT/'examples/telecom/disposable/app.py'
        spec=importlib.util.spec_from_file_location('harmless_http_fixture',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        server=HTTPServer(('127.0.0.1',0),module.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:'+str(server.server_port)+'/healthz',timeout=2) as response:
                self.assertEqual(response.status,200);self.assertIn(b'harmless',response.read(128))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_new_routes_require_auth_and_schema(self):
        from flask import Flask
        from telecom.api import register
        app=Flask('p8-boundary');register(app);client=app.test_client()
        for route in ('deployment-requests','deployment-approvals','initial-deployments','deployment-observations'):
            self.assertEqual(client.post('/api/telecom/'+route,json={}).status_code,403)
        self.assertEqual(client.get('/api/telecom/deployments').status_code,403)
    def test_research_plan_has_no_installation_side_effect(self):
        from telecom.research_setup import plan
        value=plan();self.assertFalse(value['automatic_installation'])
        self.assertFalse(value['reference_workloads']);self.assertEqual(value['execution'],'PLAN_ONLY')
    def test_untrusted_kubeconfig_plugin_rejected_before_process(self):
        import tempfile
        from pathlib import Path
        from telecom.execution import Policy
        from telecom.service import Service
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'config.json'
            config={'apiVersion':'v1','kind':'Config','current-context':'a',
                'clusters':[{'name':'a','cluster':{'server':'https://cluster.example','certificate-authority-data':'fixture'}}],
                'contexts':[{'name':'a','context':{'cluster':'a','user':'a'}}],
                'users':[{'name':'a','user':{'exec':{'command':'dangerous'}}}]}
            path.write_text(json.dumps(config));path.chmod(0o600)
            adapter=InitialDeployment(Service(root/'runtime',root),Policy({}))
            with mock.patch('telecom.initial.command') as command,self.assertRaises(Denied):
                adapter.snapshot({'kubeconfig':str(path),'namespace':'research-test'})
            command.assert_not_called()
    def test_readonly_namespace_uid_binding_and_command_budget(self):
        from telecom.execution import Policy
        adapter=object.__new__(InitialDeployment)
        target={'cluster_uid':NS,'namespace_uid':UID,'namespace':'research-alice','tenant':'alice'}
        replies=[{'metadata':{'uid':NS}},{'metadata':{'uid':UID,'labels':{'telecom.research/tenant':'alice'}}},{'items':[]}]
        with mock.patch('telecom.initial.command',side_effect=[json.dumps(v).encode() for v in replies]) as command:
            self.assertEqual(adapter.read_snapshot(target,'/private/config'),[])
            self.assertEqual(command.call_count,3)
            for call in command.call_args_list:
                self.assertIn('get',call.args[0]);self.assertEqual(call.kwargs['timeout'],7)
                self.assertEqual(call.kwargs['limit'],2*1024*1024)
        with mock.patch('telecom.initial.command',side_effect=[json.dumps(v).encode() for v in replies[:2]]+[b'{"items":[{"kind":"Deployment"}]}']) as command,self.assertRaises(Denied):
            adapter.read_snapshot(target,'/private/config',empty=True)
        self.assertEqual(command.call_count,3)
        replies[1]['metadata']['uid']=NS
        with mock.patch('telecom.initial.command',side_effect=[json.dumps(v).encode() for v in replies]),self.assertRaises(Denied):
            adapter.read_snapshot(target,'/private/config')

if __name__=='__main__':unittest.main()
