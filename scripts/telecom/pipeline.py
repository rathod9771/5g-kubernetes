"""Explicit one-job CI execution; queue ingress never deploys or starts a worker."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from assurance.locking import exclusive
from assurance.model import PROTECTED_NS
from osm_catalog import Catalog,identifier,parse_response
from .contract import canonical, confined, identity, validate
from .execution import Policy, ExecutionError, acquire, build, prebuilt, verify_webhook, helm_source, retain_image, SHA
from .package import prepare
from .registry import Publisher
from .security import Denied

def scoped_catalog(policy, principal, target):
    """Existing NBI transport with non-default project-scoped credentials."""
    client=Catalog(target['host'],ca_file=target.get('ca_file'))
    credentials=policy.secret(target['secret_ref'],principal.tenant,'osm-credentials',target['host']+'#'+target['project'])
    credentials=json.loads(credentials)
    if not isinstance(credentials,dict) or set(credentials)!={'username','password'} or not all(isinstance(credentials.get(k),str) and credentials[k] for k in ('username','password')):
        raise Denied('Explicit scoped OSM credentials required')
    if credentials['username'].lower() in ('admin','root') or credentials['password'].lower() in ('admin','password','changeme','admin123'):
        raise Denied('Non-default research-scoped OSM identity required')
    project_id=identifier(target.get('project_id'))
    client.authenticate(credentials['username'],credentials['password'],project_id)
    project=parse_response(client.request('GET','/admin/v1/projects/'+project_id))
    if not isinstance(project,dict) or project.get('_id')!=project_id or project.get('name')!=target['project']:
        raise Denied('OSM research project identity/readback differs')
    return client

class Pipeline:
    def __init__(self, service, policy):
        self.service,self.journal,self.policy=service,service.journal,policy

    def local_workspace(self, principal):
        relative=self.policy.value.get('local_roots',{}).get(principal.tenant)
        if not relative: raise Denied('Tenant source root not configured')
        return confined(self.service.workspace,relative)

    def queue(self, principal, workload_id, chart, event_id, commit=None, image_mode='prebuilt', secret_ref=None):
        principal.require('builder')
        workload=self.service.owned(principal,'workloads',workload_id)
        if not isinstance(chart,str) or not chart or Path(chart).is_absolute() or '..' in Path(chart).parts:
            raise Denied('Relative chart path required')
        if image_mode not in ('prebuilt','dockerfile'): raise ValueError('Unknown image mode')
        source=copy.deepcopy(workload['manifest']['source'])
        if commit is not None:
            if source['kind'] not in ('local-git','github') or not SHA.fullmatch(commit): raise Denied('Pinned Git trigger required')
            source['commit']=commit
        if secret_ref is not None:
            if not isinstance(secret_ref,str) or len(secret_ref)>64: raise Denied('Bounded secret reference required')
            purpose='registry-publish' if image_mode=='dockerfile' else 'registry-bearer'
            self.policy.secret(secret_ref,principal.tenant,purpose,workload['manifest']['image']['repository'])
        specification={'workload':workload_id,'chart':chart,'source':source,'image_mode':image_mode,'secret_ref':secret_ref}
        key=identity({'tenant':principal.tenant,'event':event_id,'specification':specification})
        def write(db):
            value={'id':key,'tenant':principal.tenant,'owner':principal.subject,'state':'QUEUED','specification':specification,
                   'history':[{'state':'QUEUED','at':time.time()}],'deployment':'NOT_REQUESTED','schema_version':1}
            return self.journal.put(db,principal,'jobs',key,value,immutable=True)
        return self.journal.event(principal,event_id,{'action':'queue','specification':specification},write)

    def webhook(self, principal, workload_id, raw, signature, delivery, chart):
        principal.require('builder')
        workload=self.service.owned(principal,'workloads',workload_id)
        binding=self.policy.value.get('webhooks',{}).get(workload_id,{})
        if binding.get('tenant')!=principal.tenant: raise Denied('Webhook binding unavailable')
        secret=self.policy.secret(binding['secret_ref'],principal.tenant,'github-webhook',workload_id)
        payload=verify_webhook(raw,signature,secret)
        expected=workload['manifest']['source']
        if expected['kind']!='github' or payload.get('repository',{}).get('html_url')!=expected['url'].removesuffix('.git') or payload.get('ref')!=binding.get('ref'):
            raise Denied('Webhook repository/ref mismatch')
        self.policy.repository(expected['url'])
        if chart!=binding.get('chart'): raise Denied('Webhook chart mismatch')
        return self.queue(principal,workload_id,chart,'github-'+delivery,payload['after'],binding.get('image_mode','prebuilt'))

    def _state(self, principal, job, state, **fields):
        def write(db):
            value=self.journal.get(principal,'jobs',job['id'],db)
            value.update(fields,state=state)
            value['history'].append({'state':state,'at':time.time()})
            return self.journal.put(db,principal,'jobs',value['id'],value)
        # Unique transition IDs. No network calls occur in this transaction.
        return self.journal.event(principal,'state-'+identity({'job':job['id'],'state':state}),{'job':job['id'],'state':state,'fields':fields},write)

    def cancel(self, principal, job_id, event_id):
        principal.require('builder');job=self.service.owned(principal,'jobs',job_id)
        def write(db):
            return self.journal.put(db,principal,'cancellations',job_id,{'job_id':job_id,'requested':True},immutable=True)
        self.journal.event(principal,event_id,{'action':'cancel','job':job_id},write)
        return {'job_id':job_id,'cancellation':'REQUESTED','remote_lifecycle_cancellation':'NEVER_SENT'}

    def cancelled(self, principal, key):
        try: self.journal.get(principal,'cancellations',key);return True
        except Denied:return False

    def run(self, principal, job_id):
        principal.require('builder');job=self.service.owned(principal,'jobs',job_id)
        lock=self.journal.directory/('pipeline-'+identity({'tenant':principal.tenant,'workload':job['specification']['workload']})+'.lock')
        with exclusive(self.journal.directory/'.pipeline-execution.lock'), exclusive(lock):
            job=self.journal.get(principal,'jobs',job_id)
            if job['state']!='QUEUED': raise Denied('Job already claimed; inspect outcome, never replay automatically')
            spec=job['specification'];workload=self.service.owned(principal,'workloads',spec['workload'])
            manifest=copy.deepcopy(workload['manifest']);manifest['source']=spec['source']
            cancel=lambda:self.cancelled(principal,job_id)
            stage='source'
            self._state(principal,job,'ACQUIRING')
            started=time.monotonic()
            try:
                if cancel(): raise ExecutionError(stage,'CANCELLED')
                with tempfile.TemporaryDirectory(prefix='job-',dir=self.journal.directory) as temporary:
                    root=Path(temporary);workspace=self.local_workspace(principal)
                    if spec['source']['kind'] in ('local-git','github'):
                        evidence=acquire(spec['source'],workspace,root/'source',self.policy,cancel)
                        source_root=root/'source'
                    elif spec['source']['kind']=='helm':
                        evidence=helm_source(spec['source'],root/'source',self.policy);source_root=root/'source'
                    else:
                        evidence={'authority':'PROVIDER_DECLARED','source':spec['source']};source_root=workspace
                    stage='image';self._state(principal,job,'VERIFYING_IMAGE',source=evidence)
                    if spec['image_mode']=='dockerfile':
                        if spec['source']['kind'] not in ('local-git','github'): raise Denied('Dockerfile requires captured Git source')
                        publisher=Publisher(self.journal,self.policy)
                        # Fail before an expensive build if publication is not scoped.
                        publisher.configuration(principal,manifest['image']['repository'])
                        self._state(principal,job,'BUILDING')
                        image=build(source_root,root/'image.tar',self.policy,principal.tenant,cancel)
                        self._state(principal,job,'ARTIFACT_VERIFIED',image_digest=image['digest'])
                        manifest['image']['digest']=image['digest']
                        storage=self.journal.directory/'images'
                        destination=retain_image((root/'image.tar').read_bytes(),storage,image['digest'])
                        image['retained_artifact']='images/'+destination.name
                        stage='registry';self._state(principal,job,'PUBLISHING_IMAGE',image_digest=image['digest'])
                        receipt=publisher.publish(principal,manifest['image']['repository'],destination,image['digest'],cancel)
                        image['registry_publication']='REGISTRY_MANIFEST_VERIFIED'
                        image['registry']={'receipt_id':receipt['id'],'repository':receipt['repository'],'digest':receipt['digest']}
                        self._state(principal,job,'REGISTRY_VERIFIED',registry=image['registry'])
                    else:
                        image=prebuilt(manifest['image']['repository'],manifest['image']['digest'],self.policy,principal.tenant,spec['secret_ref'])
                    if image['platform']!=manifest['image']['platform']: raise ExecutionError(stage,'PLATFORM_MISMATCH')
                    if cancel(): raise ExecutionError(stage,'CANCELLED')
                    stage='package';self._state(principal,job,'PACKAGING',image=image)
                    # Only the documented scalar values.image is rebound to verified output.
                    # Rendering and package archives both use these same captured values.
                    manifest=validate(manifest)
                    self._state(principal,job,'VALIDATING_PACKAGE')
                    package=prepare(source_root,spec['chart'],manifest,self.service.helm,verified_image=(manifest['image']['repository']+'@'+image['digest']) if spec['image_mode']=='dockerfile' else None)
                    if cancel(): raise ExecutionError(stage,'CANCELLED')
                    self._state(principal,job,'PACKAGE_VALIDATED',package_digests=package['package_digests'])
                    key=identity({'tenant':principal.tenant,'workload':workload['id'],'source':evidence,'image':image['digest'],'packages':package['package_digests']})
                    value={'id':key,'tenant':principal.tenant,'owner':principal.subject,'schema_version':1,'pipeline_version':2,
                           'workload_id':workload['id'],'job_id':job_id,'deployment_approval':'REQUIRED; INSTANCE_EXECUTION_DISABLED','state':'VERIFIED_PACKAGE','source':evidence,'image':manifest['image'],
                           'image_verification':image,'manifest_digest':'sha256:'+identity(manifest),'package':package,
                           'provenance':'CONTENT_VERIFIED','production_eligible':False,
                           'protocol_verification':'NOT_VERIFIED','history':self.journal.get(principal,'jobs',job_id)['history']+[{'state':'VERIFIED_PACKAGE','at':time.time()}]}
                    def seal(db):
                        try:existing=self.journal.get(principal,'releases',key,db)
                        except Denied:return self.journal.put(db,principal,'releases',key,value,immutable=True)
                        # Content identity is shared across explicit jobs. Keep the first
                        # sealed release/history instead of rewriting its creation time.
                        for field in ('workload_id','source','image','manifest_digest'):
                            if existing[field]!=value[field]:raise Denied('Immutable release collision')
                        if existing['package']['package_digests']!=value['package']['package_digests']:
                            raise Denied('Immutable package collision')
                        return existing
                    self.journal.event(principal,'release-'+job_id,{'job':job_id,'release':key},seal)
                    return self._state(principal,job,'COMPLETED',release_id=key,duration_seconds=time.monotonic()-started)
            except Exception as error:
                diagnostic=error.evidence() if isinstance(error,ExecutionError) else {'stage':stage,'code':type(error).__name__,'private_details':'SUPPRESSED'}
                return self._state(principal,job,'CANCELLED' if diagnostic['code']=='CANCELLED' else 'FAILED',diagnostic=diagnostic,duration_seconds=time.monotonic()-started)
            except BaseException:
                self._state(principal,job,'INTERRUPTED',diagnostic={'stage':stage,'code':'PARENT_CANCELLED'})
                raise

    def registry_gate(self,principal,release):
        image=release.get('image_verification',{})
        if image.get('builder')=='REMOTE_BUILDKIT_TLS':
            reference=image.get('registry',{})
            if image.get('registry_publication')!='REGISTRY_MANIFEST_VERIFIED':raise Denied('Registry publication/readback required')
            receipt=self.journal.get(principal,'registry-publications',reference.get('receipt_id',''))
            target=self.policy.value.get('registry_targets',{}).get(principal.tenant,{})
            if not target.get('enabled') or release['image']['repository'] not in target.get('repositories',[]) or receipt.get('policy_hash')!=identity(target):raise Denied('Registry authorization or policy changed')
            if receipt.get('state')!='VERIFIED' or receipt.get('digest')!=release['image']['digest'] or receipt.get('repository')!=release['image']['repository'] or not 0<=time.time()-receipt.get('verified_at',0)<=300:
                raise Denied('Fresh exact registry publication evidence required')
        elif image.get('verification')!='REGISTRY_CONTENT_VERIFIED' or image.get('digest')!=release['image']['digest']:
            raise Denied('Registry image content verification required')

    def request(self, principal, release_id, action, event_id):
        principal.require('builder' if action=='catalog' else 'operator')
        release=self.service.owned(principal,'releases',release_id)
        if action not in ('catalog','update','rollback'): raise ValueError('Unsupported request')
        if release.get('pipeline_version')!=2 or release.get('state')!='VERIFIED_PACKAGE': raise Denied('Content-verified package required')
        if action=='catalog':self.registry_gate(principal,release)
        target=self.policy.value.get('catalog_targets',{}).get(principal.tenant) if action=='catalog' else None
        if action=='catalog' and (not target or target.get('scope')!='DISPOSABLE_RESEARCH_PROJECT'): raise Denied('Disposable project policy required')
        # Instance updates deliberately have no executable capability profile yet.
        key=identity({'tenant':principal.tenant,'release':release_id,'action':action,'event':event_id})
        value={'id':key,'release_id':release_id,'action':action,'owner':principal.subject,'target_hash':identity(target),
               'package_digests':release['package']['package_digests'],'state':'AWAITING_APPROVAL' if action=='catalog' else 'NOT_VERIFIED',
               'lifecycle_status':'NOT_VERIFIED','rollback_eligible':False,'production_execution':'DISABLED'}
        return self.journal.event(principal,event_id,{'action':'request','request':value},lambda db:self.journal.put(db,principal,'publication-requests',key,value,immutable=True))

    def approve(self, principal, request_id, event_id):
        principal.require('reviewer');item=self.journal.get(principal,'publication-requests',request_id)
        if item['state']!='AWAITING_APPROVAL' or item['owner']==principal.subject: raise Denied('Independent catalog approval required')
        def write(db):
            approval={'id':identity({'request':request_id,'event':event_id}),'request_id':request_id,'request_hash':identity(item),
                      'reviewer':principal.subject,'expires':time.time()+300}
            return self.journal.put(db,principal,'publication-approvals',approval['id'],approval,immutable=True)
        return self.journal.event(principal,event_id,{'action':'approve-publication','request':request_id},write)

    def publish(self, principal, request_id, approval_id, catalog_factory=None):
        """At most one submission sequence. Any uncertainty blocks automatic replay.
        Only new immutable names may be created; existing production entries never PUT.
        """
        principal.require('publisher')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            item=self.journal.get(principal,'publication-requests',request_id)
            approval=self.journal.get(principal,'publication-approvals',approval_id)
            if item['state']!='AWAITING_APPROVAL' or approval['request_id']!=request_id or approval['request_hash']!=identity(item) or approval['expires']<time.time():
                raise Denied('Fresh exact approval required')
            target=self.policy.value.get('catalog_targets',{}).get(principal.tenant)
            if not target or identity(target)!=item['target_hash'] or target.get('scope')!='DISPOSABLE_RESEARCH_PROJECT' or target.get('project')!='research-'+principal.tenant or not target.get('enabled'):
                raise Denied('Catalog execution disabled or target drifted')
            release=self.journal.get(principal,'releases',item['release_id'])
            self.registry_gate(principal,release)
            if release['package']['package_digests']!=item['package_digests']: raise Denied('Package drift')
            def intent(db):
                value=dict(item,state='SUBMITTING',approval_id=approval_id)
                return self.journal.put(db,principal,'publication-requests',request_id,value)
            item=self.journal.event(principal,'submit-'+request_id,{'request':request_id,'approval':approval_id},intent)
            try:
                if catalog_factory:
                    client=catalog_factory()  # test dependency only; never an HTTP field
                else:
                    client=scoped_catalog(self.policy,principal,target)
                package=release['package'];scenario=package['scenario']
                artifacts={name:bytes.fromhex(content) for name,content in package['artifacts'].items()}
                for name,content in artifacts.items():
                    expected=package['package_digests'][name]
                    if 'sha256:'+__import__('hashlib').sha256(content).hexdigest()!=expected: raise Denied('Package digest mismatch')
                # Preflight both names before the first write. Cross-client races still
                # require exclusive project credentials; no unconditional Catalog.upload.
                for kind,field in [('knf','knf_package'),('ns','nsd_package')]:
                    if client.lookup(kind,scenario[field],required=False): raise Denied('Catalog name conflict; immutable names only')
                for kind,field in [('knf','knf_package'),('ns','nsd_package')]:
                    name=scenario[field]
                    # Reuse the existing client transport/content paths, but never PUT.
                    path='/vnfpkgm/v1/vnf_packages_content' if kind=='knf' else '/nsd/v1/ns_descriptors_content'
                    client.request('POST',path,artifacts[name+'.tar.gz'],'application/gzip')
                identities=client.verify(scenario,artifacts)
                state='PUBLISHED_CONTENT_VERIFIED';diagnostic={'catalog_ids':identities,'instance_update':'NOT_REQUESTED'}
            except Exception as error:
                state='UNKNOWN_OUTCOME';diagnostic={'code':type(error).__name__,'private_details':'SUPPRESSED','automatic_retry':False}
            def finish(db):
                value=dict(item,state=state,diagnostic=diagnostic)
                return self.journal.put(db,principal,'publication-requests',request_id,value)
            return self.journal.event(principal,'outcome-'+request_id,{'state':state,'diagnostic':diagnostic},finish)
