"""Authenticated digest-only OCI publication via maintained Skopeo client.

No Docker daemon, shell, mutable tags, anonymous writes or protocol reimplementation.
Debug HTTP traces are counted in memory and discarded; never journal raw tool output.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import ssl
import tarfile
import tempfile
import time
import uuid
from assurance.locking import exclusive
from .contract import digest,identity
from .execution import command,oci_layout,ExecutionError,MAX_IMAGE
from .security import Denied

class Traffic:
    def __init__(self,maximum):self.maximum=maximum;self.requests=0;self.tail=b'';self.reason=None
    def observe(self,stream,data):
        if stream!='stderr':return
        self.tail+=data
        lines=self.tail.split(b'\n');self.tail=lines.pop()
        if len(self.tail)>65536 or any(len(line)>65536 for line in lines):raise ExecutionError('registry','DIAGNOSTIC_LINE_BUDGET')
        for line in lines:
            if re.search(rb'\b(?:GET|HEAD|POST|PATCH|PUT|DELETE) http://',line):raise ExecutionError('registry','PLAINTEXT_TRANSPORT_REJECTED')
            self.requests+=len(re.findall(rb'\b(?:GET|HEAD|POST|PATCH|PUT|DELETE) https?://',line))
            lower=line.lower()
            if any(word in lower for word in (b'unauthorized',b'authentication required',b'statuscode: 401',b'statuscode: 403')):self.reason='REGISTRY_AUTHENTICATION_FAILED'
            if any(word in lower for word in (b'x509:',b'certificate verify',b'tls handshake',b'unknown authority')):self.reason='REGISTRY_TLS_VERIFICATION_FAILED'
            if self.requests>self.maximum:raise ExecutionError('registry','NETWORK_REQUEST_BUDGET')

class Publisher:
    def __init__(self,journal,policy):self.journal,self.policy=journal,policy
    def configuration(self,principal,repository):
        principal.require('builder')
        target=self.policy.value.get('registry_targets',{}).get(principal.tenant,{})
        if not re.fullmatch(r'[a-z0-9.-]+/[a-z0-9_./-]+',repository) or '..' in repository:raise Denied('Registry repository rejected')
        host=repository.split('/',1)[0]
        if '.' not in host or host.endswith('.') or repository.endswith('/') or '//' in repository:raise Denied('Canonical fully qualified registry/repository required')
        if target.get('scope')!='DISPOSABLE_RESEARCH_REPOSITORY' or target.get('enabled') is not True or target.get('authentication_required') is not True or not isinstance(target.get('repositories'),list) or repository not in target['repositories']:
            raise Denied('Explicit tenant-scoped research publication required')
        if not 1<=target.get('max_seconds',0)<=60 or not 1<=target.get('max_requests',0)<=256:
            raise Denied('Bounded registry execution policy required')
        if not re.fullmatch(r'skopeo version [0-9]+\.[0-9]+\.[0-9]+',target.get('client_version','')) or not target.get('transport_evidence'):
            raise Denied('Pinned client and reviewed request-trace contract required')
        # Credential purposes prevent reusing OSM/webhook/other-repository secrets.
        credentials=json.loads(self.policy.secret(target['secret_ref'],principal.tenant,'registry-publish',repository))
        if set(credentials)!={'username','password'} or not all(isinstance(credentials[k],str) and credentials[k] and len(credentials[k])<=4096 for k in credentials) or ':' in credentials['username']:
            raise Denied('Scoped registry username/password reference required')
        return target,credentials

    def _record(self,principal,key,state,**fields):
        def write(db):
            try:previous=self.journal.get(principal,'registry-publications',key,db)
            except Denied:previous={'id':key,'history':[]}
            previous.update(fields,state=state)
            previous['history'].append({'state':state,'at':time.time()})
            return self.journal.put(db,principal,'registry-publications',key,previous)
        return self.journal.event(principal,'registry-'+uuid.uuid4().hex,{'receipt':key,'state':state,'fields':fields},write)

    def publish(self,principal,repository,archive,expected,cancel=lambda:False):
        target,credentials=self.configuration(principal,repository)
        archive=Path(archive)
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size>MAX_IMAGE:raise Denied('Bounded regular OCI artifact required')
        content=archive.read_bytes();image=oci_layout(content,expected)
        # Validate exact media types and prohibit URL-addressed/foreign layers before
        # allowing a client to interpret the artifact.
        with tarfile.open(fileobj=io.BytesIO(content),mode='r:*') as stream:
            member=next((m for m in stream if m.name.removeprefix('./')=='blobs/sha256/'+expected[7:]),None)
            if member is None:raise ExecutionError('registry','MISSING_MANIFEST')
            manifest_bytes=stream.extractfile(member).read(1024*1024+1)
        manifest=json.loads(manifest_bytes)
        layers=manifest['layers']
        if len(layers)>32 or manifest['config'].get('mediaType')!='application/vnd.oci.image.config.v1+json' or 'urls' in manifest['config']:
            raise ExecutionError('registry','UNSUPPORTED_OCI_MEDIA_TYPE')
        for layer in layers:
            if layer.get('mediaType') not in {'application/vnd.oci.image.layer.v1.tar','application/vnd.oci.image.layer.v1.tar+gzip','application/vnd.oci.image.layer.v1.tar+zstd'} or 'urls' in layer:
                raise ExecutionError('registry','UNSUPPORTED_OCI_MEDIA_TYPE')
        key=identity({'tenant':principal.tenant,'repository':repository,'digest':digest(expected)})
        with exclusive(self.journal.directory/('registry-'+key+'.lock')):
            try:old=self.journal.get(principal,'registry-publications',key)
            except Denied:old=None
            if old and old['state']!='VERIFIED':raise Denied('Uncertain registry publication blocks replay; read-only reconciliation required')
            traffic=Traffic(target['max_requests']);deadline=time.monotonic()+target['max_seconds'];submitted=False
            if cancel():raise ExecutionError('registry','CANCELLED')
            with tempfile.TemporaryDirectory(prefix='registry-',dir=self.journal.directory) as temporary:
                root=Path(temporary);auth=root/'auth.json';auth.write_text(json.dumps({'auths':{repository:{'auth':base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()}}}));auth.chmod(0o600)
                # Empty registries config: no mirrors, insecure settings, search paths
                # or credential helpers inherited from the host.
                user_config=root/'.config/containers';user_config.mkdir(mode=0o700,parents=True)
                registries=user_config/'registries.conf';registries.write_text('unqualified-search-registries = []\ncredential-helpers = ["containers-auth.json"]\n')
                confdir=user_config/'registries.conf.d';confdir.mkdir();registries_d=root/'registries.d';registries_d.mkdir()
                tool_tmp=root/'tool-tmp';tool_tmp.mkdir()
                trust=root/'policy.json';trust.write_text(json.dumps({'default':[{'type':'reject'}],'transports':{'oci-archive':{'':[{'type':'insecureAcceptAnything'}]},'docker':{repository:[{'type':'insecureAcceptAnything'}]}}}))
                # insecureAcceptAnything is a signature-policy term, never TLS bypass:
                # byte digests are verified; publisher does not claim signature trust.
                certdir=root/'certs';certdir.mkdir()
                if target.get('ca_file'):
                    ca=Path(target['ca_file'])
                    if ca.is_symlink() or not ca.is_file():raise Denied('Registry CA reference rejected')
                    public=ca.read_bytes()
                    if len(public)>65536:raise Denied('Registry CA budget')
                    ssl.create_default_context(cadata=public.decode('ascii'))
                    (certdir/'ca.crt').write_bytes(public)
                env={'XDG_RUNTIME_DIR':temporary}
                def execute(argv,network=True):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise ExecutionError('registry','TIMEOUT')
                    before=traffic.requests
                    try:
                        value=command(argv,timeout=remaining,limit=1024*1024,cancel=cancel,observe=traffic.observe,
                                      extra_env=env,configuration_home=root,disk_root=root,disk_limit=MAX_IMAGE+2*1024*1024)
                    except ExecutionError as error:
                        if traffic.reason and error.code=='NONZERO_EXIT':raise ExecutionError('registry',traffic.reason,error.exit_code) from None
                        raise
                    if network and traffic.requests==before:raise ExecutionError('registry','UNVERIFIED_REQUEST_ACCOUNTING')
                    return value
                actual=execute(['skopeo','--version'],network=False).decode().strip()
                if actual!=target['client_version']:raise Denied('Skopeo version differs from reviewed transport')
                common=['skopeo','--debug','--tmpdir',str(tool_tmp),'--command-timeout',str(target['max_seconds'])+'s','--registries.d',str(registries_d),'--policy',str(trust)]
                remote='docker://'+repository+'@'+expected
                self._record(principal,key,'VERIFYING' if old else 'SUBMITTING',repository=repository,digest=expected,owner=principal.subject,policy_hash=identity(target))
                try:
                    if not old:
                        # Durable intent precedes the one permitted copy. A failed copy
                        # is uncertain, never automatically repeated by another job.
                        submitted=True
                        execute(common+['copy','--retry-times','0','--image-parallel-copies','1','--preserve-digests',
                            '--format','oci','--dest-authfile',str(auth),'--dest-tls-verify=true','--dest-cert-dir',str(certdir),
                            'oci-archive:'+str(archive),remote])
                    if cancel():raise ExecutionError('registry','CANCELLED')
                    raw=execute(common+['inspect','--raw','--retry-times','0','--authfile',str(auth),'--tls-verify=true','--cert-dir',str(certdir),remote])
                    if hashlib.sha256(raw).hexdigest()!=expected[7:] or raw!=manifest_bytes:raise ExecutionError('registry','REGISTRY_DIGEST_MISMATCH')
                    if cancel():raise ExecutionError('registry','CANCELLED')
                    return self._record(principal,key,'VERIFIED',repository=repository,digest=expected,verification='REGISTRY_MANIFEST_VERIFIED',
                        verified_at=time.time(),network_requests=traffic.requests,client_version=actual,reused_verified_publication=bool(old),
                        manifest_media_type=manifest['mediaType'],platform=image['platform'],signature_verification='NOT_CLAIMED',
                        request_accounting='PINNED_CLIENT_DEBUG_WATCHDOG; NOT_WIRE_CAPTURE',request_budget=target['max_requests'])
                except BaseException as error:
                    diagnostic=error.evidence() if isinstance(error,ExecutionError) else {'stage':'registry','code':type(error).__name__,'private_details':'SUPPRESSED'}
                    self._record(principal,key,'UNKNOWN_OUTCOME' if submitted or old else 'FAILED',diagnostic=diagnostic,network_requests=traffic.requests,
                                 cancelled=isinstance(error,ExecutionError) and error.code=='CANCELLED',automatic_retry=False)
                    raise
