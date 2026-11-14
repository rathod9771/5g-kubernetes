"""Image identity evidence for release manifests. Never fabricates a digest.

Builds are not performed: srsRAN is a local source build imported into
containerd (no approved registry), and OAI/Open5GS/helper images come from
upstream registries. Registry verification, when explicitly requested (CI only),
fetches the manifest by digest anonymously, re-hashes it and checks the platform.
"""
import hashlib
import json
import re
import urllib.parse
import urllib.request

import yaml

from runtime_images import policy

PLATFORM = 'linux/amd64'
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
INDEX_TYPES = ('application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json')
MANIFEST_TYPES = ('application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json')
BUILD_SUPPORT = {
    'srsran': {'status': 'UNSUPPORTED',
               'reason': 'Pinned source build is imported into containerd as a local-only tag (pull policy Never); '
                         'no approved registry, signing identity or reproducible CI build exists yet'},
}


def _component_status(name, image):
    record = {'repository': image['repository'], 'tag': image['tag'], 'digest': image.get('digest'),
              'platform': image.get('platform'), 'acquisition': image.get('acquisition'),
              'runtime_reference': image.get('runtime_reference', 'digest'),
              'build': BUILD_SUPPORT.get(name, {'status': 'NOT_APPLICABLE', 'reason': 'upstream registry image'}),
              'registry_verification': 'NOT_PERFORMED'}
    if record['runtime_reference'] == 'local-tag':
        record.update(status='LOCAL_ONLY', portable=False)
    elif not record['digest']:
        record.update(status='MISSING_DIGEST', portable=False)
    elif record['platform'] != PLATFORM:
        record.update(status='UNSUPPORTED_PLATFORM', portable=False)
    else:
        record.update(status='PINNED_DIGEST', portable=True)
    return record


def chart_images(files):
    """Image references declared in vendored chart values (including subcharts)."""
    found = []

    def walk(value, where):
        if isinstance(value, dict):
            if isinstance(value.get('repository'), str) and 'tag' in value:
                registry = value.get('registry') or 'docker.io'
                found.append({'location': where, 'repository': registry + '/' + value['repository'],
                              'tag': str(value['tag']), 'digest': value.get('digest') or None})
            for key, item in sorted(value.items()):
                walk(item, where + '.' + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, where + '[' + str(index) + ']')

    for name in sorted(n for n in files if n == 'values.yaml' or re.fullmatch(r'charts/[^/]+/values\.yaml', n)):
        walk(yaml.safe_load(files[name]) or {}, name)
    return found


def assess(root, entries, read_chart=None):
    """Per-component evidence for the RAN scenarios and core components in `entries`."""
    approved = policy(root)['components']
    components, scenarios = {}, {}
    for entry in entries:
        bound = sorted({c for chart in entry['charts'] for c in chart.get('image_bindings', {}).values()})
        for name in bound:
            components[name] = _component_status(name, approved[name])
        unbound = []
        if read_chart is not None:
            for chart in entry['charts']:
                if not chart.get('image_bindings'):
                    for image in chart_images(read_chart(chart)):
                        valid = bool(image['digest'] and DIGEST.fullmatch(image['digest']))
                        unbound.append(dict(image, status='PINNED_DIGEST' if valid else 'MUTABLE_TAG', portable=valid,
                                            build={'status': 'NOT_APPLICABLE', 'reason': 'vendored upstream chart image'},
                                            registry_verification='NOT_PERFORMED'))
        scenarios[entry['key']] = {'components': bound, 'chart_images': unbound}
    return {'components': components, 'scenarios': scenarios}


def complete(evidence):
    records = list(evidence['components'].values()) + [i for s in evidence['scenarios'].values() for i in s['chart_images']]
    return bool(records) and all(r['status'] == 'PINNED_DIGEST' and r['registry_verification'] == 'VERIFIED'
                                 for r in records)


def _docker_hub(repository):
    parts = repository.split('/')
    if parts[0] in ('docker.io', 'index.docker.io', 'registry-1.docker.io'):
        parts = parts[1:]
    elif '.' in parts[0] or ':' in parts[0] or parts[0] == 'localhost':
        return None                     # other registries are not supported yet
    return '/'.join(['library'] + parts if len(parts) == 1 else parts)


def verify_registry(record, opener=None, timeout=20):
    """Anonymous Docker Hub manifest verification; returns VERIFIED or a failure status."""
    opener = opener or urllib.request.build_opener()
    repository = _docker_hub(record['repository'])
    digest = record.get('digest')
    if not repository or not digest or not DIGEST.fullmatch(digest):
        return 'UNSUPPORTED_REGISTRY' if not repository else 'NO_DIGEST'

    def fetch(url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        with opener.open(request, timeout=timeout) as response:
            return response.read(16 * 1024 * 1024), response.headers.get('Content-Type', '')
    try:
        token = json.loads(fetch('https://auth.docker.io/token?service=registry.docker.io&scope=' +
                                 urllib.parse.quote('repository:' + repository + ':pull'))[0])['token']
        auth = {'Authorization': 'Bearer ' + token}
        body, kind = fetch('https://registry-1.docker.io/v2/' + repository + '/manifests/' + digest,
                           dict(auth, Accept=', '.join(INDEX_TYPES + MANIFEST_TYPES)))
        if 'sha256:' + hashlib.sha256(body).hexdigest() != digest:
            return 'DIGEST_MISMATCH'
        manifest = json.loads(body)
        kind = manifest.get('mediaType') or kind.split(';')[0]
        if kind in INDEX_TYPES:
            os_name, arch = PLATFORM.split('/')
            ok = any(m.get('platform', {}).get('os') == os_name and m.get('platform', {}).get('architecture') == arch
                     for m in manifest.get('manifests', []))
            return 'VERIFIED' if ok else 'PLATFORM_MISSING'
        if kind in MANIFEST_TYPES:
            config = manifest['config']['digest']
            blob, _ = fetch('https://registry-1.docker.io/v2/' + repository + '/blobs/' + config, auth)
            if 'sha256:' + hashlib.sha256(blob).hexdigest() != config:
                return 'DIGEST_MISMATCH'
            data = json.loads(blob)
            return 'VERIFIED' if data.get('os') + '/' + data.get('architecture') == PLATFORM else 'PLATFORM_MISSING'
        return 'UNSUPPORTED_MANIFEST_TYPE'
    except Exception:
        return 'VERIFICATION_FAILED'
