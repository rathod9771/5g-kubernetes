"""Offline P5 entry-point and failure tests; never connect to a live backend."""
import copy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from telecom.execution import (Policy,ExecutionError,acquire,unpack,oci_layout,prebuilt,build,command,verify_webhook,helm_source,MAX_SOURCE)
from telecom.pipeline import Pipeline
from telecom.service import Service,SimulatedCatalog
from telecom.security import Principal,Denied
from telecom.contract import identity
from assurance.locking import exclusive

HELM='/usr/local/bin/helm.real' if Path('/usr/local/bin/helm.real').exists() else 'helm'

def fixture_image():
    blobs={}
    def descriptor(content,media):
        digest='sha256:'+hashlib.sha256(content).hexdigest();blobs['blobs/sha256/'+digest.split(':')[1]]=content
        return {'digest':digest,'size':len(content),'mediaType':media}
    layer_tar=io.BytesIO()
    with tarfile.open(fileobj=layer_tar,mode='w') as archive:
        entry=tarfile.TarInfo('research-fixture.txt');content=b'offline fixture layer';entry.size=len(content);archive.addfile(entry,io.BytesIO(content))
    layer=descriptor(layer_tar.getvalue(),'application/vnd.oci.image.layer.v1.tar')
    config=descriptor(json.dumps({'os':'linux','architecture':'amd64','rootfs':{'type':'layers','diff_ids':[layer['digest']]}}).encode(),'application/vnd.oci.image.config.v1+json')
    manifest={'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','config':config,'layers':[layer]}
    raw=json.dumps(manifest).encode();desc=descriptor(raw,manifest['mediaType'])
    files=dict(blobs,**{'index.json':json.dumps({'schemaVersion':2,'manifests':[desc]}).encode(),'oci-layout':b'{"imageLayoutVersion":"1.0.0"}'})
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as archive:
        for name,data in files.items():
            info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
    return out.getvalue(),desc['digest'],files,raw

