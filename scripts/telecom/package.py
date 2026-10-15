"""Bounded offline packaging, reusing repository Helm/OSM validators and archives."""
import importlib.util
import json
import os
import stat
import tempfile
from pathlib import Path
import yaml
from osm_packages import safe_chart_inputs, descriptors, validate_references, archive_bytes, json_bytes
from .contract import confined, digest, identity

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('_telecom_bounded_read', ROOT/'ran-selector/monitoring.py')
_bounded = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_bounded)


def read_command(argv):
    with tempfile.TemporaryDirectory(prefix='telecom-command-') as home:
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':home,'LANG':'C.UTF-8',
             'KUBECONFIG':os.devnull,'GOMAXPROCS':'1','GIT_CONFIG_NOSYSTEM':'1',
             'GIT_CONFIG_GLOBAL':os.devnull,'HELM_CONFIG_HOME':home,'HELM_CACHE_HOME':home,'HELM_DATA_HOME':home}
        result = _bounded.run(argv, timeout=8, env=env, max_rss=256*1024*1024)
    if result.returncode:
        raise ValueError('Bounded offline validation command failed; private output suppressed')
    return result.stdout


def source_evidence(source, workspace):
    if source['kind'] == 'local-git':
        path = confined(workspace, source['path'])
        args = ['git', '-c', 'core.fsmonitor=false', '-C', str(path)]
        commit = read_command(args + ['rev-parse', '--verify', source['commit']+'^{commit}']).strip()
        tree = read_command(args + ['rev-parse', '--verify', source['commit']+'^{tree}']).strip()
        if commit != source['commit']:
            raise ValueError('Source commit differs')
        return {'method':'LOCAL_GIT_OBJECT_IDENTITY', 'commit':commit, 'tree':tree,
                'working_tree_included':False, 'build':'NOT_RUN', 'authority':'PLATFORM_OFFLINE_VERIFIED'}
    return {'method':'DECLARATION_ONLY', 'authority':'PROVIDER_DECLARED',
            'fetch':'NOT_RUN', 'registry_verification':'NOT_RUN', 'build':'NOT_RUN'}


