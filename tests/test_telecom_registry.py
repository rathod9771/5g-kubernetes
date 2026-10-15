"""Offline Skopeo/worker transport doubles. No daemon, registry or OSM writes."""
import base64
import hashlib
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
import unittest
from unittest import mock
import test_telecom_execution as fixtures
from test_telecom_execution import fixture_image
from telecom.execution import command,ExecutionError,verify_worker,build,oci_layout
from telecom.registry import Publisher,Traffic
from telecom.security import Principal,Denied
from telecom.contract import identity

class RegistryTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.ExecutionTests.tearDown
    registry=fixtures.ExecutionTests.registry

    def configure(self):
        self.data,self.image_digest,self.files,self.raw=fixture_image()
        self.archive=self.root/'image.tar';self.archive.write_bytes(self.data)
        secret=self.root/'registry-secret.json';secret.write_text(json.dumps({'value':json.dumps({'username':'researcher','password':'offline-registry-fixture-value-'+('x'*20)})}));secret.chmod(0o600)
        self.policy.value['secrets']={'publish':{'tenant':'alice','purpose':'registry-publish','resources':['registry.example/nf'],'file':str(secret)}}
        self.target={'scope':'DISPOSABLE_RESEARCH_REPOSITORY','enabled':True,'authentication_required':True,'repositories':['registry.example/nf'],
            'secret_ref':'publish','max_seconds':30,'max_requests':64,'client_version':'skopeo version 1.21.0','transport_evidence':'OFFLINE_FIXTURE_NOT_LIVE_CERTIFICATION'}
        self.policy.value['registry_targets']={'alice':self.target}
        self.publisher=Publisher(self.service.journal,self.policy);self.calls=[];self.remote=None;self.auth_paths=[]

    def client(self,argv,**kwargs):
        self.calls.append(argv)
        if argv==['skopeo','--version']:return b'skopeo version 1.21.0\n'
        self.assertEqual(argv[0],'skopeo');self.assertIn('--debug',argv)
        self.assertTrue(all('offline-registry-fixture-value' not in x for x in argv))
        self.assertNotIn('OSM_PASSWORD',kwargs.get('extra_env',{}));self.assertNotIn('--tls-verify=false',argv)
        auth=Path(argv[argv.index('--dest-authfile')+1] if 'copy' in argv else argv[argv.index('--authfile')+1]);self.auth_paths.append(auth)
        self.assertEqual(auth.stat().st_mode&0o777,0o600)
        content=json.loads(auth.read_text());self.assertEqual(set(content['auths']),{'registry.example/nf'})
        self.assertTrue(base64.b64decode(content['auths']['registry.example/nf']['auth']).startswith(b'researcher:'))
        self.assertEqual(argv[-1],'docker://registry.example/nf@'+self.image_digest)
        if 'copy' in argv:
            self.assertIn('--dest-tls-verify=true',argv);self.assertIn('--preserve-digests',argv)
            self.assertEqual(argv[argv.index('--retry-times')+1],'0');self.assertEqual(argv[argv.index('--image-parallel-copies')+1],'1')
            kwargs['observe']('stderr',b'level=debug msg="HEAD https://registry.example/v2/nf/blobs/digest"\n')
            kwargs['observe']('stderr',b'level=debug msg="PUT https://registry.example/v2/nf/manifests/digest"\n')
            self.remote=self.raw;return b''
        self.assertIn('--tls-verify=true',argv)
        kwargs['observe']('stderr',b'level=debug msg="GET https://registry.example/v2/nf/manifests/digest"\n')
        return self.remote

    def push(self,cancel=lambda:False):
        return self.publisher.publish(self.principal,'registry.example/nf',self.archive,self.image_digest,cancel)

    def test_push_readback_and_private_credentials_cleanup(self):
        self.configure()
        with mock.patch('telecom.registry.command',side_effect=self.client):receipt=self.push()
        self.assertEqual(receipt['state'],'VERIFIED');self.assertEqual(receipt['digest'],self.image_digest)
        self.assertEqual(receipt['network_requests'],3);self.assertTrue(all(not p.exists() for p in self.auth_paths))
        self.assertTrue(self.service.journal.verify_audit())

    def test_duplicate_push_only_rechecks_manifest(self):
        self.configure()
        with mock.patch('telecom.registry.command',side_effect=self.client):first=self.push();second=self.push()
        self.assertEqual(first['id'],second['id']);self.assertEqual(sum('copy' in c for c in self.calls),1)
        self.assertEqual(second['network_requests'],1);self.assertTrue(second['reused_verified_publication'])

    def test_invalid_credentials_fail_closed_without_retry(self):
        self.configure()
        def failure(argv,**kw):
            if argv==['skopeo','--version']:return self.client(argv,**kw)
            kw['observe']('stderr',b'GET https://registry.example/v2/\nunauthorized: sensitive-string\n')
            raise ExecutionError('command','NONZERO_EXIT',1)
        with mock.patch('telecom.registry.command',side_effect=failure):
            with self.assertRaises(ExecutionError) as caught:self.push()
            self.assertEqual(caught.exception.code,'REGISTRY_AUTHENTICATION_FAILED')
            with self.assertRaises(Denied):self.push()
        value=self.service.journal.list(self.principal,'registry-publications')[0]
        self.assertEqual(value['state'],'UNKNOWN_OUTCOME');self.assertNotIn('sensitive-string',json.dumps(value))

    def test_tls_failure_never_uses_insecure_fallback(self):
        self.configure()
        def failure(argv,**kw):
            if argv==['skopeo','--version']:return self.client(argv,**kw)
            self.assertIn('--dest-tls-verify=true',argv)
            kw['observe']('stderr',b'x509: certificate signed by unknown authority\n')
            raise ExecutionError('command','NONZERO_EXIT',1)
        with mock.patch('telecom.registry.command',side_effect=failure),self.assertRaises(ExecutionError) as caught:self.push()
        self.assertEqual(caught.exception.code,'REGISTRY_TLS_VERIFICATION_FAILED')

    def test_registry_digest_mismatch_blocks_promotion(self):
        self.configure()
        def corrupt(argv,**kw):
            value=self.client(argv,**kw)
            return b'corrupt manifest' if 'inspect' in argv else value
        with mock.patch('telecom.registry.command',side_effect=corrupt),self.assertRaises(ExecutionError) as caught:self.push()
        self.assertEqual(caught.exception.code,'REGISTRY_DIGEST_MISMATCH')
        self.assertEqual(self.service.journal.list(self.principal,'registry-publications')[0]['state'],'UNKNOWN_OUTCOME')

    def test_upload_interruption_and_cancel_preserve_uncertainty(self):
        self.configure();cancelled=False
        def cancel_after_copy(argv,**kw):
            nonlocal cancelled
            value=self.client(argv,**kw)
            if 'copy' in argv:cancelled=True
            return value
        with mock.patch('telecom.registry.command',side_effect=cancel_after_copy),self.assertRaises(ExecutionError) as caught:self.push(lambda:cancelled)
        self.assertEqual(caught.exception.code,'CANCELLED')
        self.assertTrue(self.service.journal.list(self.principal,'registry-publications')[0]['cancelled'])
        self.assertTrue(all(not p.exists() for p in self.auth_paths))

    def test_network_watchdog_unknown_trace_and_output_budget(self):
        self.configure();self.target['max_requests']=1
        with mock.patch('telecom.registry.command',side_effect=self.client),self.assertRaises(ExecutionError) as caught:self.push()
        self.assertEqual(caught.exception.code,'NETWORK_REQUEST_BUDGET')
        traffic=Traffic(2)
        with self.assertRaises(ExecutionError):traffic.observe('stderr',b'GET http://registry.example/v2/\n')
        with self.assertRaises(ExecutionError):Traffic(2).observe('stderr',b'x'*65537)

    def test_actual_command_request_watchdog_terminates_process(self):
        traffic=Traffic(1)
        program='import sys,time;sys.stderr.write("GET https://registry.example/a\\nGET https://registry.example/b\\n");sys.stderr.flush();time.sleep(20)'
        with self.assertRaises(ExecutionError) as caught:command([sys.executable,'-c',program],timeout=2,observe=traffic.observe)
        self.assertEqual(caught.exception.code,'NETWORK_REQUEST_BUDGET')

    def test_missing_client_never_falls_back_to_docker(self):
        self.configure()
        with mock.patch('telecom.registry.command',side_effect=FileNotFoundError('skopeo')) as runner,self.assertRaises(FileNotFoundError):self.push()
        self.assertEqual(runner.call_args.args[0],['skopeo','--version'])
        self.assertEqual(self.service.journal.list(self.principal,'registry-publications'),[])

    def test_version_mismatch_and_missing_accounting_are_not_success(self):
        self.configure()
        with mock.patch('telecom.registry.command',return_value=b'skopeo version 0.0.0'),self.assertRaises(Denied):self.push()
        def missing_trace(argv,**kw):return b'skopeo version 1.21.0' if '--version' in argv else b''
        with mock.patch('telecom.registry.command',side_effect=missing_trace),self.assertRaises(ExecutionError) as caught:self.push()
        self.assertEqual(caught.exception.code,'UNVERIFIED_REQUEST_ACCOUNTING')

    def test_tenant_repository_and_secret_scopes(self):
        self.configure();foreign=Principal('researcher','other',self.principal.roles)
        for actor,repo in [(foreign,'registry.example/nf'),(self.principal,'registry.example/other')]:
            with self.assertRaises(Denied):self.publisher.configuration(actor,repo)
        self.policy.value['secrets']['publish']['purpose']='osm-credentials'
        with self.assertRaises(Denied):self.push()

    def test_invalid_image_fails_before_client_invocation(self):
        self.configure();self.archive.write_bytes(b'invalid image')
        with mock.patch('telecom.registry.command') as runner,self.assertRaises(Exception):self.push()
        runner.assert_not_called()

    def test_full_git_build_push_readback_package_release_request(self):
        self.configure();self.policy.value['build_worker']={'tenant':'alice'}
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','p7-end-to-end',image_mode='dockerfile')
        def builder(source,output,policy,tenant,cancel):
            self.assertTrue((Path(source)/'Dockerfile').is_file());Path(output).write_bytes(self.data)
            return dict(oci_layout(self.data),builder='REMOTE_BUILDKIT_TLS',worker_identity={'verification':'OFFLINE_TRANSPORT_DOUBLE'})
        with mock.patch('telecom.pipeline.build',side_effect=builder),mock.patch('telecom.registry.command',side_effect=self.client):
            result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'COMPLETED',result)
        release=self.service.journal.get(self.principal,'releases',result['release_id'])
        self.assertEqual(release['image_verification']['registry_publication'],'REGISTRY_MANIFEST_VERIFIED')
        states=[v['state'] for v in result['history']]
        for state in ('ACQUIRING','BUILDING','ARTIFACT_VERIFIED','PUBLISHING_IMAGE','REGISTRY_VERIFIED','VALIDATING_PACKAGE','PACKAGE_VALIDATED','COMPLETED'):self.assertIn(state,states)
        self.policy.value['catalog_targets']={'alice':{'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':False,'project':'research-alice','host':'https://osm.example'}}
        request=self.pipeline.request(self.principal,release['id'],'catalog','p7-request')
        self.assertEqual(request['state'],'AWAITING_APPROVAL')
        with self.assertRaises(Denied):self.pipeline.publish(self.principal,request['id'],'missing')
        self.assertTrue(self.service.journal.verify_audit());self.assertFalse(release['production_eligible'])

    def test_failed_registry_stage_produces_no_release(self):
        self.configure();job=self.pipeline.queue(self.principal,self.workload['id'],'chart','p7-failed',image_mode='dockerfile')
        def builder(source,output,*args):Path(output).write_bytes(self.data);return dict(oci_layout(self.data),builder='REMOTE_BUILDKIT_TLS')
        with mock.patch('telecom.pipeline.build',side_effect=builder),mock.patch('telecom.registry.command',side_effect=ExecutionError('registry','TIMEOUT')):
            result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'FAILED');self.assertEqual(result['diagnostic']['code'],'TIMEOUT')
        self.assertEqual(self.service.journal.list(self.principal,'releases'),[])

    def test_build_unavailable_timeout_and_cancel_block_release(self):
        for reason in ('WORKER_TLS_OR_AVAILABILITY_FAILURE','TIMEOUT','CANCELLED'):
            self.configure();job=self.pipeline.queue(self.principal,self.workload['id'],'chart','p7-'+reason,image_mode='dockerfile')
            with mock.patch('telecom.pipeline.build',side_effect=ExecutionError('build',reason)):
                result=self.pipeline.run(self.principal,job['id'])
            self.assertEqual(result['state'],'CANCELLED' if reason=='CANCELLED' else 'FAILED')
        self.assertEqual(self.service.journal.list(self.principal,'releases'),[])

    def test_old_unpublished_build_cannot_request_catalog(self):
        self.configure();job=fixtures.PipelineTests.complete(self)
        release=self.service.journal.get(self.principal,'releases',job['release_id'])
        release['image_verification'].update(builder='REMOTE_BUILDKIT_TLS',registry_publication='NOT_RUN')
        with self.service.journal.transaction() as db:self.service.journal.put(db,self.principal,'releases',release['id'],release)
        with self.assertRaises(Denied):self.pipeline.request(self.principal,release['id'],'catalog','p7-deny-unpublished')

    def test_nondefault_osm_nbi_scope_without_osm_cli(self):
        # Real coordinator branch uses the existing NBI Catalog API, not factory or CLI.
        self.configure();job=fixtures.PipelineTests.complete(self)
        target={'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':True,'project':'research-alice','host':'https://osm.example',
                'project_id':'d9c958fd-25f7-4856-a93b-2dc30c52349d','secret_ref':'osm'}
        self.policy.value['catalog_targets']={'alice':target}
        secret=self.root/'osm-reference.json';secret.write_text(json.dumps({'value':json.dumps({'username':'research-alice-user','password':'offline-scoped-fixture-value-123456789'})}));secret.chmod(0o600)
        self.policy.value['secrets']['osm']={'tenant':'alice','purpose':'osm-credentials','resources':['https://osm.example#research-alice'],'file':str(secret)}
        request=self.pipeline.request(self.principal,job['release_id'],'catalog','nbi-request')
        approval=self.pipeline.approve(self.reviewer,request['id'],'nbi-approve')
        release=self.service.journal.get(self.principal,'releases',job['release_id']);scenario=release['package']['scenario']
        client=mock.Mock();client.request.return_value=json.dumps({'_id':target['project_id'],'name':'research-alice'}).encode();client.lookup.return_value=None;client.verify.return_value={'knf':'offline','ns':'offline'}
        with mock.patch('telecom.pipeline.Catalog',return_value=client),mock.patch.dict(sys.modules,{'osm_im':None}):
            result=self.pipeline.publish(self.principal,request['id'],approval['id'])
        self.assertEqual(result['state'],'PUBLISHED_CONTENT_VERIFIED',result)
        self.assertEqual(client.authenticate.call_args.args[2],target['project_id'])
        self.assertEqual(client.request.call_args_list[0].args,('GET','/admin/v1/projects/'+target['project_id']))
        self.assertEqual(sum(call.args[0]=='POST' for call in client.request.call_args_list),2)

    def test_default_osm_identity_rejected_before_authentication(self):
        self.configure();job=fixtures.PipelineTests.complete(self)
        target={'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':True,'project':'research-alice','host':'https://osm.example','secret_ref':'osm'}
        self.policy.value['catalog_targets']={'alice':target}
        secret=self.root/'osm-default.json';secret.write_text(json.dumps({'value':json.dumps({'username':'admin','password':'offline-credential-fixture-that-is-not-used'})}));secret.chmod(0o600)
        self.policy.value['secrets']['osm']={'tenant':'alice','purpose':'osm-credentials','resources':['https://osm.example#research-alice'],'file':str(secret)}
        request=self.pipeline.request(self.principal,job['release_id'],'catalog','default-request');approval=self.pipeline.approve(self.reviewer,request['id'],'default-approve')
        client=mock.Mock()
        with mock.patch('telecom.pipeline.Catalog',return_value=client):result=self.pipeline.publish(self.principal,request['id'],approval['id'])
        self.assertEqual(result['state'],'UNKNOWN_OUTCOME');client.authenticate.assert_not_called();client.request.assert_not_called()

    def test_expired_registry_receipt_blocks_catalog_request(self):
        self.configure()
        release={'image':{'repository':'registry.example/nf','digest':self.image_digest},'image_verification':{'builder':'REMOTE_BUILDKIT_TLS',
            'registry_publication':'REGISTRY_MANIFEST_VERIFIED','registry':{'receipt_id':'expired'}}}
        with self.service.journal.transaction() as db:self.service.journal.put(db,self.principal,'registry-publications','expired',
            {'state':'VERIFIED','repository':'registry.example/nf','digest':self.image_digest,'verified_at':time.time()-301,'policy_hash':identity(self.target)})
        with self.assertRaises(Denied):self.pipeline.registry_gate(self.principal,release)

    def test_pax_metadata_bomb_rejected_before_tar_parser(self):
        import tarfile,io
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT) as archive:
            item=tarfile.TarInfo('index.json');item.pax_headers={'comment':'extension'};item.size=2;archive.addfile(item,io.BytesIO(b'{}'))
        with self.assertRaises(ExecutionError) as caught:oci_layout(stream.getvalue())
        self.assertEqual(caught.exception.code,'UNSUPPORTED_TAR_EXTENSION')

    def test_registry_authorization_drift_blocks_existing_release(self):
        self.configure()
        with mock.patch('telecom.registry.command',side_effect=self.client):receipt=self.push()
        release={'image':{'repository':'registry.example/nf','digest':self.image_digest},'image_verification':{'builder':'REMOTE_BUILDKIT_TLS',
            'registry_publication':'REGISTRY_MANIFEST_VERIFIED','registry':{'receipt_id':receipt['id']}}}
        self.pipeline.registry_gate(self.principal,release)
        self.target['enabled']=False
        with self.assertRaises(Denied):self.pipeline.registry_gate(self.principal,release)

    def test_registry_api_authentication_and_tenant_filtering(self):
        import os
        from flask import Flask
        from telecom.api import register
        self.configure()
        with mock.patch('telecom.registry.command',side_effect=self.client):self.push()
        auth=self.root/'view-auth.json';tokens={'alice':'offline-alice-token-'+('a'*40),'other':'offline-other-token-'+('b'*40)}
        auth.write_text(json.dumps([{'subject':'reader','tenant':tenant,'roles':['reader'],'sha256':hashlib.sha256(token.encode()).hexdigest()} for tenant,token in tokens.items()]));auth.chmod(0o600)
        app=Flask('p7-registry-api');register(app)
        with mock.patch.dict(os.environ,{'TELECOM_AUTH_FILE':str(auth),'TELECOM_RUNTIME_DIR':str(self.service.journal.directory),'TELECOM_WORKSPACE':str(self.root)}):
            client=app.test_client();path='/api/telecom/registry-publications'
            self.assertEqual(client.get(path).status_code,403)
            mine=client.get(path,headers={'Authorization':'Bearer '+tokens['alice']})
            foreign=client.get(path,headers={'Authorization':'Bearer '+tokens['other']})
            self.assertEqual(mine.status_code,200);self.assertEqual(len(mine.json['items']),1)
            self.assertEqual(foreign.status_code,200);self.assertEqual(foreign.json['items'],[])
            self.assertNotIn('offline-registry-fixture-value',mine.get_data(as_text=True))

    def test_archive_directory_traversal_rejected_before_skopeo(self):
        import tarfile,io
        self.configure();archive=io.BytesIO(self.data)
        with tarfile.open(fileobj=archive,mode='a') as stream:
            directory=tarfile.TarInfo('../escape');directory.type=tarfile.DIRTYPE;stream.addfile(directory)
        self.archive.write_bytes(archive.getvalue())
        with mock.patch('telecom.registry.command') as runner,self.assertRaises(ExecutionError):self.push()
        runner.assert_not_called()

    def test_bounded_pax_timestamp_export_is_supported(self):
        import tarfile,io
        self.configure();stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT) as archive:
            for name,data in self.files.items():
                member=tarfile.TarInfo(name);member.size=len(data);member.pax_headers={'mtime':'0.123456789'}
                archive.addfile(member,io.BytesIO(data))
        self.archive.write_bytes(stream.getvalue())
        with mock.patch('telecom.registry.command',side_effect=self.client):receipt=self.push()
        self.assertEqual(receipt['state'],'VERIFIED')

    def test_short_names_cannot_normalize_to_an_unapproved_registry(self):
        self.configure()
        for repository in ('registry/nf','registry.example/nf/','registry.example//nf'):
            self.target['repositories']=[repository]
            self.policy.value['secrets']['publish']['resources']=[repository]
            with self.assertRaises(Denied):self.publisher.configuration(self.principal,repository)

