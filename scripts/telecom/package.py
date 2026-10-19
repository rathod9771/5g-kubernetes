"""Bounded offline packaging, reusing repository Helm/OSM validators and archives."""
import importlib.util
import json
import os
import stat
import tempfile
import time
from pathlib import Path
import yaml
from osm_packages import safe_chart_inputs, descriptors, validate_references, archive_bytes, json_bytes
from . import chart_adapters, subcharts
from .contract import confined, digest, identity

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('_telecom_bounded_read', ROOT/'ran-selector/monitoring.py')
_bounded = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_bounded)

# Two capture profiles. 'default' is unchanged from before this milestone --
# every existing caller keeps today's exact behavior. 'large' exists for
# charts that are legitimately bigger than a synthetic single-NF fixture
# (e.g. a real vendored-dependency chart) but is NEVER selected
# automatically: a caller must explicitly pass chart_profile='large' to
# prepare(). TRUST NOTE (same honesty as chart_adapters.py/subcharts.py's
# documented limitations): passing 'large' is a bare caller-supplied flag
# today, not an independently verified operator authorization -- there is
# no live caller that can reach this parameter (see pipeline REPORT), and a
# future wiring should gate it the same way a trusted dependency inventory
# is gated (see subcharts.TRUSTED_DEPENDENCIES), not merely accept whatever
# a packaging-time caller asserts. Every number below is still a genuine,
# finite bound -- not "unlimited" -- so even a caller that can set this
# flag cannot exceed it.
CAPTURE_PROFILES = {
    'default': {'max_depth': 8, 'max_entries': 256, 'max_files': 128,
                'max_file_bytes': 1024 * 1024, 'max_total_bytes': 4 * 1024 * 1024},
    'large': {'max_depth': 8, 'max_entries': 768, 'max_files': 512,
              'max_file_bytes': 1024 * 1024, 'max_total_bytes': 8 * 1024 * 1024},
}
# Wall-clock bound on the whole capture() walk, independent of file-count/
# size budgets -- guards against pathological I/O (e.g. a slow filesystem,
# many tiny files) regardless of profile. The expensive part of prepare()
# (helm lint/template) already has its own timeout+RSS bound via
# read_command()/_bounded.run(); this is the equivalent bound for the
# pure-Python directory walk and hashing work, which that call doesn't cover.
CAPTURE_WALLTIME_BUDGET_SECONDS = 20


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


