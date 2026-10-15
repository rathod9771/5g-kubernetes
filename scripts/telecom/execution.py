"""Bounded source/OCI execution. No shell, build hooks, Docker socket or deployment.

BuildKit must be an independently provisioned, quota-enforced disposable worker.
Server policy, never request JSON, supplies endpoints, certificates and secret refs.
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
from osm_catalog import NoRedirect
from .contract import canonical, confined, digest, identity
from .security import Denied

MAX_SOURCE=16*1024*1024
MAX_IMAGE=128*1024*1024
SHA=re.compile(r'[0-9a-f]{40}')

class ExecutionError(ValueError):
    def __init__(self, stage, code, exit_code=None):
        self.stage,self.code,self.exit_code=stage,code,exit_code
        super().__init__(stage+': '+code)
    def evidence(self):
        return {'stage':self.stage,'code':self.code,'exit_code':self.exit_code,
                'signal':-self.exit_code if self.exit_code is not None and self.exit_code<0 else None}

def private_json(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise Denied('Private server policy required')
        data=stream.read(65537)
    if len(data)>65536: raise Denied('Server policy size exceeded')
    return json.loads(data)

class Policy:
    def __init__(self, value):
        if not isinstance(value,dict):raise Denied('Object execution policy required')
        for field in ('github_repositories','oci_repositories','helm_charts'):
            items=value.get(field,[])
            if not isinstance(items,list) or any(not isinstance(item,str) for item in items):
                raise Denied('Explicit policy allowlist required')
        for field in ('local_roots','secrets','webhooks','catalog_targets','build_worker','instance_ownership','registry_targets','deployment_targets'):
            if not isinstance(value.get(field,{}),dict):raise Denied('Object policy section required')
        self.value=value
    @classmethod
    def environment(cls):
        path=os.environ.get('TELECOM_EXECUTION_POLICY')
        if not path: raise Denied('Execution policy not configured')
        return cls(private_json(path))
    def secret(self, reference, tenant, purpose, resource):
        item=self.value.get('secrets',{}).get(reference)
        if not isinstance(item,dict) or item.get('tenant')!=tenant or item.get('purpose')!=purpose or not isinstance(item.get('resources'),list) or resource not in item['resources']:
            raise Denied('Secret reference unavailable for this purpose/resource')
        value=private_json(item['file']).get('value')
        if not isinstance(value,str) or not 32<=len(value)<=8192 or any(c in value for c in '\r\n'):
            raise Denied('Invalid referenced secret')
        return value
    def repository(self, url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port:
            raise Denied('Only approved GitHub HTTPS repositories accepted')
        if not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?',parsed.path):
            raise Denied('Repository URL rejected')
        if url not in self.value.get('github_repositories',[]): raise Denied('Repository not allowlisted')
        return url

def command(argv, *, timeout=30, limit=MAX_SOURCE, cancel=lambda:False, cwd=None, disk_root=None, disk_limit=MAX_IMAGE, observe=None, extra_env=None, configuration_home=None):
    """No inherited credentials/config, bounded pipes/RSS/time, kill entire group.
    Only trusted Git/BuildKit client commands enter here; Dockerfile RUN is remote.
    """
    from assurance.memory import tree_memory, MemoryMeasurementError
    with tempfile.TemporaryDirectory(prefix='telecom-client-') as home:
        env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':home,'LANG':'C.UTF-8',
             'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,
             'GIT_TERMINAL_PROMPT':'0','GIT_PROTOCOL_FROM_USER':'0','GOMAXPROCS':'1',
             'GIT_ALLOW_PROTOCOL':'https','GIT_LFS_SKIP_SMUDGE':'1'}
        if configuration_home is not None:
            selected=Path(configuration_home)
            if selected.is_symlink() or not selected.is_dir() or selected.stat().st_uid!=os.getuid() or selected.stat().st_mode&0o077:raise Denied('Private client configuration home required')
            env['HOME']=str(selected);env['XDG_CONFIG_HOME']=str(selected/'.config')
        if extra_env:
            if set(extra_env)-{'CONTAINERS_REGISTRIES_CONF','CONTAINERS_REGISTRIES_CONF_DIR','XDG_RUNTIME_DIR'}:raise Denied('Client environment override rejected')
            env.update(extra_env)
        proc=subprocess.Popen(argv,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        started=time.monotonic(); disk_checked=0; buffers={proc.stdout:bytearray(),proc.stderr:bytearray()};total=0;tracked={}
        try:
            with selectors.DefaultSelector() as selector:
                for stream in buffers:
                    os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
                while selector.get_map() or proc.poll() is None:
                    if cancel(): raise ExecutionError('command','CANCELLED')
                    if disk_root and time.monotonic()-disk_checked>.25:
                        disk_checked=time.monotonic();size=0;entries=0
                        for directory,dirs,files in os.walk(disk_root,followlinks=False):
                            entries+=len(dirs)+len(files)
                            if entries>8192: raise ExecutionError('command','DISK_INVENTORY_BUDGET')
                            for name in files:
                                try: size+=(Path(directory)/name).lstat().st_size
                                except FileNotFoundError: pass
                                if size>disk_limit: raise ExecutionError('command','DISK_BUDGET')
                    if time.monotonic()-started>timeout: raise ExecutionError('command','TIMEOUT')
                    if proc.poll() is None:
                        try:
                            if tree_memory(proc.pid,tracked)['rss_sum_bytes']>256*1024*1024:
                                raise ExecutionError('command','RSS_BUDGET')
                        except MemoryMeasurementError:
                            if proc.poll() is None: raise ExecutionError('command','MEMORY_ACCOUNTING_UNAVAILABLE')
                    for key,_ in selector.select(.02):
                        data=os.read(key.fileobj.fileno(),65536)
                        if not data: selector.unregister(key.fileobj);continue
                        total+=len(data)
                        if total>limit: raise ExecutionError('command','OUTPUT_BUDGET')
                        if observe is not None:observe('stderr' if key.fileobj is proc.stderr else 'stdout',data)
                        buffers[key.fileobj].extend(data)
            code=proc.wait(timeout=2)
            if code: raise ExecutionError('command','NONZERO_EXIT',code)
            return bytes(buffers[proc.stdout])
        finally:
            # Also remove descendants that outlive a normally exiting client.
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait(timeout=5)
            proc.stdout.close();proc.stderr.close()

GIT=['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',
     '-c','protocol.allow=never','-c','protocol.https.allow=always',
     '-c','http.followRedirects=false','-c','credential.helper=',
     '-c','core.attributesFile=/dev/null','-c','filter.lfs.required=false']

def unpack(data, destination, maximum=MAX_SOURCE):
    """Extract only ordinary source files/directories, never links or archive paths."""
    if len(data)>maximum: raise ExecutionError('source','ARCHIVE_BUDGET')
    destination=Path(destination);destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    total=0;seen=set();count=0
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as archive:
        for member in archive:
            count+=1;name=PurePosixPath(member.name)
            if count>1024 or len(name.parts)>16 or len(member.name)>512 or name.is_absolute() or '..' in name.parts or '.git' in name.parts or member.name in seen:
                raise ExecutionError('source','UNSAFE_ARCHIVE')
            seen.add(member.name)
            target=destination.joinpath(*name.parts)
            if member.isdir(): target.mkdir(mode=0o700,parents=True,exist_ok=True);continue
            if not member.isfile() or member.size>maximum or member.size<0: raise ExecutionError('source','UNSAFE_ARCHIVE')
            total+=member.size
            if total>maximum: raise ExecutionError('source','EXPANSION_BUDGET')
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with archive.extractfile(member) as stream: content=stream.read(member.size+1)
            if len(content)!=member.size: raise ExecutionError('source','TRUNCATED_ARCHIVE')
            with target.open('xb') as out: out.write(content)
            target.chmod(0o700 if member.mode&0o111 else 0o600)
    return {'archive_digest':'sha256:'+hashlib.sha256(data).hexdigest(),'files':count,'bytes':total}

def acquire(source, workspace, destination, policy, cancel=lambda:False):
    sha=source.get('commit','')
    if not SHA.fullmatch(sha): raise ExecutionError('source','PINNED_SHA_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='telecom-git-') as temporary:
        if source['kind']=='local-git':
            repo=confined(workspace,source['path'])
            # Read object storage through a fresh config-free bare repository. Never
            # load repository config, filters, submodules, hooks or worktree content.
            if repo.is_symlink() or not repo.is_dir(): raise Denied('Local repository unavailable')
            objects=(repo/'.git/objects') if (repo/'.git').is_dir() else repo/'objects'
            objects=confined(workspace,str(objects))
            if not objects.is_dir(): raise Denied('Ordinary local Git object directory required')
            if (objects/'info/alternates').exists(): raise Denied('External object alternates forbidden')
            # No symlinks anywhere in object storage: Git otherwise follows packs.
            count=0
            for directory,dirs,files in os.walk(objects,followlinks=False):
                count+=len(dirs)+len(files)
                if count>4096: raise Denied('Local object inventory budget exceeded')
                if any((Path(directory)/n).is_symlink() for n in dirs+files): raise Denied('Object symlink forbidden')
            fresh=Path(temporary)/'repo'
            command(GIT+['init','--bare','--template=',str(fresh)],cancel=cancel)
            (fresh/'objects/info/alternates').write_text(str(objects)+'\n')
            repo=fresh
        elif source['kind']=='github':
            url=policy.repository(source['url']);repo=Path(temporary)/'repo'
            command(GIT+['init','--bare','--template=',str(repo)],cancel=cancel)
            # Public GitHub only initially. No credentials are sent in URLs/env.
            command(GIT+['-C',str(repo),'fetch','--depth=1','--no-tags','--no-recurse-submodules',url,sha],cancel=cancel,disk_root=temporary,disk_limit=MAX_SOURCE)
        else: raise ExecutionError('source','GIT_SOURCE_REQUIRED')
        found=command(GIT+['-C',str(repo),'rev-parse','--verify',sha+'^{commit}'],cancel=cancel).decode().strip()
        if found!=sha: raise ExecutionError('source','COMMIT_MISMATCH')
        data=command(GIT+['-C',str(repo),'archive','--format=tar',sha],cancel=cancel)
        # Git archives represent submodules as empty directories: never claim complete source.
        tree=command(GIT+['-C',str(repo),'ls-tree','-r',sha],cancel=cancel)
        if any(line.startswith(b'160000 ') for line in tree.splitlines()): raise ExecutionError('source','SUBMODULE_NOT_SUPPORTED')
        result=unpack(data,destination)
        result.update(commit=sha,authority='LOCAL_OBJECTS_VERIFIED' if source['kind']=='local-git' else 'GITHUB_FETCH_VERIFIED')
        return result

def oci_layout(data, expected=None):
    """Verify OCI manifest/config/layer bytes, not just a tool's claimed digest."""
    if len(data)>MAX_IMAGE: raise ExecutionError('image','IMAGE_BUDGET')
    checked_oci_tar(data)
    files={};total=0
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as archive:
        for index,member in enumerate(archive):
            name=member.name.removeprefix('./')
            if member.name.startswith('/') or '..' in PurePosixPath(member.name).parts:
                raise ExecutionError('image','INVALID_OCI_ARCHIVE')
            if member.isdir():
                if name.rstrip('/') not in ('','.','blobs','blobs/sha256'):raise ExecutionError('image','INVALID_OCI_ARCHIVE')
                continue
            if index>1024 or not member.isfile() or name in files or not (name in ('index.json','oci-layout') or re.fullmatch(r'blobs/sha256/[0-9a-f]{64}',name)):
                raise ExecutionError('image','INVALID_OCI_ARCHIVE')
            total+=member.size
            if member.size<0 or total>MAX_IMAGE: raise ExecutionError('image','IMAGE_BUDGET')
            with archive.extractfile(member) as stream: files[name]=stream.read(member.size+1)
    def blob(descriptor):
        value=digest(descriptor['digest']);content=files.get('blobs/sha256/'+value.split(':')[1],b'')
        if hashlib.sha256(content).hexdigest()!=value.split(':')[1] or len(content)!=descriptor['size']:
            raise ExecutionError('image','DIGEST_MISMATCH')
        return content
    if json.loads(files['oci-layout'])!={'imageLayoutVersion':'1.0.0'}: raise ExecutionError('image','INVALID_LAYOUT')
    if len(files['index.json'])>65536: raise ExecutionError('image','INDEX_BUDGET')
    index=json.loads(files['index.json'])
    if len(index['manifests'])!=1: raise ExecutionError('image','SINGLE_PLATFORM_REQUIRED')
    descriptor=index['manifests'][0];raw=blob(descriptor)
    if len(raw)>1024*1024: raise ExecutionError('image','MANIFEST_BUDGET')
    manifest=json.loads(raw)
    if manifest.get('schemaVersion')!=2 or manifest.get('mediaType')!='application/vnd.oci.image.manifest.v1+json':
        raise ExecutionError('image','OCI_MANIFEST_REQUIRED')
    config_raw=blob(manifest['config'])
    if len(config_raw)>1024*1024 or len(manifest['layers'])>128: raise ExecutionError('image','METADATA_BUDGET')
    config=json.loads(config_raw)
    for layer in manifest['layers']: blob(layer)
    result={'digest':descriptor['digest'],'platform':config['os']+'/'+config['architecture'],
            'layers':len(manifest['layers']),'verification':'OCI_CONTENT_VERIFIED'}
    if expected and result['digest']!=digest(expected): raise ExecutionError('image','DIGEST_MISMATCH')
    return result