class WorkerIdentityTests(unittest.TestCase):
    def worker(self):return {'address':'tcp://worker.example:1234','certificate_sha256':hashlib.sha256(b'certificate').hexdigest(),'dedicated_ca':True,
        'allowed_ips':['192.0.2.20'],'denied_cidrs':['10.0.0.0/8','127.0.0.0/8'],'ca':'ca','cert':'cert','key':'key','quota_evidence':'offline'}
    def test_mtls_peer_identity_and_pinned_ip(self):
        worker=self.worker();ctx=mock.MagicMock();sock=mock.MagicMock();tls=ctx.wrap_socket.return_value.__enter__.return_value;tls.getpeercert.return_value=b'certificate'
        with mock.patch('ssl.create_default_context',return_value=ctx),mock.patch('socket.create_connection',return_value=sock) as connection:
            result=verify_worker(worker)
        connection.assert_called_once_with(('192.0.2.20',1234),timeout=3)
        self.assertEqual(result['pinned_address'],'tcp://192.0.2.20:1234');self.assertEqual(ctx.load_cert_chain.call_args.args,('cert','key'));self.assertTrue(callable(ctx.load_cert_chain.call_args.kwargs['password']))
    def test_wrong_certificate_and_protected_addresses(self):
        worker=self.worker();worker['allowed_ips']=['127.0.0.2']
        with self.assertRaises(Denied):verify_worker(worker)
        worker=self.worker();worker['allowed_ips']=['10.0.0.3']
        with self.assertRaises(Denied):verify_worker(worker)
        worker=self.worker();ctx=mock.MagicMock();ctx.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value=b'wrong'
        with mock.patch('ssl.create_default_context',return_value=ctx),mock.patch('socket.create_connection',return_value=mock.MagicMock()),self.assertRaises(Denied):verify_worker(worker)
    def test_unavailable_worker_and_untrusted_ca(self):
        with mock.patch('ssl.create_default_context',side_effect=ssl.SSLError('private detail')),self.assertRaises(ExecutionError) as caught:verify_worker(self.worker())
        self.assertEqual(caught.exception.code,'WORKER_TLS_OR_AVAILABILITY_FAILURE');self.assertNotIn('private detail',str(caught.exception))

if __name__=='__main__':unittest.main()
