"""Approved ready-RAN image identities. No discovery or imports at module load."""
import argparse
import json
import re
from pathlib import Path

LOCK_PATH = 'config/reference-versions.json'
ROOT = Path(__file__).resolve().parents[1]


def policy(root=ROOT):
    data = json.loads((Path(root) / LOCK_PATH).read_text())['ran_images']
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported ready-RAN image policy')
    for name, image in data['components'].items():
        tag, digest = image['tag'], image.get('digest')
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', tag) or tag.lower() in ('master', 'latest'):
            raise ValueError('Floating/invalid ready image tag: ' + name)
        if not re.fullmatch(r'[a-z0-9][a-z0-9./_-]*', image['repository']):
            raise ValueError('Invalid ready image repository: ' + name)
        if digest is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('Invalid ready image digest: ' + name)
        if not digest and (image.get('digest_status') != 'deferred-reference-cache-verification'
                           or name not in ('oai-cu', 'oai-du') or not re.fullmatch(r'[0-9]{4}\.w[0-9]{2}', tag)):
            raise ValueError('Ready image requires digest or explicit reference-verification deferral: ' + name)
        if image['pull_policy'] not in ('IfNotPresent', 'Never'):
            raise ValueError('Invalid ready image pull policy: ' + name)
        mode = image.get('runtime_reference', 'digest')
        acquisition = image.get('acquisition')
        if mode not in ('digest', 'local-tag') or (mode == 'local-tag' and
                (acquisition not in ('verified-import', 'pinned-source-build') or
                 image['pull_policy'] != 'Never' or (acquisition == 'verified-import' and not digest))):
            raise ValueError('Local tag requires verified import or pinned source build and Never pull policy')
        acquisition = image.get('acquisition')
        if acquisition == 'verified-import' and (not digest or image['pull_policy'] != 'Never'):
            raise ValueError('Imported ready image must have a digest and Never pull policy')
        if acquisition == 'pinned-source-build':
            source = image.get('source') or {}
            if (image['pull_policy'] != 'Never' or mode != 'local-tag' or
                    not source.get('repository') or
                    not re.fullmatch(r'[0-9a-f]{40}', source.get('commit', '')) or
                    not source.get('version')):
                raise ValueError('Pinned source build requires local-tag/Never and an exact source repository, commit and version')
    return data


def reference(image):
    if image.get('runtime_reference') == 'local-tag':
        return image['repository'] + ':' + image['tag']
    return image['repository'] + (('@' + image['digest']) if image.get('digest') else (':' + image['tag']))


def chart_values(root, chart):
    bindings = chart.get('image_bindings', {})
    if not bindings:
        return {}
    images = policy(root)['components']
    values = {}
    for field, component in bindings.items():
        image = images[component]
        parent = values
        parts = field.split('.')
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        parent[parts[-1]] = (reference(image) if field == 'edgeApp.image' else
                            dict(repository=image['repository'], tag=image['tag'],
                                 reference=reference(image), pullPolicy=image['pull_policy']))
    return values


def verify_rendered(root, chart, documents):
    if not chart.get('image_bindings'):
        return
    images = policy(root)['components']
    expected = {reference(images[c]): images[c]['pull_policy'] for c in chart['image_bindings'].values()}
    found = set()
    for doc in documents:
        spec = doc.get('spec', {})
        pod = spec.get('template', {}).get('spec', {})
        if doc.get('kind') == 'Pod':
            pod = spec
        if doc.get('kind') == 'CronJob':
            pod = spec.get('jobTemplate', {}).get('spec', {}).get('template', {}).get('spec', {})
        for container in pod.get('containers', []) + pod.get('initContainers', []):
            ref = container.get('image')
            if ref not in expected:
                raise ValueError('Unapproved ready runtime image: ' + str(ref))
            if container.get('imagePullPolicy', 'IfNotPresent') != expected[ref]:
                raise ValueError('Ready image pull policy differs: ' + str(ref))
            found.add(ref)
    if found != set(expected):
        raise ValueError('Missing approved ready runtime image')


def verify_imported_nodes(nodes, image):
    """Fail before teardown if a schedulable workload node lacks imported bytes."""
    eligible = [n for n in nodes.get('items', []) if not n.get('spec', {}).get('unschedulable')]
    if not eligible:
        raise ValueError('No schedulable workload nodes for approved srsRAN image')
    for node in eligible:
        status = node.get('status', {})
        if status.get('nodeInfo', {}).get('architecture') != 'amd64':
            raise ValueError('Approved srsRAN image requires amd64 workload nodes')
        names = {name for entry in status.get('images', []) for name in entry.get('names', [])}
        if reference(image) not in names:
            raise ValueError('Approved srsRAN runtime reference not advertised on workload node; import on every node and wait for kubelet image inventory')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['values', 'list'])
    parser.add_argument('scenario', nargs='?')
    parser.add_argument('kdu', nargs='?')
    args = parser.parse_args()
    if args.command == 'list':
        print(json.dumps({k: reference(v) for k, v in policy()['components'].items()}, indent=2))
    else:
        from scenario_registry import load_registry, select_scenarios
        scenario = select_scenarios(load_registry(), [args.scenario])[0]
        charts = [c for c in scenario['charts'] if c['kdu'] == args.kdu]
        if len(charts) != 1:
            parser.error('Select a ready scenario and its exact KDU')
        print(json.dumps(chart_values(ROOT, charts[0]), indent=2))


if __name__ == '__main__':
    main()
