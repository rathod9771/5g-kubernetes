"""Reproducible offline OCI+Helm onboarding. All remote transport is simulated."""
import argparse
import io
import json
from pathlib import Path
import shutil
from unittest.mock import patch, Mock
from .demo_execution import image_fixture, ROOT
from .contract import canonical
from .execution import Policy
from .service import Service, SimulatedCatalog
from .pipeline import Pipeline
from .initial import InitialDeployment
from .security import Principal

UID='11111111-2222-4333-8444-555555555555'
OP='22222222-2222-4333-8444-555555555555'

def run(output,helm):
    output=Path(output);output.mkdir(mode=0o700,parents=True,exist_ok=False)
    workspace=output/'workspace';workspace.mkdir(mode=0o700)
    shutil.copytree(ROOT/'examples/telecom/disposable/chart',workspace/'chart')
    _,digest,blobs,raw=image_fixture()
    manifest=json.loads((ROOT/'examples/telecom/disposable/manifest.json').read_text())
    repo='registry.example/offline/disposable'
    manifest['source'].update(repository=repo,digest=digest)
    manifest['image'].update(repository=repo,digest=digest)
    (workspace/'chart/values.yaml').write_text('image: '+repo+'@'+digest+'\n')
    user=Principal('researcher','demo',frozenset({'researcher','builder','publisher','operator'}))
    reviewer=Principal('independent-reviewer','demo',frozenset({'reviewer'}))
    target={'scope':'DISPOSABLE_RESEARCH_NAMESPACE','tenant':'demo','enabled':True,
            'namespace':'research-demo-offline','namespace_uid':UID,'cluster_uid':OP,
            'vim_id':UID,'kubeconfig':'UNUSED_OFFLINE_FIXTURE'}
    policy=Policy({'local_roots':{'demo':'.'},'oci_repositories':[repo],
        'catalog_targets':{'demo':{'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':True,
            'host':'https://osm.example','project':'research-demo'}},
        'deployment_targets':{'demo':target}})
    service=Service(output/'runtime',workspace,helm=helm);pipeline=Pipeline(service,policy)
    workload=service.register(user,manifest,'register')
    job=pipeline.queue(user,workload['id'],'chart','queue-prebuilt',image_mode='prebuilt')
    def registry(request,**kwargs):
        if not request.full_url.startswith('https://registry.example/v2/offline/disposable/'):
            raise AssertionError('Unexpected network destination')
        key=request.full_url.rsplit('/',1)[-1]
        return io.BytesIO(raw if '/manifests/' in request.full_url else blobs['blobs/sha256/'+key[7:]])
    with patch('urllib.request.OpenerDirector.open',side_effect=registry),patch('telecom.pipeline.build',side_effect=AssertionError('No BuildKit needed')):
        job=pipeline.run(user,job['id'])
    if job['state']!='COMPLETED':raise RuntimeError('Offline package pipeline failed')
    release=service.journal.get(user,'releases',job['release_id']);scenario=release['package']['scenario']
    class OfflineCatalog(SimulatedCatalog):
        def request(self,method,path,data=None,content_type='application/yaml'):
            if method=='POST':self.pending=('knf',scenario['knf_package']) if 'vnfpkgm' in path else ('ns',scenario['nsd_package'])
            return super().request(method,path,data,content_type)
    publication=pipeline.request(user,release['id'],'catalog','catalog-request')
    approval=pipeline.approve(reviewer,publication['id'],'catalog-approve')
    publication=pipeline.publish(user,publication['id'],approval['id'],OfflineCatalog)
    if publication['state']!='PUBLISHED_CONTENT_VERIFIED':raise RuntimeError('Fixture catalog failure')
    adapter=InitialDeployment(service,policy)
    request=adapter.request(user,publication['id'],'initial-request')
    approval=adapter.approve(reviewer,request['id'],'initial-approve')
    client=Mock();client.verify.return_value=publication['diagnostic']['catalog_ids']
    calls=[]
    def nbi(method,path,data=None,*args):
        calls.append({'method':method,'path':path})
        if method=='POST':return canonical({'id':UID,'nslcmop_id':OP})
        if path.endswith('ns_lcm_op_occs'):return b'[]'
        if '/ns_instances/' in path:return canonical({'_id':UID,'name':request['ns_name'],
            'nsd-id':publication['diagnostic']['catalog_ids']['ns'],'nsState':'READY',
            'operational-status':'running','config-status':'configured'})
        return canonical({'nsInstanceId':UID,'lcmOperationType':'INSTANTIATE','operationState':'COMPLETED'})
    client.request.side_effect=nbi
    persist=adapter.save
    def simulated_record(principal,item,event):
        if 'observation' in item:
            item=dict(item,observation=dict(item['observation'],provenance='SIMULATED'))
        return persist(principal,item,event)
    with patch('telecom.initial.scoped_catalog',return_value=client),patch.object(adapter,'snapshot',return_value=[]) as reader,patch.object(adapter,'save',side_effect=simulated_record):
        submitted=adapter.submit(user,request['id'],approval['id'])
        reader.return_value=[{'metadata':{'namespace':target['namespace']},
            'spec':{'containers':[{'image':repo+'@'+digest}]},
            'status':{'containerStatuses':[{'ready':True,'imageID':repo+'@'+digest}]}}]
        observed=adapter.refresh(user,request['id'],'observe')
    summary={'status':'OFFLINE_TESTED','remote_evidence':'SIMULATED','source':'PREBUILT_OCI',
        'buildkit_required':False,'image_digest':digest,'registry_bytes':'VERIFIED_FIXTURE_CONTENT',
        'helm':'REAL_LINT_RENDER','package_digests':release['package']['package_digests'],
        'catalog':publication['state'],'initial_submission':submitted['state'],
        'deployment':observed['state'],'application_health':'UNVERIFIED',
        'protocol_verification':'NOT_RUN','upgrade':'NOT_VERIFIED','rollback':'NOT_VERIFIED',
        'simulated_nbi_create_calls':sum(c['method']=='POST' for c in calls),
        'production_calls':0,'production_eligible':False,'audit_integrity':service.journal.verify_audit()}
    (output/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--helm',default='helm')
    args=parser.parse_args();print(json.dumps(run(args.output,args.helm),indent=2))
if __name__=='__main__':main()