def prebuilt(repository, expected, policy, tenant, secret_ref=None):
    """Digest-addressed HTTPS registry fetch. No redirects/auth challenge forwarding."""
    digest(expected)
    if not re.fullmatch(r'[a-z0-9.-]+/[a-z0-9_./-]+',repository) or '..' in repository:
        raise Denied('Explicit registry/repository required')
    host,name=repository.split('/',1)
    if repository not in policy.value.get('oci_repositories',[]): raise Denied('Registry repository not approved')
    headers={'Accept':'application/vnd.oci.image.manifest.v1+json'}
    if secret_ref: headers['Authorization']='Bearer '+policy.secret(secret_ref,tenant,'registry-bearer',repository)
    import ssl
    opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()),NoRedirect(),urllib.request.ProxyHandler({}))
    total=0;deadline=time.monotonic()+30
    def get(kind, reference):
        nonlocal total
        remaining=deadline-time.monotonic()
        if remaining<=0: raise ExecutionError('registry','TIMEOUT')
        request=urllib.request.Request('https://'+host+'/v2/'+name+'/'+kind+'/'+reference,headers=headers)
        try:
            with opener.open(request,timeout=min(10,remaining)) as response: content=response.read(MAX_IMAGE-total+1)
        except Exception: raise ExecutionError('registry','FETCH_FAILED') from None
        total+=len(content)
        if total>MAX_IMAGE: raise ExecutionError('registry','IMAGE_BUDGET')
        if 'sha256:'+hashlib.sha256(content).hexdigest()!=reference: raise ExecutionError('registry','DIGEST_MISMATCH')
        return content
    raw=get('manifests',expected)
    if len(raw)>1024*1024: raise ExecutionError('registry','MANIFEST_BUDGET')
    manifest=json.loads(raw)
    if manifest.get('mediaType')!='application/vnd.oci.image.manifest.v1+json' or manifest.get('schemaVersion')!=2:
        raise ExecutionError('registry','SINGLE_OCI_MANIFEST_REQUIRED')
    if len(manifest.get('layers',[]))>128: raise ExecutionError('registry','LAYER_BUDGET')
    configuration=None
    for item in [manifest['config']]+manifest['layers']:
        content=get('blobs',digest(item['digest']))
        if len(content)!=item['size']: raise ExecutionError('registry','SIZE_MISMATCH')
        if item is manifest['config']:
            if len(content)>1024*1024: raise ExecutionError('registry','CONFIG_BUDGET')
            configuration=json.loads(content)
    return {'digest':expected,'platform':configuration['os']+'/'+configuration['architecture'],
            'verification':'REGISTRY_CONTENT_VERIFIED','requests':2+len(manifest['layers'])}

