"""Approval-controlled create-only research NS adapter; never upgrades or deletes.

Uses the combined initial-instantiation endpoint already used by osm_client.py.
Transport compatibility and workload placement require a separately approved test.
"""
import json
import re
import time
import uuid
import yaml
from osm_catalog import identifier, parse_response
from assurance.model import PROTECTED_NS
from .pipeline import exclusive
from .contract import identity
from .security import Denied
from .pipeline import Pipeline, scoped_catalog
from .execution import command

class InitialDeployment:
    def __init__(self, service, policy):
        self.service,self.journal,self.policy=service,service.journal,policy

    def target(self, principal):
        target=self.policy.value.get('deployment_targets',{}).get(principal.tenant,{})
        catalog=self.policy.value.get('catalog_targets',{}).get(principal.tenant,{})
        if (target.get('scope')!='DISPOSABLE_RESEARCH_NAMESPACE' or
            catalog.get('scope')!='DISPOSABLE_RESEARCH_PROJECT' or
            catalog.get('project')!='research-'+principal.tenant or
            not re.fullmatch(r'research-[a-z0-9][a-z0-9-]{0,40}',target.get('namespace',''))):
            raise Denied('Dedicated research target required')
        for field in ('namespace_uid','cluster_uid','vim_id'):
            identifier(target.get(field))
        return target,catalog

    def request(self, principal, publication_id, event_id):
        principal.require('builder')
        publication=self.service.owned(principal,'publication-requests',publication_id)
        release=self.service.owned(principal,'releases',publication['release_id'])
        target,catalog=self.target(principal)
        if (publication.get('state')!='PUBLISHED_CONTENT_VERIFIED' or
            publication.get('target_hash')!=identity(catalog) or
            release.get('pipeline_version')!=2 or release.get('state')!='VERIFIED_PACKAGE' or
            release.get('package',{}).get('namespace_portable') is not True):
            raise Denied('Verified catalog publication required')
        Pipeline(self.service,self.policy).registry_gate(principal,release)
        ids=publication['diagnostic']['catalog_ids']
        identifier(ids['ns']);identifier(ids['knf'])
        key=identity({'tenant':principal.tenant,'publication':publication_id,'event':event_id})
        value={'id':key,'owner':principal.subject,'release_id':release['id'],
               'publication_id':publication_id,'target_hash':identity([target,catalog]),
               'namespace_identity':identity([target['cluster_uid'],target['namespace_uid']]),'ns_name':'research-'+key[:24],'state':'AWAITING_APPROVAL',
               'upgrade':'NOT_VERIFIED','rollback':'NOT_VERIFIED','protocol_verification':'NOT_RUN',
               'production_eligible':False,'history':[{'state':'AWAITING_APPROVAL'}]}
        return self.journal.event(principal,event_id,{'initial_request':value},
            lambda db:self.journal.put(db,principal,'deployments',key,value,immutable=True))

    def approve(self, principal, request_id, event_id):
        principal.require('reviewer');item=self.journal.get(principal,'deployments',request_id)
        if item['state']!='AWAITING_APPROVAL' or item['owner']==principal.subject:
            raise Denied('Independent initial-deployment approval required')
        value={'id':identity({'request':request_id,'event':event_id}),
               'request_id':request_id,'request_hash':identity(item),'reviewer':principal.subject,
               'expires':time.time()+300}
        return self.journal.event(principal,event_id,{'initial_approval':value['request_id']},
            lambda db:self.journal.put(db,principal,'deployment-approvals',value['id'],value,immutable=True))

    def snapshot(self, target, *, empty=False):
        # Server-only kubeconfig. Never accepts arbitrary kubectl arguments or executes pods.
        from .execution import private_json
        from urllib.parse import urlsplit
        import tempfile
        import os
        # JSON kubeconfig deliberately excludes executable auth plugins, local
        # certificate paths, proxy overrides and insecure TLS. Use a frozen copy.
        config=private_json(target['kubeconfig'])
        if set(config)-{'apiVersion','kind','clusters','contexts','users','current-context'}:
            raise Denied('Minimal JSON kubeconfig required')
        if config.get('apiVersion')!='v1' or config.get('kind')!='Config':raise Denied('Kubeconfig schema')
        if any(not isinstance(config.get(k),list) or len(config[k])!=1 for k in ('clusters','contexts','users')):
            raise Denied('Single isolated Kubernetes identity required')
        cluster=config['clusters'][0];user=config['users'][0];context=config['contexts'][0]
        connection=cluster['cluster'];credentials=user['user'];binding=context['context']
        if set(connection)!={'server','certificate-authority-data'} or set(credentials) not in ({'token'},{'client-certificate-data','client-key-data'}):
            raise Denied('Embedded TLS and static scoped credentials only')
        endpoint=urlsplit(connection['server'])
        if endpoint.scheme!='https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise Denied('Verified HTTPS Kubernetes endpoint required')
        if (set(binding)-{'cluster','user','namespace'} or context['name']!=config['current-context'] or
            binding['cluster']!=cluster['name'] or binding['user']!=user['name'] or
            binding.get('namespace',target['namespace'])!=target['namespace']):
            raise Denied('Isolated kubeconfig context required')
        with tempfile.TemporaryDirectory(prefix='research-kubeconfig-') as directory:
            path=os.path.join(directory,'config.json')
            descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(descriptor,'w') as stream:json.dump(config,stream)
            return self.read_snapshot(target,path,empty=empty)

    def read_snapshot(self, target, kubeconfig, *, empty=False):
        def read(resource, namespace=None):
            args=['kubectl','--kubeconfig',kubeconfig,'--request-timeout=5s']
            if namespace:args+=['-n',namespace]
            args+=['get',resource,'-o','json']
            return json.loads(command(args,timeout=7,limit=2*1024*1024))
        cluster=read('namespace/kube-system');ns=read('namespace/'+target['namespace'])
        if (cluster['metadata']['uid']!=target['cluster_uid'] or
            ns['metadata']['uid']!=target['namespace_uid'] or
            ns['metadata'].get('labels',{}).get('telecom.research/tenant')!=target['tenant']):
            raise Denied('Cluster/namespace ownership mismatch')
        if empty:
            inventory=read('deployments,statefulsets,daemonsets,replicasets,jobs,cronjobs',target['namespace'])
            if inventory.get('items')!=[]:raise Denied('Existing workload controllers in namespace')
        return read('pods',target['namespace'])['items']

    def save(self, principal, item, event):
        history=list(item.get('history',[]))
        if len(history)>=64:raise Denied('Deployment history retention review required')
        history.append({'state':item['state'],'at':time.time(),'actor':principal.subject})
        item=dict(item,history=history)
        return self.journal.event(principal,event,{'deployment':item},
            lambda db:self.journal.put(db,principal,'deployments',item['id'],item))

    def submit(self, principal, request_id, approval_id):
        principal.require('operator')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            item=self.service.owned(principal,'deployments',request_id)
            approval=self.journal.get(principal,'deployment-approvals',approval_id)
            target,catalog=self.target(principal)
            if (item['state']!='AWAITING_APPROVAL' or approval['request_id']!=request_id or
                approval['request_hash']!=identity(item) or approval['expires']<time.time() or
                identity([target,catalog])!=item['target_hash'] or target.get('tenant')!=principal.tenant or
                target.get('enabled') is not True or catalog.get('enabled') is not True):
                raise Denied('Fresh exact approval and enabled isolated targets required')
            release=self.journal.get(principal,'releases',item['release_id'])
            Pipeline(self.service,self.policy).registry_gate(principal,release)
            # All checks before intent are reads. Failure here sends no NS request.
            if self.snapshot(target,empty=True):raise Denied('Disposable namespace must initially be empty')
            client=scoped_catalog(self.policy,principal,catalog)
            package=release['package']
            ids=client.verify(package['scenario'],{k:bytes.fromhex(v) for k,v in package['artifacts'].items()})
            publication=self.journal.get(principal,'publication-requests',item['publication_id'])
            if ids!=publication['diagnostic']['catalog_ids']:raise Denied('Catalog identity drift')
            operations=parse_response(client.request('GET','/nslcm/v1/ns_lcm_op_occs'))
            if not isinstance(operations,list) or any(not isinstance(op,dict) or op.get('operationState') not in ('COMPLETED','FAILED','ROLLED_BACK') for op in operations):
                raise Denied('Lifecycle conflict or unavailable operation state')
            # Namespace reservation survives timeout/uncertain submissions across different requests.
            with self.journal.transaction() as db:
                rows=db.execute("SELECT data FROM objects WHERE kind='deployments'").fetchall()
                if any(json.loads(row[0]).get('namespace_identity')==item['namespace_identity'] and
                       json.loads(row[0]).get('state')!='AWAITING_APPROVAL' for row in rows):
                    raise Denied('Target already reserved; manual reconciliation required')
            item=dict(item,state='SUBMITTING',approval_id=approval_id,submitted_at=time.time())
            item=self.save(principal,item,'initial-submit-'+request_id)
            payload={'nsName':item['ns_name'],'nsDescription':'Disposable researcher onboarding',
                     'nsdId':ids['ns'],'vimAccountId':target['vim_id'],
                     'additionalParamsForVnf':[{'member-vnf-index':'nf',
                     'additionalParamsForKdu':[{'kdu_name':'nf','k8s-namespace':target['namespace']}]}]}
            try:
                response=parse_response(client.request('POST','/nslcm/v1/ns_instances_content',yaml.safe_dump(payload).encode()))
                ns=identifier(response['id']);op=identifier(response['nslcmop_id'])
                if ns in PROTECTED_NS:raise Denied('Protected identity returned')
                item=dict(item,state='SUBMITTED',instance_id=ns,operation_id=op)
            except Exception as error:
                item=dict(item,state='UNKNOWN_OUTCOME',diagnostic={'class':type(error).__name__,
                          'details':'SUPPRESSED','automatic_retry':False})
            return self.save(principal,item,'initial-outcome-'+request_id)

    def refresh(self, principal, request_id, event_id):
        principal.require('operator')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            item=self.service.owned(principal,'deployments',request_id)
            target,catalog=self.target(principal)
            if item['state'] not in ('SUBMITTED','VERIFYING','WORKLOAD_READY','UNAVAILABLE','FAILED') or identity([target,catalog])!=item['target_hash']:
                raise Denied('Known submitted research instance required')
            ns_id=identifier(item['instance_id']);op_id=identifier(item['operation_id'])
            if ns_id in PROTECTED_NS:raise Denied('Protected instance forbidden')
            try:
                client=scoped_catalog(self.policy,principal,catalog)
                ns=parse_response(client.request('GET','/nslcm/v1/ns_instances/'+ns_id))
                op=parse_response(client.request('GET','/nslcm/v1/ns_lcm_op_occs/'+op_id))
                release=self.journal.get(principal,'releases',item['release_id'])
                publication=self.journal.get(principal,'publication-requests',item['publication_id'])
                if ns.get('_id')!=ns_id or ns.get('name',ns.get('nsName'))!=item['ns_name'] or ns.get('nsd-id',ns.get('nsdId'))!=publication['diagnostic']['catalog_ids']['ns'] or op.get('nsInstanceId')!=ns_id or op.get('lcmOperationType')!='INSTANTIATE':
                    raise Denied('OSM instance/operation identity mismatch')
                pods=self.snapshot(target);image=release['image'];expected=image['repository']+'@'+image['digest']
                healthy=bool(pods)
                for pod in pods:
                    containers=pod.get('spec',{}).get('containers',[])
                    statuses=pod.get('status',{}).get('containerStatuses',[])
                    healthy=healthy and pod.get('metadata',{}).get('namespace')==target['namespace'] and not pod.get('metadata',{}).get('deletionTimestamp') and bool(containers) and len(containers)==len(statuses) and all(c.get('image')==expected for c in containers) and all(c.get('ready') is True and c.get('imageID','').split('@')[-1]==image['digest'] for c in statuses)
                state='FAILED' if op.get('operationState')=='FAILED' else 'VERIFYING'
                if op.get('operationState')=='COMPLETED' and ns.get('nsState')=='READY' and ns.get('operational-status')=='running' and ns.get('config-status')=='configured' and healthy:state='WORKLOAD_READY'
                observation={'provenance':'LIVE','at':time.time(),'pod_count':len(pods),
                             'image_digest_verified':bool(healthy),'application_health':'UNVERIFIED',
                             'telecom_protocols':'NOT_RUN'}
            except Exception as error:
                state='UNAVAILABLE';observation={'provenance':'UNAVAILABLE','at':time.time(),'error_class':type(error).__name__}
            return self.save(principal,dict(item,state=state,observation=observation),event_id)