class FixtureResponse(io.BytesIO): pass

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        subprocess.run(['git','init','--template=',str(self.repo)],check=True,capture_output=True)
        shutil.copytree(ROOT/'examples/telecom/ran-chart',self.repo/'chart')
        (self.repo/'Dockerfile').write_text('FROM scratch\nLABEL research.example="offline"\n')
        for args in (['add','.'],['-c','user.name=Offline','-c','user.email=offline@example.invalid','commit','-m','fixture']):
            subprocess.run(['git','-C',str(self.repo)]+args,check=True,capture_output=True)
        self.sha=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD']).decode().strip()
        self.source={'kind':'local-git','path':'repo','commit':self.sha};self.policy=Policy({'local_roots':{'alice':'.'}})
    def tearDown(self): self.tmp.cleanup()
    def test_real_git_pinned_acquisition_and_hooks_not_executed(self):
        (self.repo/'.git/hooks').mkdir(exist_ok=True)
        (self.repo/'.git/hooks/post-checkout').write_text('#!/bin/sh\ntouch '+str(self.root/'BAD'))
        (self.repo/'.git/hooks/post-checkout').chmod(0o700)
        subprocess.run(['git','-C',str(self.repo),'config','core.fsmonitor','touch '+str(self.root/'BAD')],check=True)
        result=acquire(self.source,self.root,self.root/'captured',self.policy)
        self.assertEqual(result['commit'],self.sha);self.assertTrue((self.root/'captured/Dockerfile').is_file());self.assertFalse((self.root/'BAD').exists())
    def test_source_paths_and_protocols_fail_closed(self):
        for source in [dict(self.source,path='../escape'),dict(self.source,commit='main'),{'kind':'github','url':'file:///etc','commit':self.sha}]:
            with self.subTest(source=source),self.assertRaises((Denied,ValueError)):
                acquire(source,self.root,self.root/'captured',self.policy)
        for url in ('https://evil.invalid/a/b','ssh://github.com/a/b','https://token@github.com/a/b'):
            with self.assertRaises(Denied):self.policy.repository(url)
    def test_github_fetch_entry_path_with_offline_transport(self):
        from telecom import execution
        original=execution.command
        def transport(argv,**kw):
            if 'fetch' in argv:
                # Fixtures supply Git objects, not a network fetch. Other commands run.
                destination=Path(argv[argv.index('-C')+1]);shutil.copytree(self.repo/'.git/objects',destination/'objects',dirs_exist_ok=True);return b''
            return original(argv,**kw)
        url='https://github.com/research/example';policy=Policy({'github_repositories':[url]})
        with mock.patch.object(execution,'command',side_effect=transport):
            result=acquire({'kind':'github','url':url,'commit':self.sha},self.root,self.root/'github',policy)
        self.assertEqual(result['authority'],'GITHUB_FETCH_VERIFIED')
    def test_archive_rejects_links_traversal_and_expansion(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('link',tarfile.SYMTYPE)]:
            out=io.BytesIO()
            with tarfile.open(fileobj=out,mode='w') as archive:
                info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc/passwd';archive.addfile(info)
            with self.assertRaises(ExecutionError):unpack(out.getvalue(),self.root/('out'+str(len(name))))
    def test_oci_digest_checks_real_bytes(self):
        data,digest,_,_=fixture_image()
        self.assertEqual(oci_layout(data,digest)['digest'],digest)
        with self.assertRaises(ExecutionError):oci_layout(data,'sha256:'+'0'*64)
        with self.assertRaises((ExecutionError,ValueError,KeyError)):oci_layout(data.replace(b'offline fixture layer',b'corrupt fixture layer'))
    def registry(self):
        data,digest,files,raw=fixture_image()
        def open_request(request,**kw):
            tail=request.full_url.rsplit('/',1)[1]
            content=raw if '/manifests/' in request.full_url else files['blobs/sha256/'+tail.split(':')[1]]
            return FixtureResponse(content)
        return digest,open_request
    def test_registry_content_verification_and_auth_failure(self):
        digest,transport=self.registry();policy=Policy({'oci_repositories':['registry.example/nf']})
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=transport):
            value=prebuilt('registry.example/nf',digest,policy,'alice');self.assertEqual(value['requests'],3)
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=OSError('sensitive-token')):
            with self.assertRaises(ExecutionError) as caught:prebuilt('registry.example/nf',digest,policy,'alice')
        self.assertNotIn('sensitive-token',str(caught.exception))
    def test_helm_download_digest_and_safe_extraction(self):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w') as archive:
            info=tarfile.TarInfo('chart/Chart.yaml');content=b'apiVersion: v2';info.size=len(content);archive.addfile(info,io.BytesIO(content))
        data=out.getvalue();source={'url':'https://charts.example/nf.tgz','digest':'sha256:'+hashlib.sha256(data).hexdigest()}
        with mock.patch('urllib.request.OpenerDirector.open',return_value=FixtureResponse(data)):
            result=helm_source(source,self.root/'helm',Policy({'helm_charts':[source['url']]}))
        self.assertEqual(result['authority'],'HELM_CONTENT_VERIFIED')
    def test_build_requires_isolated_worker(self):
        with self.assertRaises(Denied):build(self.repo,self.root/'image.tar',self.policy,'alice')
    def test_remote_build_command_and_verified_export(self):
        data,digest,_,_=fixture_image();worker={'tenant':'alice','isolation':'DEDICATED_OFF_HOST_QUOTA_ENFORCED','quota_evidence':'reviewed-fixture','address':'tcp://builder.example:1234','max_seconds':30,'lease_seconds':120,'cpu_millicores':1000,'memory_mib':1024,'pids':128,'disk_mib':2048}
        for name in ('ca','cert','key'):
            path=self.root/name;path.write_text('fixture');path.chmod(0o600);worker[name]=str(path)
        def fake_worker(argv,**kw):
            self.assertNotIn('--allow',argv);self.assertIn('type=oci,dest='+str(self.root/'image.tar'),argv)
            (self.root/'image.tar').write_bytes(data);return b''
        with mock.patch('telecom.execution.command',side_effect=fake_worker), mock.patch('telecom.execution.verify_worker',return_value={'pinned_address':'tcp://192.0.2.20:1234','server_name':'builder.example'}):
            result=build(self.repo,self.root/'image.tar',Policy({'build_worker':worker}),'alice')
        self.assertEqual(result['digest'],digest)
    def test_nonzero_timeout_output_and_cancellation(self):
        for argv,kwargs,reason in [([sys.executable,'-c','raise RuntimeError("secret")'],{},'NONZERO_EXIT'),
             ([sys.executable,'-c','import time;time.sleep(10)'],{'timeout':.05},'TIMEOUT'),
             ([sys.executable,'-c','print("x"*10000)'],{'limit':100},'OUTPUT_BUDGET'),
             ([sys.executable,'-c','import time;time.sleep(10)'],{'cancel':lambda:True},'CANCELLED')]:
            with self.subTest(reason=reason),self.assertRaises(ExecutionError) as caught:command(argv,**kwargs)
            self.assertEqual(caught.exception.code,reason);self.assertNotIn('secret',str(caught.exception))
    def test_subprocess_descendants_cleaned(self):
        pidfile=self.root/'pid'
        code='import subprocess,time; p=subprocess.Popen(["sleep","30"]);open('+repr(str(pidfile))+',"w").write(str(p.pid));time.sleep(30)'
        with self.assertRaises(ExecutionError):command([sys.executable,'-c',code],timeout=.3)
        pid=int(pidfile.read_text());status=Path('/proc')/str(pid)/'status'
        self.assertTrue(not status.exists() or 'State:\tZ' in status.read_text())
    def test_rss_watchdog_and_disk_budget(self):
        with mock.patch('assurance.memory.tree_memory',return_value={'rss_sum_bytes':300*1024*1024}):
            with self.assertRaises(ExecutionError) as caught:command(['sleep','10'])
        self.assertEqual(caught.exception.code,'RSS_BUDGET')
        (self.root/'large').write_bytes(b'x'*2048)
        with self.assertRaises(ExecutionError) as caught:command(['sleep','10'],disk_root=self.root,disk_limit=10)
        self.assertEqual(caught.exception.code,'DISK_BUDGET')
    def test_webhook_signatures(self):
        secret='offline-webhook-value-not-a-credential';raw=json.dumps({'after':self.sha}).encode()
        signature='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
        self.assertEqual(verify_webhook(raw,signature,secret)['after'],self.sha)
        with self.assertRaises(Denied):verify_webhook(raw,'sha256=invalid',secret)