def build(source_directory, output, policy, tenant, cancel=lambda:False):
    worker=policy.value.get('build_worker',{})
    if worker.get('tenant')!=tenant or worker.get('isolation')!='DEDICATED_OFF_HOST_QUOTA_ENFORCED':
        raise Denied('Dedicated quota-enforced worker required')
    for resource,maximum in (('cpu_millicores',1000),('memory_mib',1024),('pids',128),('disk_mib',2048)):
        if type(worker.get(resource)) is not int or not 1<=worker[resource]<=maximum:raise Denied('Worker resource contract unavailable')
    if not worker.get('quota_evidence') or worker.get('lease_seconds',0)<1 or worker.get('lease_seconds',0)>120 or worker.get('max_seconds',0)>120 or worker.get('max_seconds',0)<1:
        raise Denied('Worker quota certification required')
    address=worker.get('address','')
    if not re.fullmatch(r'tcp://[a-zA-Z0-9.-]+:[0-9]{2,5}',address) or any(x in address for x in ('localhost','127.0.0.1')):
        raise Denied('Explicit remote TLS BuildKit required')
    for field in ('ca','cert','key'):
        path=Path(worker[field])
        if path.is_symlink() or not path.is_file() or path.stat().st_size>65536: raise Denied('Bounded worker TLS files required')
        if field=='key' and path.stat().st_mode&0o077: raise Denied('Private worker key required')
    source_directory=Path(source_directory)
    if not (source_directory/'Dockerfile').is_file(): raise ExecutionError('build','DOCKERFILE_REQUIRED')
    # No arbitrary build args, secrets, SSH sockets, host entitlements or push.
    argv=['buildctl','--addr',address,'--tlscacert',worker['ca'],'--tlscert',worker['cert'],'--tlskey',worker['key'],
          'build','--frontend','dockerfile.v0','--local','context='+str(source_directory),
          '--local','dockerfile='+str(source_directory),'--opt','platform=linux/amd64',
          '--opt','build-arg:SOURCE_DATE_EPOCH=0','--output','type=oci,dest='+str(output)]
    identity_evidence=verify_worker(worker)
    argv[argv.index('--addr')+1]=identity_evidence['pinned_address']
    argv[1:1]=['--tlsservername',identity_evidence['server_name']]
    command(argv,timeout=worker['max_seconds'],limit=1024*1024,cancel=cancel,disk_root=Path(output).parent)
    if cancel(): raise ExecutionError('build','CANCELLED')
    if not Path(output).is_file() or Path(output).stat().st_size>MAX_IMAGE: raise ExecutionError('build','IMAGE_BUDGET')
    result=oci_layout(Path(output).read_bytes())
    result.update(worker_identity=identity_evidence,builder='REMOTE_BUILDKIT_TLS',quota_evidence=worker['quota_evidence'],
                  reproducibility='PINNED_SOURCE_METADATA_ONLY; BASE_IMAGES_AND_NETWORK_REQUIRE_REVIEW')
    return result