def prepare(workspace, chart_path, manifest, helm='helm', *, verified_image=None, reviewed_inputs=None,
            source_evidence=None, vendored_dependencies=None, chart_profile='default'):
    chart = confined(workspace, chart_path)
    if not chart.is_dir():
        raise ValueError('Local chart directory required for offline packaging')
    if reviewed_inputs is not None and manifest.get('schema_version') != 2:
        raise ValueError('Reviewed vendored chart inventory is only accepted for multi-image manifests')
    if vendored_dependencies is not None and manifest.get('schema_version') != 2:
        raise ValueError('Reviewed vendored dependency inventory is only accepted for multi-image manifests')
    if chart_profile not in CAPTURE_PROFILES:
        raise ValueError('Unknown chart capture profile')
    if chart_profile != 'default' and manifest.get('schema_version') != 2:
        raise ValueError('The large chart capture profile is only accepted for multi-image manifests')
    profile = CAPTURE_PROFILES[chart_profile]
    capture_started = time.monotonic()
    files = {}; total = 0; entries = 0
    def capture(fd, prefix='', depth=0):
        nonlocal total, entries
        if time.monotonic() - capture_started > CAPTURE_WALLTIME_BUDGET_SECONDS:
            raise ValueError('Chart capture time budget exceeded')
        if depth>profile['max_depth']: raise ValueError('Chart depth budget exceeded')
        with os.scandir(fd) as listing:
            for item in listing:
                entries+=1
                if entries>profile['max_entries']: raise ValueError('Chart traversal budget exceeded')
                name=prefix+item.name
                info=item.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child=os.open(item.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try: capture(child,name+'/',depth+1)
                    finally: os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    child=os.open(item.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                    with os.fdopen(child,'rb') as stream:
                        data=stream.read(profile['max_file_bytes']+1)
                    total+=len(data)
                    if len(data)>profile['max_file_bytes'] or total>profile['max_total_bytes'] or len(files)>=profile['max_files']:
                        raise ValueError('Chart input budget exceeded')
                    files[name]=data
                else: raise ValueError('Only regular chart files/directories are accepted')
    fd=os.open(chart,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: capture(fd)
    finally: os.close(fd)
    # Adaptation (if this exact chart name/version/source-commit has a
    # reviewed entry, AND source_evidence proves independently verified
    # acquisition -- see chart_adapters.adapt()) runs BEFORE
    # safe_chart_inputs/multi_image.verify, not instead of them: the adapted
    # file set still passes through the exact same, unweakened checks below
    # as any organically authored template. adapt() now also performs any
    # registered, source-verified file REMOVAL (e.g. a redundant packaged
    # dependency archive) using the exact same gating.
    files, adapted_files = chart_adapters.adapt(files, manifest, source_evidence)
    # Narrow, source-verified values.yaml / Chart.yaml contracts (see
    # chart_adapters.py) -- run BEFORE subcharts.validate() so a
    # dependency-declaration removal and the corresponding vendored-file
    # removal above are both already reflected when subcharts.py checks
    # Chart.yaml's dependencies: against what is actually vendored.
    files, values_overrides_applied = chart_adapters.apply_values_overrides(files, manifest, source_evidence)
    files, dependency_declarations_removed = chart_adapters.apply_dependency_removals(files, manifest, source_evidence)
    # Vendored Helm subchart entries (charts/<name>/...) are validated
    # separately (exact per-file digest pin + dependency-relationship
    # checks + the identical structural/content policy a top-level template
    # already gets -- see subcharts.py), ONLY for schema_version=2, and only
    # THEN excluded from the safe_chart_inputs() call below: that shared,
    # non-telecom-specific function's path allowlist has no concept of
    # charts/ and must stay exactly as strict as it is today for every
    # other caller -- a v1 chart's charts/ entries (if any) are NEVER
    # exempted and still hit safe_chart_inputs()'s existing rejection,
    # exactly as before this feature existed.
    dependency_registry_key = None
    if manifest.get('schema_version') == 2:
        # subcharts.validate() always runs here (not only when charts/
        # entries are already present): a Chart.yaml that DECLARES a
        # dependency with nothing vendored for it at all must still be
        # caught, not silently treated as having nothing to check.
        try:
            parent_meta = yaml.safe_load(files.get('Chart.yaml', b''))
        except Exception:
            parent_meta = None
        dependency_registry_key = subcharts.validate(
            files, parent_meta if isinstance(parent_meta, dict) else {}, vendored_dependencies,
            commit=(manifest.get('source') or {}).get('commit'), source_evidence=source_evidence)
        files_for_safe_chart_inputs = {n: v for n, v in files.items() if not n.startswith('charts/')}
    else:
        files_for_safe_chart_inputs = files
    safe_chart_inputs(files_for_safe_chart_inputs, reviewed=reviewed_inputs)
    for p in ('Chart.yaml', 'values.yaml'):
        if p not in files:
            raise ValueError('Chart metadata/values required')
    if verified_image is not None:
        if manifest.get('schema_version') != 1:
            raise ValueError('Dockerfile/build-and-publish binding is not supported for multi-image manifests')
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
    image_discovery = None
    if manifest.get('schema_version') == 1:
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
    else:
        from . import multi_image
        seen = set(); namespace_portable = True
        for doc in docs:
            if not isinstance(doc, dict) or 'kind' not in doc or 'metadata' not in doc:
                raise ValueError('Invalid/empty rendered resource')
            metadata = doc.get('metadata', {}) or {}
            # Hook resources (test/lifecycle) still execute or mutate cluster
            # state, so they are never exempted from the duplicate-name or
            # namespace check -- only multi_image.verify's role-completeness
            # requirement treats them differently.
            key = (doc['kind'], metadata.get('name'))
            if not key[1] or key in seen:
                raise ValueError('Duplicate or unnamed chart resource')
            seen.add(key)
            namespace_portable = namespace_portable and 'namespace' not in metadata and (
                doc['kind'] != 'Service' or doc.get('spec', {}).get('type', 'ClusterIP') == 'ClusterIP')
            if metadata.get('namespace', 'research-sandbox') != 'research-sandbox':
                raise ValueError('Chart namespace is not authorized')
        image_discovery = multi_image.verify(manifest, docs)
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
    # Unified adaptation provenance: every field below represents a VERIFIED
    # fact established above (a registry key only appears here if it was
    # actually matched AND passed its source_evidence/digest gates; a
    # None here is an honest "no operator-reviewed registry backs this,"
    # never a caller-supplied approval claim -- see chart_adapters.py and
    # subcharts.py's own "TRUST MODEL" documentation). Identity is
    # recomputed fresh from the final Chart.yaml -- safe, since no
    # mechanism above ever touches its name/version fields.
    try:
        final_meta = yaml.safe_load(files.get('Chart.yaml', b'')) or {}
    except Exception:
        final_meta = {}
    if not isinstance(final_meta, dict):
        final_meta = {}
    chart_identity_key = (final_meta.get('name'), final_meta.get('version'), (manifest.get('source') or {}).get('commit'))
    adaptation_provenance = {
        'source_commit': chart_identity_key[2],
        'chart_name': chart_identity_key[0],
        'chart_version': chart_identity_key[1],
        'adapter_registry_key': chart_identity_key if (
            chart_identity_key in chart_adapters.ADAPTERS or chart_identity_key in chart_adapters.REMOVALS) else None,
        'values_override_registry_key': chart_identity_key if chart_identity_key in chart_adapters.VALUES_OVERRIDES else None,
        'dependency_removal_registry_key': chart_identity_key if chart_identity_key in chart_adapters.DEPENDENCY_REMOVALS else None,
        'dependency_inventory_registry_key': dependency_registry_key,
        'modified_files': [p for p in adapted_files if p.get('action') == 'substitute'],
        'removed_files': [p for p in adapted_files if p.get('action') == 'remove'],
        'values_overrides_applied': values_overrides_applied,
        'dependency_declarations_removed': dependency_declarations_removed,
        'chart_digest': 'sha256:' + content_hash,
        'chart_profile': chart_profile,
        'validation_results': {
            'safe_chart_inputs': 'PASS',
            'subcharts_validate': 'PASS' if manifest.get('schema_version') == 2 else 'NOT_APPLICABLE',
            'multi_image_verify': 'PASS' if manifest.get('schema_version') == 2 else 'NOT_APPLICABLE',
        },
    }
    return {'namespace_portable':namespace_portable,'scenario':scenario, 'chart_digest':'sha256:'+content_hash,
            'image_discovery':image_discovery, 'adapted_files':adapted_files,
            'adaptation_provenance':adaptation_provenance,
            'package_digests':{k:'sha256:'+__import__('hashlib').sha256(v).hexdigest() for k,v in artifacts.items()},
            'helm_validation':{'outcome':'PASS','authority':'PLATFORM_OFFLINE_VERIFIED','deployed':False},
            'artifacts':{k:v.hex() for k,v in artifacts.items()}}