class PipelineTests(unittest.TestCase):
    tearDown=ExecutionTests.tearDown
    registry=ExecutionTests.registry
    def setUp(self):
        ExecutionTests.setUp(self);self.principal=Principal('researcher','alice',frozenset({'researcher','builder','operator','publisher'}))
        self.reviewer=Principal('reviewer','alice',frozenset({'reviewer'}))
        self.service=Service(self.root/'runtime',self.root,helm=HELM)
        self.pipeline=Pipeline(self.service,self.policy)
        self.digest,self.transport=self.registry()
        self.policy.value['oci_repositories']=['registry.example/nf']
        manifest=json.loads((ROOT/'examples/telecom/ran.json').read_text());manifest['source']=self.source
        manifest['image']['repository']='registry.example/nf';manifest['image']['digest']=self.digest
        chart=self.repo/'chart/values.yaml'
        chart.write_text(chart.read_text().replace('example.invalid/research/ran@sha256:'+'1'*64,'registry.example/nf@'+self.digest))
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Offline','-c','user.email=offline@example.invalid','commit','-m','pin actual fixture digest'],check=True,capture_output=True)
        self.sha=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD']).decode().strip();self.source['commit']=self.sha;manifest['source']=self.source
        self.workload=self.service.register(self.principal,manifest,'register')
    def complete(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','queue')
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=self.transport): result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'COMPLETED',result)
        return result
    def test_full_pipeline_real_git_helm_oci_and_dedup(self):
        job=self.complete();release=self.service.journal.get(self.principal,'releases',job['release_id'])
        self.assertEqual(release['image']['digest'],self.digest);self.assertEqual(release['state'],'VERIFIED_PACKAGE')
        self.assertEqual(self.pipeline.queue(self.principal,self.workload['id'],'chart','queue')['id'],job['id'])
        with self.assertRaises(Denied):self.pipeline.run(self.principal,job['id'])
        self.assertTrue(self.service.journal.verify_audit());self.assertFalse(release['production_eligible'])
    def test_cross_tenant_and_missing_approval(self):
        stranger=Principal('researcher','other',self.principal.roles)
        with self.assertRaises(Denied):self.pipeline.queue(stranger,self.workload['id'],'chart','queue')
        job=self.complete();request=self.pipeline.request(self.principal,job['release_id'],'catalog','request') if self.policy.value.get('catalog_targets') else None
        with self.assertRaises(Denied):self.pipeline.publish(self.principal,'missing','missing')
    def test_cancel_and_lock_release(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','queue');self.pipeline.cancel(self.principal,job['id'],'cancel')
        self.assertEqual(self.pipeline.run(self.principal,job['id'])['state'],'CANCELLED')
        lock=self.service.journal.directory/('pipeline-'+identity({'tenant':'alice','workload':self.workload['id']})+'.lock')
        with exclusive(lock): pass
    def test_overlap_rejected(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','queue')
        lock=self.service.journal.directory/('pipeline-'+identity({'tenant':'alice','workload':self.workload['id']})+'.lock')
        with exclusive(lock),self.assertRaises(RuntimeError):self.pipeline.run(self.principal,job['id'])
    def test_invalid_helm_and_incorrect_digest(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'missing-chart','queue')
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=self.transport):result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'FAILED');self.assertEqual(result['diagnostic']['stage'],'package')
    def catalog_setup(self):
        job=self.complete();self.policy.value['catalog_targets']={'alice':{'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':True,'project':'research-alice','host':'https://osm.example','secret_ref':'osm'}}
        item=self.pipeline.request(self.principal,job['release_id'],'catalog','request')
        with self.assertRaises(Denied):self.pipeline.approve(self.principal,item['id'],'self-approve')
        approval=self.pipeline.approve(self.reviewer,item['id'],'approve')
        return item,approval,job
    def test_catalog_existing_client_exact_readback(self):
        item,approval,job=self.catalog_setup();release=self.service.journal.get(self.principal,'releases',job['release_id']);scenario=release['package']['scenario']
        class FixtureCatalog(SimulatedCatalog):
            def request(self,method,path,data=None,content_type='application/yaml'):
                if method=='POST':self.pending=('knf',scenario['knf_package']) if 'vnfpkgm' in path else ('ns',scenario['nsd_package'])
                return super().request(method,path,data,content_type)
        client=FixtureCatalog();result=self.pipeline.publish(self.principal,item['id'],approval['id'],lambda:client)
        self.assertEqual(result['state'],'PUBLISHED_CONTENT_VERIFIED',result)
        self.assertFalse(any(x['method']=='PUT' for x in client.requests))
        with self.assertRaises(Denied):self.pipeline.publish(self.principal,item['id'],approval['id'],lambda:client)
    def test_osm_auth_failure_sanitized_and_no_retry(self):
        item,approval,_=self.catalog_setup()
        def failure():raise RuntimeError('private-password')
        result=self.pipeline.publish(self.principal,item['id'],approval['id'],failure)
        self.assertEqual(result['state'],'UNKNOWN_OUTCOME');self.assertNotIn('private-password',json.dumps(result))
        with self.assertRaises(Denied):self.pipeline.publish(self.principal,item['id'],approval['id'],failure)
    def test_catalog_conflict_and_readback_failure(self):
        item,approval,_=self.catalog_setup();client=mock.Mock();client.lookup.return_value='existing'
        result=self.pipeline.publish(self.principal,item['id'],approval['id'],lambda:client)
        self.assertEqual(result['state'],'UNKNOWN_OUTCOME');client.request.assert_not_called()
    def test_update_rollback_not_verified(self):
        job=self.complete()
        for action in ('update','rollback'):
            result=self.pipeline.request(self.principal,job['release_id'],action,action)
            self.assertEqual(result['state'],'NOT_VERIFIED');self.assertFalse(result['rollback_eligible'])
            with self.assertRaises(Denied):self.pipeline.approve(self.reviewer,result['id'],'approve-'+action)

    def test_incorrect_registry_digest_blocks_release(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','queue')
        with mock.patch('urllib.request.OpenerDirector.open',return_value=FixtureResponse(b'incorrect content')):
            result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'FAILED');self.assertEqual(result['diagnostic']['code'],'DIGEST_MISMATCH')
        self.assertEqual(self.service.journal.list(self.principal,'releases'),[])

    def test_cancel_during_image_stage_and_no_automatic_recovery(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','queue')
        def image(*args,**kwargs):
            self.pipeline.cancel(self.principal,job['id'],'cancel-during-build')
            return {'digest':self.digest,'platform':'linux/amd64'}
        with mock.patch('telecom.pipeline.prebuilt',side_effect=image):result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'CANCELLED')
        with self.assertRaises(Denied):self.pipeline.run(self.principal,job['id'])

    def test_live_catalog_auth_error_uses_existing_client(self):
        item,approval,_=self.catalog_setup()
        from osm_catalog import AuthorizationError
        client=mock.Mock();client.lookup.side_effect=AuthorizationError('private credential suppressed')
        result=self.pipeline.publish(self.principal,item['id'],approval['id'],lambda:client)
        self.assertEqual(result['state'],'UNKNOWN_OUTCOME');client.request.assert_not_called()

    def test_catalog_readback_checksum_failure_is_unknown(self):
        item,approval,_=self.catalog_setup();client=mock.Mock();client.lookup.return_value=None
        client.verify.side_effect=ValueError('checksum mismatch')
        result=self.pipeline.publish(self.principal,item['id'],approval['id'],lambda:client)
        self.assertEqual(client.request.call_count,2);self.assertEqual(result['state'],'UNKNOWN_OUTCOME')

    def test_real_http_webhook_duplicate_and_tenant_binding(self):
        from flask import Flask
        from telecom.api import register
        manifest=copy.deepcopy(self.workload['manifest']);manifest['name']='github-fixture'
        url='https://github.com/research/example';manifest['source']={'kind':'github','url':url,'commit':self.sha}
        workload=self.service.register(self.principal,manifest,'github-register')
        secret='offline-only-webhook-fixture-value-123456789'
        secret_file=self.root/'secret.json';secret_file.write_text(json.dumps({'value':secret}));secret_file.chmod(0o600)
        self.policy.value.update(github_repositories=[url],secrets={'hook':{'tenant':'alice','file':str(secret_file),'purpose':'github-webhook','resources':[workload['id']]}},
            webhooks={workload['id']:{'tenant':'alice','subject':'researcher','secret_ref':'hook','ref':'refs/heads/main','chart':'chart'}})
        policy=self.root/'policy.json';policy.write_text(json.dumps(self.policy.value));policy.chmod(0o600)
        app=Flask('p5-webhook');register(app)
        raw=json.dumps({'after':self.sha,'ref':'refs/heads/main','repository':{'html_url':url}}).encode()
        headers={'X-GitHub-Event':'push','X-GitHub-Delivery':'offline-delivery','X-Hub-Signature-256':'sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()}
        environment={'TELECOM_EXECUTION_POLICY':str(policy),'TELECOM_RUNTIME_DIR':str(self.service.journal.directory),'TELECOM_WORKSPACE':str(self.root)}
        with mock.patch.dict(os.environ,environment):
            client=app.test_client();path='/api/telecom/github-webhooks/'+workload['id']
            first=client.post(path,data=raw,content_type='application/json',headers=headers)
            second=client.post(path,data=raw,content_type='application/json',headers=headers)
            self.assertEqual(first.status_code,200,first.json);self.assertEqual(first.json['id'],second.json['id'])
            self.assertEqual(first.json['state'],'QUEUED');self.assertEqual(first.json['deployment'],'NOT_REQUESTED')
            bad=dict(headers,**{'X-Hub-Signature-256':'sha256=forged'})
            self.assertEqual(client.post(path,data=raw,content_type='application/json',headers=bad).status_code,403)
            self.assertEqual(client.post('/api/telecom/pipeline-runs',json={'job_id':first.json['id']},headers=headers).status_code,403)

    def test_instance_guard_blocks_protected_and_unverified_operations(self):
        from telecom.lifecycle import Lifecycle
        from assurance.model import PROTECTED_NS
        adapter=Lifecycle(self.service,self.policy)
        for ns in PROTECTED_NS:
            with self.assertRaises(Denied):adapter.plan(self.principal,'release',ns,'rollback',{})
        with self.assertRaises(Denied):adapter.execute({'approved':True})
        self.assertEqual(adapter.installation_status,'NOT_VERIFIED')

    def test_subprocess_disk_and_tenant_source_policies(self):
        other=Principal('researcher','other',self.principal.roles)
        with self.assertRaises(Denied):self.pipeline.local_workspace(other)
        self.policy.value['local_roots']['alice']='../outside'
        with self.assertRaises((Denied,ValueError)):self.pipeline.local_workspace(self.principal)

    def test_full_dockerfile_pipeline_export_and_digest_binding(self):
        image_data,image_digest,_,_=fixture_image()
        worker={'tenant':'alice','isolation':'DEDICATED_OFF_HOST_QUOTA_ENFORCED','quota_evidence':'offline-fixture',
                'address':'tcp://builder.example:1234','max_seconds':30,'lease_seconds':120,'cpu_millicores':1000,'memory_mib':1024,'pids':128,'disk_mib':2048}
        for field in ('ca','cert','key'):
            path=self.root/field;path.write_text('fixture');path.chmod(0o600);worker[field]=str(path)
        self.policy.value['build_worker']=worker
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','docker-job',image_mode='dockerfile')
        def fixture_buildctl(argv,**kw):
            if argv[0]!='buildctl':return command(argv,**kw)
            destination=Path(next(value.split('dest=',1)[1] for value in argv if value.startswith('type=oci,dest=')))
            destination.write_bytes(image_data);return b''
        with mock.patch('telecom.execution.command',side_effect=fixture_buildctl), mock.patch('telecom.execution.verify_worker',return_value={'pinned_address':'tcp://192.0.2.20:1234','server_name':'builder.example'}), mock.patch('telecom.registry.Publisher.configuration',return_value=({},{})), mock.patch('telecom.registry.Publisher.publish',return_value={'id':'offline-receipt','repository':'registry.example/nf','digest':image_digest}):result=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(result['state'],'COMPLETED',result)
        release=self.service.journal.get(self.principal,'releases',result['release_id'])
        self.assertEqual(release['image']['digest'],image_digest)
        self.assertTrue((self.service.journal.directory/release['image_verification']['retained_artifact']).is_file())
        self.assertEqual(release['image_verification']['registry_publication'],'REGISTRY_MANIFEST_VERIFIED')

    def test_actual_worker_entry_point_cancelled_job(self):
        job=self.pipeline.queue(self.principal,self.workload['id'],'chart','worker-job');self.pipeline.cancel(self.principal,job['id'],'cancel-worker')
        token='offline-worker-auth-fixture-'+('x'*40)
        auth=self.root/'auth.json';auth.write_text(json.dumps([{'subject':'researcher','tenant':'alice','roles':['builder'],
            'sha256':hashlib.sha256(token.encode()).hexdigest()}]));auth.chmod(0o600)
        token_file=self.root/'token.json';token_file.write_text(json.dumps({'value':token}));token_file.chmod(0o600)
        policy=self.root/'execution.json';policy.write_text(json.dumps(self.policy.value));policy.chmod(0o600)
        env=dict(os.environ,PYTHONPATH=str(ROOT/'scripts'),TELECOM_AUTH_FILE=str(auth),TELECOM_RUNTIME_DIR=str(self.service.journal.directory),
            TELECOM_WORKSPACE=str(self.root),TELECOM_EXECUTION_POLICY=str(policy),PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-m','telecom.worker','--job-id',job['id'],'--token-file',str(token_file)],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1,result.stderr);self.assertEqual(json.loads(result.stdout)['state'],'CANCELLED')
        self.assertNotIn(token,result.stdout+result.stderr)

    def test_secret_purpose_and_destination_cannot_be_confused(self):
        secret=self.root/'secret-ref.json';secret.write_text(json.dumps({'value':'offline-only-secret-reference-value-123456789'}));secret.chmod(0o600)
        self.policy.value['secrets']={'osm':{'tenant':'alice','purpose':'osm-credentials','resources':['https://osm.example#research-alice'],'file':str(secret)}}
        with self.assertRaises(Denied):self.policy.secret('osm','alice','registry-bearer','registry.example/nf')
        with self.assertRaises(Denied):self.policy.secret('osm','other','osm-credentials','https://osm.example#research-alice')
        with self.assertRaises(Denied):self.policy.secret('osm','alice','osm-credentials','https://evil.example#research-alice')

    def test_same_source_explicit_new_event_preserves_sealed_release(self):
        first=self.complete();job=self.pipeline.queue(self.principal,self.workload['id'],'chart','different-explicit-event')
        with mock.patch('urllib.request.OpenerDirector.open',side_effect=self.transport):second=self.pipeline.run(self.principal,job['id'])
        self.assertEqual(second['state'],'COMPLETED',second);self.assertEqual(first['release_id'],second['release_id'])
        self.assertEqual(len(self.service.journal.list(self.principal,'releases')),1)

    def test_retained_artifact_is_verified_and_not_overwritten(self):
        from telecom.execution import retain_image
        data,digest,_,_=fixture_image();directory=self.root/'images'
        path=retain_image(data,directory,digest);self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(retain_image(data,directory,digest),path)
        path.write_bytes(b'interrupted artifact')
        with self.assertRaises((ExecutionError,tarfile.TarError,ValueError)):retain_image(data,directory,digest)
        self.assertEqual(path.read_bytes(),b'interrupted artifact')

    def test_parent_cancellation_cleans_command_group(self):
        count=0
        def interrupt():
            nonlocal count
            count+=1
            if count>2:raise KeyboardInterrupt
            return False
        with self.assertRaises(KeyboardInterrupt):command(['sleep','30'],cancel=interrupt)

    def test_malformed_policy_cannot_turn_allowlist_into_substring_match(self):
        with self.assertRaises(Denied):Policy({'github_repositories':'https://github.com/a/b-internal'})
        with self.assertRaises(Denied):Policy({'secrets':[]})

class ChartScopedSubmoduleTests(unittest.TestCase):
    """Adversarial coverage for acquire()'s chart_path-scoped submodule/
    symlink safety boundary -- modeled directly on the real free5GC finding
    (github.com/free5gc/free5gc-helm, commit
    d22485f5a95fd6bb97ec1252f0933ef5da17f96d) that charts/free5gc is a safe
    subtree to onboard even though the same repository, elsewhere, vendors
    an unrelated submodule at charts/free-ran-ue -- the exact scenario
    execution.acquire() previously rejected outright (see
    chart_adapters.py's REMOVALS/DEPENDENCY_REMOVALS comments for that
    earlier finding)."""

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        subprocess.run(['git','init','--template=',str(self.repo)],check=True,capture_output=True)
        self.policy=Policy({'local_roots':{'alice':'.'}})
        self._outs=0

    def tearDown(self): self.tmp.cleanup()

    def git(self,*args):
        return subprocess.run(['git','-C',str(self.repo)]+list(args),check=True,capture_output=True).stdout

    def commit(self,message='fixture'):
        self.git('-c','user.name=t','-c','user.email=t@example.invalid','commit','-q','-m',message)
        return self.git('rev-parse','HEAD').decode().strip()

    def write_base_chart(self):
        (self.repo/'charts/free5gc/templates').mkdir(parents=True,exist_ok=True)
        (self.repo/'charts/free5gc/Chart.yaml').write_text('name: free5gc\nversion: 1.1.7\n')
        (self.repo/'charts/free5gc/templates/a.yaml').write_text('x: 1\n')
        (self.repo/'charts/other').mkdir(parents=True,exist_ok=True)
        (self.repo/'charts/other/Chart.yaml').write_text('name: other\n')
        self.git('add','-A')
        return self.commit('base')

    def add_gitlink(self,path):
        self.git('update-index','--add','--cacheinfo','160000,'+('a'*40)+','+path)

    def source(self,sha):
        return {'kind':'local-git','path':'repo','commit':sha}

    def out(self):
        self._outs+=1;return self.root/('out%d'%self._outs)

    def test_unrelated_sibling_submodule_allowed_for_requested_chart(self):
        self.write_base_chart()
        self.add_gitlink('charts/free-ran-ue')
        sha=self.commit('add sibling gitlink')
        destination=self.out()
        result=acquire(self.source(sha),self.root,destination,self.policy,chart_path='charts/free5gc')
        self.assertTrue((destination/'Chart.yaml').is_file())
        self.assertFalse((destination/'free-ran-ue').exists())
        self.assertEqual(result['commit'],sha)

    def test_submodule_inside_selected_chart_rejected(self):
        self.write_base_chart()
        self.add_gitlink('charts/free5gc/vendor')
        sha=self.commit('nested gitlink inside chart')
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/free5gc')
        self.assertEqual(cm.exception.code,'SUBMODULE_NOT_SUPPORTED')

    def test_submodule_at_selected_chart_path_rejected(self):
        self.write_base_chart()
        self.add_gitlink('charts/vendored-ue')
        sha=self.commit('gitlink at the exact requested path')
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/vendored-ue')
        self.assertEqual(cm.exception.code,'SUBMODULE_NOT_SUPPORTED')

    def test_submodule_at_ancestor_of_selected_chart_rejected(self):
        self.write_base_chart()
        self.add_gitlink('charts/vendored-ue')
        sha=self.commit('gitlink ancestor of requested nested path')
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/vendored-ue/nested/path')
        self.assertEqual(cm.exception.code,'SUBMODULE_NOT_SUPPORTED')

    def test_invalid_chart_path_rejected(self):
        sha=self.write_base_chart()
        for bad in ('','.','/charts/free5gc','../escape','charts/../free5gc','charts//free5gc','charts/free5gc/'):
            with self.subTest(bad=bad):
                with self.assertRaises(ExecutionError) as cm:
                    acquire(self.source(sha),self.root,self.out(),self.policy,chart_path=bad)
                self.assertEqual(cm.exception.code,'INVALID_CHART_PATH')

    def test_symlink_inside_selected_chart_rejected(self):
        self.write_base_chart()
        (self.repo/'charts/free5gc/evil').symlink_to('/etc/passwd')
        self.git('add','-A')
        sha=self.commit('symlink inside chart')
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/free5gc')
        self.assertEqual(cm.exception.code,'SYMLINK_NOT_SUPPORTED')

    def test_wrong_source_commit_rejected(self):
        self.write_base_chart()
        with self.assertRaises(ExecutionError):
            acquire(self.source('1'*40),self.root,self.out(),self.policy,chart_path='charts/free5gc')

    def test_source_content_drift_across_commits_never_stale(self):
        """Re-acquiring the SAME chart_path at two different, independently
        pinned commits always returns exactly that commit's content --
        never a stale/leftover copy from a previous call."""
        sha1=self.write_base_chart()
        (self.repo/'charts/free5gc/Chart.yaml').write_text('name: free5gc\nversion: 1.1.8\n')
        self.git('add','-A')
        sha2=self.commit('bump version')
        out1,out2=self.out(),self.out()
        result1=acquire(self.source(sha1),self.root,out1,self.policy,chart_path='charts/free5gc')
        result2=acquire(self.source(sha2),self.root,out2,self.policy,chart_path='charts/free5gc')
        self.assertIn('1.1.7',(out1/'Chart.yaml').read_text())
        self.assertIn('1.1.8',(out2/'Chart.yaml').read_text())
        self.assertNotEqual(result1['archive_digest'],result2['archive_digest'])

    def test_root_scope_still_rejects_unrelated_submodule_unweakened(self):
        """chart_path=None (every existing caller's behavior today) stays
        exactly as strict as before this feature existed: ANY submodule
        anywhere is still rejected, even one that would be an allowed
        sibling under a chart_path scope."""
        self.write_base_chart()
        self.add_gitlink('charts/free-ran-ue')
        sha=self.commit('sibling gitlink, root scope')
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy)
        self.assertEqual(cm.exception.code,'SUBMODULE_NOT_SUPPORTED')

    def test_chart_path_pointing_at_a_file_rejected(self):
        sha=self.write_base_chart()
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/free5gc/Chart.yaml')
        self.assertEqual(cm.exception.code,'CHART_PATH_NOT_A_DIRECTORY')

    def test_archive_contains_exactly_the_selected_chart_subtree(self):
        """The extracted destination contains EXACTLY the chart subtree's
        files -- no more, no less, matching `git ls-tree -r` of that exact
        subtree -- and every file's content is byte-for-byte what git
        itself reports for that blob. Also confirms the path layout
        (chart-root-relative, e.g. 'Chart.yaml' not 'charts/free5gc/
        Chart.yaml') is exactly what package.py's own file-capture code
        expects."""
        self.write_base_chart()
        self.add_gitlink('charts/free-ran-ue')
        sha=self.commit('with unrelated sibling gitlink')
        destination=self.out()
        acquire(self.source(sha),self.root,destination,self.policy,chart_path='charts/free5gc')
        expected=set(self.git('ls-tree','-r','--name-only',sha+':charts/free5gc').decode().splitlines())
        extracted={str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()}
        self.assertEqual(expected,extracted)
        for rel in expected:
            self.assertEqual((destination/rel).read_bytes(),self.git('show',sha+':charts/free5gc/'+rel))

    def test_result_echoes_verified_commit_authority_and_chart_path(self):
        """source_evidence built from this result can state the commit,
        acquisition authority AND the actually-verified chart_path as
        plain, retained facts -- chart_path is None for root-scope, the
        exact string for a scoped acquisition, never silently dropped."""
        sha=self.write_base_chart()
        root_result=acquire(self.source(sha),self.root,self.out(),self.policy)
        self.assertEqual(root_result['commit'],sha)
        self.assertEqual(root_result['authority'],'LOCAL_OBJECTS_VERIFIED')
        self.assertIsNone(root_result['chart_path'])
        scoped_result=acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/free5gc')
        self.assertEqual(scoped_result['commit'],sha)
        self.assertEqual(scoped_result['authority'],'LOCAL_OBJECTS_VERIFIED')
        self.assertEqual(scoped_result['chart_path'],'charts/free5gc')

    def test_file_size_budget_still_enforced_for_scoped_archive(self):
        """unpack()'s existing MAX_SOURCE/expansion budget is untouched and
        still fires for a chart-scoped archive, exactly as it always has
        for a whole-repository one."""
        self.write_base_chart()
        (self.repo/'charts/free5gc/big.bin').write_bytes(b'x'*(MAX_SOURCE+1))
        self.git('add','-A')
        sha=self.commit('oversized file inside chart')
        with self.assertRaises(ExecutionError) as cm:
            acquire(self.source(sha),self.root,self.out(),self.policy,chart_path='charts/free5gc')
        self.assertIn(cm.exception.code,('ARCHIVE_BUDGET','EXPANSION_BUDGET','TRUNCATED_ARCHIVE','OUTPUT_BUDGET'))

    def test_chart_path_cannot_be_supplied_through_untrusted_source_dict(self):
        """`chart_path` is a distinct keyword argument, never read from the
        caller-supplied `source` dict -- a 'chart_path' key smuggled into
        `source` (the one piece of the request a less-trusted caller might
        influence) is silently ignored, never adopted as a scoping
        boundary."""
        sha=self.write_base_chart()
        self.add_gitlink('charts/free-ran-ue')
        sha=self.commit('with sibling gitlink')
        tainted=dict(self.source(sha),chart_path='charts/free5gc')
        with self.assertRaises(ExecutionError) as cm:
            acquire(tainted,self.root,self.out(),self.policy)
        self.assertEqual(cm.exception.code,'SUBMODULE_NOT_SUPPORTED')

if __name__=='__main__':unittest.main()