def verify_webhook(raw, signature, secret):
    if len(raw)>65536 or not isinstance(signature,str) or not hmac.compare_digest('sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest(),signature):
        raise Denied('Webhook authentication failed')
    event=json.loads(raw)
    if event.get('deleted') or not SHA.fullmatch(event.get('after','')): raise Denied('Pinned push required')
    return event


def helm_source(source, destination, policy):
    """Pinned HTTPS chart download; safe unpack never runs chart hooks."""
    url=source['url'];parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or url not in policy.value.get('helm_charts',[]):
        raise Denied('Chart URL not approved')
    opener=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
    try:
        with opener.open(url,timeout=10) as response: content=response.read(MAX_SOURCE+1)
    except Exception: raise ExecutionError('source','HELM_FETCH_FAILED') from None
    if len(content)>MAX_SOURCE or 'sha256:'+hashlib.sha256(content).hexdigest()!=digest(source['digest']):
        raise ExecutionError('source','HELM_DIGEST_MISMATCH')
    result=unpack(content,destination)
    result['authority']='HELM_CONTENT_VERIFIED'
    return result


def retain_image(data, directory, expected):
    """Atomic immutable artifact publication; verify existing bytes after a crash."""
    oci_layout(data,expected)
    directory=Path(directory);directory.mkdir(mode=0o700,exist_ok=True)
    destination=directory/(digest(expected).split(':')[1]+'.tar')
    if destination.exists() or destination.is_symlink():
        fd=os.open(destination,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise Denied('Invalid retained artifact')
            content=stream.read(MAX_IMAGE+1)
        oci_layout(content,expected)
        return destination
    entries=list(directory.iterdir())
    if len(entries)>=32 or any(p.is_symlink() or not p.is_file() for p in entries) or sum(p.stat().st_size for p in entries)+len(data)>512*1024*1024:
        raise ExecutionError('image','ARTIFACT_RETENTION_BUDGET')
    name=None
    try:
        with tempfile.NamedTemporaryFile(dir=directory,prefix='.image-',delete=False) as stream:
            name=stream.name;stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.link(name,destination)  # Never overwrite an existing immutable artifact.
        fd=os.open(directory,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if name:Path(name).unlink(missing_ok=True)
    return destination


def verify_worker(worker):
    """Verify reviewed worker mTLS certificate before sending a build context.
    Buildctl then connects to the pinned IP with the same expected TLS server name.
    The CA must be dedicated to the approved worker, not a broad corporate CA.
    """
    import ipaddress
    import socket
    import ssl
    parsed=urlsplit(worker['address']);host=parsed.hostname;port=parsed.port
    fingerprint=worker.get('certificate_sha256','')
    if not re.fullmatch(r'[0-9a-f]{64}',fingerprint) or worker.get('dedicated_ca') is not True:
        raise Denied('Reviewed worker certificate and dedicated CA required')
    if not worker.get('allowed_ips') or not worker.get('denied_cidrs'):
        raise Denied('Worker IP/production-network boundaries required')
    addresses=worker['allowed_ips']
    if not isinstance(addresses,list) or len(addresses)!=1:raise Denied('One pinned worker IP required')
    address=addresses[0];ip=ipaddress.ip_address(address)
    denied=[ipaddress.ip_network(value) for value in worker['denied_cidrs']]
    if ip.version!=4 or ip.is_loopback or ip.is_unspecified or ip.is_link_local or address not in worker['allowed_ips'] or any(ip in network for network in denied):
        raise Denied('Worker address outside approved isolation boundary')
    try:
        context=ssl.create_default_context(cafile=worker['ca'])
        context.load_cert_chain(worker['cert'],worker['key'],password=lambda:'')
        with socket.create_connection((address,port),timeout=3) as connection:
            with context.wrap_socket(connection,server_hostname=host) as secured:
                if hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest()!=fingerprint:
                    raise Denied('Worker certificate identity differs')
    except Denied:raise
    except Exception:raise ExecutionError('build','WORKER_TLS_OR_AVAILABILITY_FAILURE') from None
    return {'certificate_sha256':fingerprint,'server_name':host,'pinned_address':'tcp://'+address+':'+str(port),
            'quota_evidence':worker['quota_evidence'],'isolation_verification':'OPERATOR_ATTESTED; REMOTE_LIMITS_REQUIRE_LIVE_EVIDENCE'}


def checked_oci_tar(data):
    """Bound TAR metadata before library allocation; support normal OCI PAX times.
    Path/size overrides, sparse records and GNU/compressed extensions fail closed.
    """
    offset=0;count=0;metadata_total=0
    while offset+512<=len(data):
        block=data[offset:offset+512]
        if block==b'\0'*512:
            if any(data[offset:]):raise ExecutionError('image','TRAILING_ARCHIVE_DATA')
            return
        count+=1;kind=block[156:157]
        if count>1024 or kind not in (b'0',b'\0',b'5',b'x'):
            raise ExecutionError('image','UNSUPPORTED_TAR_EXTENSION')
        try:size=int(block[124:136].rstrip(b'\0 ').lstrip(b' ') or b'0',8)
        except ValueError:raise ExecutionError('image','INVALID_ARCHIVE_SIZE') from None
        if not 0<=size<=MAX_IMAGE or (kind==b'5' and size):raise ExecutionError('image','IMAGE_BUDGET')
        end=offset+512+size
        if end>len(data):raise ExecutionError('image','TRUNCATED_ARCHIVE')
        if kind==b'x':
            metadata_total+=size
            if size>16384 or metadata_total>65536:raise ExecutionError('image','TAR_METADATA_BUDGET')
            payload=data[offset+512:end];position=0
            while position<len(payload):
                separator=payload.find(b' ',position)
                field=payload[position:separator] if separator>=position else b''
                if not re.fullmatch(rb'[0-9]{1,5}',field):raise ExecutionError('image','INVALID_PAX_METADATA')
                length=int(field);record_end=position+length
                if record_end<=separator+1 or record_end>len(payload):raise ExecutionError('image','INVALID_PAX_METADATA')
                record=payload[separator+1:record_end]
                if not re.fullmatch(rb'(?:mtime|atime|ctime)=-?[0-9]{1,20}(?:\.[0-9]{1,9})?\n',record):
                    raise ExecutionError('image','UNSUPPORTED_TAR_EXTENSION')
                position=record_end
        offset+=512+((size+511)//512)*512
        if offset>len(data):raise ExecutionError('image','TRUNCATED_ARCHIVE')
    raise ExecutionError('image','MISSING_ARCHIVE_END')
