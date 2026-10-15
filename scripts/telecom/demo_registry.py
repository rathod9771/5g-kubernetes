"""P7 offline Git -> build export -> registry -> package -> catalog request.

Git, OCI hash verification, Helm and journal are real. BuildKit identity/command and
Skopeo network operations are explicit test doubles, never live verification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import urllib.request
import urllib.error
from unittest.mock import patch
from .contract import canonical
from .demo_execution import image_fixture,ROOT
from .execution import Policy,command
from .pipeline import Pipeline
from .security import Principal
from .service import Service


def run(output,helm):
    output=Path(output);output.mkdir(mode=0o700,parents=True,exist_ok=False)
    workspace=output/'workspace';repo=workspace/'external-ran';repo.mkdir(mode=0o700,parents=True)
    data,digest,files,manifest_bytes=image_fixture()
    shutil.copytree(ROOT/'examples/telecom/ran-chart',repo/'chart')
    (repo/'Dockerfile').write_text('FROM scratch\nLABEL research.offline-fixture=true\n')
    command(['git','init','--template=',str(repo)]);command(['git','-C',str(repo),'add','.'])
    command(['git','-C',str(repo),'-c','user.name=Offline researcher','-c','user.email=research@example.invalid','commit','-m','Disposable P7 source'])
    sha=command(['git','-C',str(repo),'rev-parse','HEAD']).decode().strip()
    declaration=json.loads((ROOT/'examples/telecom/ran.json').read_text())
    declaration['source']={'kind':'local-git','path':'external-ran','commit':sha}
    declaration['image']['repository']='registry.example/research/demo/ran'
    registry_secret=output/'registry-secret.json';registry_secret.write_text(json.dumps({'value':json.dumps({'username':'offline-demo','password':'OFFLINE_FIXTURE_NOT_A_CREDENTIAL_123456789'})}));registry_secret.chmod(0o600)
    worker={'tenant':'demo','isolation':'DEDICATED_OFF_HOST_QUOTA_ENFORCED','quota_evidence':'OFFLINE_NOT_VERIFIED',
            'address':'tcp://worker.example:1234','max_seconds':120,'lease_seconds':120,
            'cpu_millicores':1000,'memory_mib':1024,'pids':128,'disk_mib':2048}
    for key in ('ca','cert','key'):
        path=output/(key+'.fixture');path.write_text('OFFLINE TRANSPORT DOUBLE; NOT A CERTIFICATE');path.chmod(0o600);worker[key]=str(path)
    policy_value={'local_roots':{'demo':'.'},'build_worker':worker,
        'registry_targets':{'demo':{'scope':'DISPOSABLE_RESEARCH_REPOSITORY','enabled':True,'authentication_required':True,
            'repositories':['registry.example/research/demo/ran'],'secret_ref':'publication','max_seconds':60,'max_requests':64,
            'client_version':'skopeo version 1.21.0','transport_evidence':'OFFLINE_COMMAND_DOUBLE'}},
        'secrets':{'publication':{'tenant':'demo','purpose':'registry-publish','resources':['registry.example/research/demo/ran'],'file':str(registry_secret)}},
        'catalog_targets':{'demo':{'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':False,'project':'research-demo','host':'https://osm.example'}}}
    policy_file=output/'policy.json';policy_file.write_text(json.dumps(policy_value));policy_file.chmod(0o600)
    service=Service(output/'runtime',workspace,helm=helm);pipeline=Pipeline(service,Policy(policy_value))
    actor=Principal('researcher','demo',frozenset({'researcher','builder','publisher','operator'}))
    workload=service.register(actor,declaration,'p7-register')
    job=pipeline.queue(actor,workload['id'],'chart','p7-source-event',image_mode='dockerfile')
    operations=[];remote={}
    def client(argv,**kwargs):
        if argv[0]=='buildctl':
            assert '--tlsservername' in argv and '--tlskey' in argv
            destination=next(value.split('dest=',1)[1] for value in argv if value.startswith('type=oci,dest='))
            Path(destination).write_bytes(data);operations.append('BUILD_EXPORT_FIXTURE');return b''
        return command(argv,**kwargs)
    def registry(argv,**kwargs):
        if argv==['skopeo','--version']:return b'skopeo version 1.21.0\n'
        assert argv[0]=='skopeo' and '--debug' in argv
        assert argv[-1]=='docker://registry.example/research/demo/ran@'+digest
        if 'copy' in argv:
            assert '--preserve-digests' in argv and '--dest-tls-verify=true' in argv
            remote[digest]=manifest_bytes;operations.append('REGISTRY_COPY_FIXTURE')
            kwargs['observe']('stderr',b'PUT https://registry.example/v2/research/demo/ran/manifests/digest\n');return b''
        assert '--raw' in argv and '--tls-verify=true' in argv
        operations.append('REGISTRY_READBACK_FIXTURE')
        kwargs['observe']('stderr',b'GET https://registry.example/v2/research/demo/ran/manifests/digest\n')
        return remote[digest]
    identity={'pinned_address':'tcp://192.0.2.20:1234','server_name':'worker.example','verification':'OFFLINE_IDENTITY_DOUBLE'}
    with patch('telecom.execution.verify_worker',return_value=identity),patch('telecom.execution.command',side_effect=client),patch('telecom.registry.command',side_effect=registry):
        result=pipeline.run(actor,job['id'])
    if result['state']!='COMPLETED':raise RuntimeError('Offline P7 job failed: '+json.dumps(result))
    release=service.journal.get(actor,'releases',result['release_id'])
    request=pipeline.request(actor,release['id'],'catalog','p7-request-catalog')
    update=pipeline.request(actor,release['id'],'update','p7-request-update')
    for name,content in release['package']['artifacts'].items():(output/name).write_bytes(bytes.fromhex(content))
    # Serve the real research-only application, not a view mock. No external I/O.
    import importlib.util
    from werkzeug.serving import make_server,WSGIRequestHandler
    spec=importlib.util.spec_from_file_location('p7_research_app',ROOT/'docs/telecom/p6/research_only.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    token=secrets.token_urlsafe(40);auth=output/'auth.json'
    auth.write_text(json.dumps([{'subject':'researcher','tenant':'demo','roles':['reader','builder','publisher'],
                               'sha256':hashlib.sha256(token.encode()).hexdigest()}]));auth.chmod(0o600)
    environment={'TELECOM_AUTH_FILE':str(auth),'TELECOM_RUNTIME_DIR':str(output/'runtime'),'TELECOM_WORKSPACE':str(workspace),'TELECOM_EXECUTION_POLICY':str(policy_file)}
    class Quiet(WSGIRequestHandler):
        def log(self,*args,**kwargs):pass
    checks=[]
    with patch.dict(os.environ,environment):
        server=make_server('127.0.0.1',0,module.create_app(),request_handler=Quiet)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            for path in ('/','/telecom.js','/api/telecom/pipeline-jobs','/api/telecom/releases','/api/telecom/registry-publications','/api/telecom/publication-requests'):
                req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,headers={'Authorization':'Bearer '+token})
                with urllib.request.urlopen(req,timeout=5) as response:
                    assert response.status==200;response.read(4*1024*1024);checks.append({'path':path,'status':response.status})
        finally:server.shutdown();thread.join(5);server.server_close()
        assert not thread.is_alive()
    summary={'status':'OFFLINE_TESTED','job_id':job['id'],'job_state':result['state'],'source_commit':sha,'release_id':release['id'],
        'image_digest':release['image']['digest'],'initial_declared_digest':declaration['image']['digest'],
        'registry_publication':release['image_verification']['registry_publication'],
        'registry_receipt':service.journal.get(actor,'registry-publications',release['image_verification']['registry']['receipt_id']),
        'stages':[row['state'] for row in result['history']],'package_digests':release['package']['package_digests'],
        'catalog_request':request['state'],'catalog_published':False,'instance_update':update['state'],
        'transport_operations':operations,'worker_transport':'OFFLINE_COMMAND_AND_IDENTITY_DOUBLES','registry_transport':'OFFLINE_COMMAND_DOUBLE',
        'actual_buildkit_or_skopeo_run':False,'http_checks':checks,'server_stopped':True,'audit_integrity':service.journal.verify_audit(),
        'production_eligible':False,'production_mutations':0}
    (output/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--helm',default='helm')
    args=parser.parse_args();print(json.dumps(run(args.output,args.helm),indent=2))

if __name__=='__main__':main()
