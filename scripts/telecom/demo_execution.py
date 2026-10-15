"""Disposable external RAN source-to-package demonstration, entirely offline.

Real Git object acquisition, OCI byte verification, Helm lint/render and OSM
packaging execute. Registry transport and catalog transport are explicit fixtures.
No Dockerfile build, image push, OSM instance action or protocol test is claimed.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
from unittest.mock import patch
from .contract import canonical
from .execution import Policy,command,oci_layout
from .pipeline import Pipeline
from .security import Principal
from .service import Service,SimulatedCatalog

ROOT=Path(__file__).resolve().parents[2]

def image_fixture():
    config=canonical({'architecture':'amd64','os':'linux','config':{'Labels':{'research.fixture':'true'}},'rootfs':{'type':'layers','diff_ids':[]}})
    config_digest='sha256:'+hashlib.sha256(config).hexdigest()
    manifest=canonical({'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json',
                       'config':{'mediaType':'application/vnd.oci.image.config.v1+json','digest':config_digest,'size':len(config)},'layers':[]})
    digest='sha256:'+hashlib.sha256(manifest).hexdigest()
    files={'oci-layout':canonical({'imageLayoutVersion':'1.0.0'}),
           'index.json':canonical({'schemaVersion':2,'manifests':[{'mediaType':'application/vnd.oci.image.manifest.v1+json','digest':digest,'size':len(manifest)}]}),
           'blobs/sha256/'+config_digest[7:]:config,'blobs/sha256/'+digest[7:]:manifest}
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,content in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o600;archive.addfile(info,io.BytesIO(content))
    return stream.getvalue(),digest,files,manifest

def run(output,helm):
    output=Path(output);output.mkdir(mode=0o700,parents=True,exist_ok=False)
    workspace=output/'workspace';repo=workspace/'external-ran';repo.mkdir(mode=0o700,parents=True)
    data,digest,blobs,manifest_bytes=image_fixture();oci_layout(data,digest)
    (output/'external-ran.oci.tar').write_bytes(data)
    shutil.copytree(ROOT/'examples/telecom/ran-chart',repo/'chart')
    (repo/'chart/values.yaml').write_text('image: registry.example/offline/ran@'+digest+'\n')
    (repo/'Dockerfile').write_text('FROM scratch\nLABEL research.fixture=true\n')
    command(['git','init','--template=',str(repo)])
    command(['git','-C',str(repo),'add','.'])
    command(['git','-C',str(repo),'-c','user.name=Offline researcher','-c','user.email=research@example.invalid','commit','-m','Disposable external RAN fixture'])
    sha=command(['git','-C',str(repo),'rev-parse','HEAD']).decode().strip()
    declaration=json.loads((ROOT/'examples/telecom/ran.json').read_text())
    declaration['source']={'kind':'local-git','path':'external-ran','commit':sha}
    declaration['image'].update(repository='registry.example/offline/ran',digest=digest)
    (output/'manifest.json').write_bytes(canonical(declaration))
    principal=Principal('researcher','demo',frozenset({'researcher','builder','operator','publisher'}))
    reviewer=Principal('reviewer','demo',frozenset({'reviewer'}))
    service=Service(output/'runtime',workspace,helm=helm)
    policy=Policy({'local_roots':{'demo':'.'},'oci_repositories':['registry.example/offline/ran'],
                   'catalog_targets':{'demo':{'scope':'DISPOSABLE_RESEARCH_PROJECT','enabled':True,'project':'research-demo','host':'https://osm.example'}}})
    pipeline=Pipeline(service,policy);workload=service.register(principal,declaration,'register')
    job=pipeline.queue(principal,workload['id'],'chart','local-push-1')
    def registry(request,**kwargs):
        if not request.full_url.startswith('https://registry.example/v2/offline/ran/'):raise AssertionError('Network forbidden')
        key=request.full_url.rsplit('/',1)[1]
        return io.BytesIO(manifest_bytes if '/manifests/' in request.full_url else blobs['blobs/sha256/'+key[7:]])
    with patch('urllib.request.OpenerDirector.open',side_effect=registry):result=pipeline.run(principal,job['id'])
    if result['state']!='COMPLETED':raise RuntimeError('Offline job did not complete: '+json.dumps(result))
    release=service.journal.get(principal,'releases',result['release_id']);scenario=release['package']['scenario']
    request=pipeline.request(principal,release['id'],'catalog','request-catalog');approval=pipeline.approve(reviewer,request['id'],'approve-catalog')
    class OfflineCatalog(SimulatedCatalog):
        def request(self,method,path,data=None,content_type='application/yaml'):
            if method=='POST':self.pending=('knf',scenario['knf_package']) if 'vnfpkgm' in path else ('ns',scenario['nsd_package'])
            return super().request(method,path,data,content_type)
    publication=pipeline.publish(principal,request['id'],approval['id'],OfflineCatalog)
    update=pipeline.request(principal,release['id'],'update','request-update')
    for name,value in release['package']['artifacts'].items():(output/name).write_bytes(bytes.fromhex(value))
    summary={'status':'OFFLINE_TESTED','job_id':job['id'],'job_state':result['state'],'release_id':release['id'],
             'source_commit':sha,'source_acquisition':'REAL_LOCAL_GIT_OBJECTS','helm':'REAL_LINT_AND_RENDER',
             'image_digest':digest,'image_verification':'REAL_OCI_BYTES; FIXTURE_REGISTRY_TRANSPORT',
             'dockerfile_build':'NOT_RUN','catalog_state':publication['state'],'catalog_transport':'SIMULATED',
             'instance_update':update['state'],'package_digests':release['package']['package_digests'],
             'duplicate_event_same_job':pipeline.queue(principal,workload['id'],'chart','local-push-1')['id']==job['id'],
             'audit_integrity':service.journal.verify_audit(),'production_eligible':False,'production_calls':0}
    (output/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--helm',default='helm')
    args=parser.parse_args();print(json.dumps(run(args.output,args.helm),indent=2))
if __name__=='__main__':main()