def prepare(workspace, chart_path, manifest, helm='helm', *, verified_image=None):
    chart = confined(workspace, chart_path)
    if not chart.is_dir():
        raise ValueError('Local chart directory required for offline packaging')
    files = {}; total = 0; entries = 0
    def capture(fd, prefix='', depth=0):
        nonlocal total, entries
        if depth>8: raise ValueError('Chart depth budget exceeded')
        with os.scandir(fd) as listing:
            for item in listing:
                entries+=1
                if entries>256: raise ValueError('Chart traversal budget exceeded')
                name=prefix+item.name
                info=item.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child=os.open(item.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try: capture(child,name+'/',depth+1)
                    finally: os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    child=os.open(item.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                    with os.fdopen(child,'rb') as stream:
                        data=stream.read(1024*1024+1)
                    total+=len(data)
                    if len(data)>1024*1024 or total>4*1024*1024 or len(files)>=128:
                        raise ValueError('Chart input budget exceeded')
                    files[name]=data
                else: raise ValueError('Only regular chart files/directories are accepted')
    fd=os.open(chart,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: capture(fd)
    finally: os.close(fd)
    safe_chart_inputs(files)
    for p in ('Chart.yaml', 'values.yaml'):
        if p not in files:
            raise ValueError('Chart metadata/values required')
    if verified_image is not None:
        expected=manifest['image']['repository']+'@'+digest(manifest['image']['digest'])
        if verified_image!=expected: raise ValueError('Verified image binding differs')
        values=yaml.safe_load(files['values.yaml'])
        if not isinstance(values,dict) or not isinstance(values.get('image'),str):
            raise ValueError('Build charts require a scalar values.image contract')
        values['image']=verified_image
        files['values.yaml']=yaml.safe_dump(values,sort_keys=True).encode()
    # Render and archive identical captured bytes, including an explicitly bound image.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='telecom-chart-') as temp:
        for name, data in files.items():
            target = Path(temp)/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        read_command([helm, 'lint', temp, '--strict'])
        rendered = read_command([helm, 'template', 'research-fixture', temp, '--namespace', 'research-sandbox'])
    docs = list(yaml.safe_load_all(rendered))
    images = []; seen=set(); namespace_portable=True
    for doc in docs:
        if not isinstance(doc, dict) or doc.get('kind') not in ('Deployment', 'Service', 'ConfigMap'):
            raise ValueError('MVP accepts only namespaced Deployment/Service/ConfigMap')
        metadata = doc.get('metadata', {})
        namespace_portable=namespace_portable and 'namespace' not in metadata and (doc['kind']!='Service' or doc.get('spec',{}).get('type','ClusterIP')=='ClusterIP')
        key=(doc['kind'],metadata.get('name'))
        if not key[1] or key in seen: raise ValueError('Duplicate or unnamed chart resource')
        seen.add(key)
        if metadata.get('namespace', 'research-sandbox') != 'research-sandbox' or metadata.get('annotations', {}).get('helm.sh/hook'):
            raise ValueError('Chart namespace/hook is not authorized')
        if doc['kind'] == 'Deployment':
            if doc['spec'].get('replicas',1)!=1: raise ValueError('MVP fixture must be singleton')
            spec = doc['spec']['template']['spec']
            if spec.get('automountServiceAccountToken') is not False: raise ValueError('Disable service account token mounting')
            if any(spec.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC', 'serviceAccountName', 'initContainers')):
                raise ValueError('Privileged pod features require separate qualification')
            if any('hostPath' in v or 'secret' in v for v in spec.get('volumes', [])):
                raise ValueError('Host/secret mounts forbidden in MVP')
            for c in spec['containers']:
                images.append(c['image'])
                security = c.get('securityContext', {})
                if any(p.get('hostPort') for p in c.get('ports',[])) or security.get('runAsNonRoot') is not True or security.get('capabilities',{}).get('drop')!=['ALL'] or security.get('privileged') or security.get('allowPrivilegeEscalation') is not False or security.get('capabilities', {}).get('add'):
                    raise ValueError('Explicit nonprivileged container required')
                if c.get('envFrom') or any('valueFrom' in v for v in c.get('env', [])):
                    raise ValueError('Secret/environment references need separate review')
                limits = c.get('resources', {}).get('limits', {})
                if not limits.get('cpu') or not limits.get('memory'):
                    raise ValueError('Container resource limits required')
    expected = manifest['image']['repository']+'@'+digest(manifest['image']['digest'])
    if not images or any(i != expected for i in images):
        raise ValueError('Rendered image must match the immutable capability image')
    content_hash = identity({n:__import__('hashlib').sha256(v).hexdigest() for n,v in files.items()})
    # Descriptor identity includes the provider capability revision, not just chart bytes.
    name = 'research-'+identity({'chart':content_hash,'capability':manifest})[:20]
    scenario = {'key':name, 'knf_package':name+'-knf', 'nsd_package':name+'-ns',
                'member_id':'nf', 'descriptor_version':'1.0', 'display_name':manifest['name'],
                'charts':[{'kdu':'nf'}]}
    vnfd, nsd = descriptors(scenario)
    inputs = {f"{scenario['knf_package']}/{scenario['knf_package']}_vnfd.yaml":json_bytes(vnfd),
              f"{scenario['nsd_package']}/{scenario['nsd_package']}_nsd.yaml":json_bytes(nsd)}
    inputs.update({f"{scenario['knf_package']}/helm-chart-v3s/nf/{n}":v for n,v in files.items()})
    validate_references(scenario, inputs)
    artifacts = {scenario[k]+'.tar.gz':archive_bytes(scenario[k], {n:v for n,v in inputs.items() if n.startswith(scenario[k]+'/')}) for k in ('knf_package','nsd_package')}
    return {'namespace_portable':namespace_portable,'scenario':scenario, 'chart_digest':'sha256:'+content_hash,
            'package_digests':{k:'sha256:'+__import__('hashlib').sha256(v).hexdigest() for k,v in artifacts.items()},
            'helm_validation':{'outcome':'PASS','authority':'PLATFORM_OFFLINE_VERIFIED','deployed':False},
            'artifacts':{k:v.hex() for k,v in artifacts.items()}}
