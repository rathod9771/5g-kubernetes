#!/usr/bin/env python3
"""Build an immutable release candidate: impact analysis, deterministic OSM
packages, image evidence and release-manifest.json. Never contacts OSM or
Kubernetes; never deploys. Publication eligibility is computed, deployment is not.
"""
import argparse
import datetime
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from osm_packages import PackageError, build, read_tree
from release_images import assess, complete, verify_registry
from release_impact import ImpactError, analyze, git, git_changes, git_reader
from release_manifest import (MANIFEST_NAME, SCHEMA, SCHEMA_VERSION, WORKFLOW_PATH, ManifestError, channel,
                              eligibility, render, sha256)
from scenario_registry import RegistryError, load_registry, repository_path, select_scenarios

DEFAULT_RELEASE_BRANCHES = ('reproducible-installer',)


class ReleaseError(ValueError):
    pass


def identity(root, environ):
    """Workflow identity from the GitHub runtime; anything else is a local, ineligible build."""
    if environ.get('GITHUB_ACTIONS') != 'true':
        branch = git(root, 'rev-parse', '--abbrev-ref', 'HEAD').decode().strip()
        return {'repository': 'local', 'event': 'local', 'ref': 'refs/heads/' + branch,
                'workflow': {'path': None, 'ref': None, 'run_id': None, 'run_attempt': None,
                             'runner_environment': 'local', 'server_url': None}}
    runner = environ.get('RUNNER_ENVIRONMENT')
    if runner != 'github-hosted':
        raise ReleaseError('Release candidates are built only on GitHub-hosted runners')
    repository, workflow_ref = environ['GITHUB_REPOSITORY'], environ['GITHUB_WORKFLOW_REF']
    path = workflow_ref.split('@', 1)[0][len(repository) + 1:]
    if not workflow_ref.startswith(repository + '/') or path != WORKFLOW_PATH:
        raise ReleaseError('Unexpected workflow identity')
    return {'repository': repository, 'event': environ['GITHUB_EVENT_NAME'], 'ref': environ['GITHUB_REF'],
            'workflow': {'path': path, 'ref': workflow_ref, 'run_id': int(environ['GITHUB_RUN_ID']),
                         'run_attempt': int(environ['GITHUB_RUN_ATTEMPT']), 'runner_environment': runner,
                         'server_url': environ.get('GITHUB_SERVER_URL')}}


def build_release(root, base, head, output, environ, release_branches=DEFAULT_RELEASE_BRANCHES,
                  environment='hp-lab', ci_validation='not-run', verify_images=False, clock=time.time, opener=None):
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ReleaseError('Release output must be outside the repository')
    if git(root, 'rev-parse', 'HEAD').decode().strip() != head:
        raise ReleaseError('Checked-out commit differs from the requested release commit')
    if environ.get('GITHUB_ACTIONS') == 'true' and environ.get('GITHUB_SHA') != head:
        raise ReleaseError('Release commit differs from the workflow commit')
    if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ReleaseError('Working tree is not clean; artifacts must come from the exact commit')
    source = identity(root, environ)
    registry = load_registry(root)
    paths = git_changes(root, base, head)
    impact = analyze(paths, registry, git_reader(root, base) if paths is not None else None, git_reader(root, head))
    entries = select_scenarios(registry, impact['buildable']) if impact['buildable'] else []
    try:
        output.mkdir()                 # atomic claim: concurrent builds never share an output
    except FileExistsError:
        raise ReleaseError('Release output already exists; each build needs a new directory') from None
    try:
        return _populate(root, head, base, paths, output, source, registry, impact, entries, release_branches,
                         environment, ci_validation, verify_images, clock, opener)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)    # never leave a partial release behind
        raise


def _populate(root, head, base, paths, output, source, registry, impact, entries, release_branches,
              environment, ci_validation, verify_images, clock, opener):
    workspace = output / '.osm-build'
    packages, artifacts = [], {}
    if entries:
        published = build(root, workspace, entries)
        (output / 'packages').mkdir()
        (output / 'provenance').mkdir()
        core = {e['key'] for e in registry.get('installer_components', [])}
        for entry in entries:
            record = {'scenario': entry['key'], 'kind': 'core' if entry['key'] in core else 'ran',
                      'descriptor_version': entry['descriptor_version'], 'kdus': [c['kdu'] for c in entry['charts']],
                      'image_components': sorted({v for c in entry['charts'] for v in c.get('image_bindings', {}).values()})}
            provenance = json.loads((published / (entry['key'] + '.provenance.json')).read_bytes())
            for role, field in (('knf', 'knf_package'), ('nsd', 'nsd_package')):
                data = (published / (entry[field] + '.tar.gz')).read_bytes()
                if provenance['archives_sha256'][entry[field] + '.tar.gz'] != sha256(data):
                    raise ReleaseError('Generated archive differs from its provenance: ' + entry[field])
                name = 'packages/' + entry[field] + '.tar.gz'
                (output / name).write_bytes(data)
                record[role] = {'descriptor_id': entry[field], 'artifact': name, 'sha256': sha256(data), 'size': len(data)}
                artifacts[name] = sha256(data)
            data = (published / (entry['key'] + '.provenance.json')).read_bytes()
            name = 'provenance/' + entry['key'] + '.provenance.json'
            (output / name).write_bytes(data)
            record['provenance'] = {'artifact': name, 'sha256': sha256(data)}
            artifacts[name] = sha256(data)
            packages.append(record)
        shutil.rmtree(workspace)
    images = assess(root, entries, lambda chart: read_tree(repository_path(root, chart['source'])))
    if verify_images:
        for record in list(images['components'].values()) + [i for s in images['scenarios'].values() for i in s['chart_images']]:
            if record['status'] == 'PINNED_DIGEST':
                record['registry_verification'] = verify_registry(record, opener=opener)
    branch = source['ref'][len('refs/heads/'):] if source['ref'].startswith('refs/heads/') else None
    manifest = {
        'schema': SCHEMA, 'schema_version': SCHEMA_VERSION, 'repository': source['repository'],
        'source': {'commit': head, 'ref': source['ref'], 'branch': branch, 'event': source['event'],
                   'base_commit': base if paths is not None else None, 'tree_clean': True},
        'workflow': source['workflow'],
        'built_at': datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'channel': channel(source['event'], source['ref'], release_branches),
        'change_impact': impact,
        'ci_validation': {'status': ci_validation,
                          'evidence': 'the attested workflow runs the full offline regression suite before this step'},
        'images': dict(images, build_support='no image is built or pushed by this pipeline'),
        'packages': packages, 'artifacts': artifacts,
        'target': {'architecture': 'linux/amd64', 'environment': environment},
        'osm_revision_binding': {
            'descriptor_ids': 'registry knf_package/nsd_package, unchanged across releases',
            'revision': 'assigned by OSM v19 when content is published under the same descriptor id; the deployment '
                        'agent records (descriptor id, revision, package sha256) and the running KDU revision'},
        'toolchain': {'python': platform.python_version()},
    }
    manifest['eligibility'] = eligibility(manifest, complete(images))
    (output / MANIFEST_NAME).write_bytes(render(manifest))
    for name, digest in artifacts.items():
        if sha256((output / name).read_bytes()) != digest:
            raise ReleaseError('Artifact changed after writing: ' + name)
    files = sorted(str(p.relative_to(output)) for p in output.rglob('*') if p.is_file())
    (output / 'SHA256SUMS').write_text(''.join(sha256((output / f).read_bytes()) + '  ' + f + '\n' for f in files))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='', help='Comparable base commit; empty forces a full rebuild')
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-branch', action='append', dest='release_branches')
    parser.add_argument('--environment', default='hp-lab')
    parser.add_argument('--ci-validation', choices=('passed', 'not-run'), default='not-run')
    parser.add_argument('--verify-registry', action='store_true', help='CI only: verify pinned digests anonymously')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        manifest = build_release(root, args.base, args.head, args.output, dict(os.environ),
                                 tuple(args.release_branches or DEFAULT_RELEASE_BRANCHES), args.environment,
                                 args.ci_validation, args.verify_registry)
    except (ReleaseError, ImpactError, ManifestError, PackageError, RegistryError, OSError, ValueError, KeyError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    summary = {'channel': manifest['channel'], 'impact': manifest['change_impact']['status'],
               'ran_scenarios': manifest['change_impact']['ran_scenarios'],
               'core_components': manifest['change_impact']['core_components'],
               'blocked': manifest['change_impact']['blocked'], 'packages': len(manifest['packages']),
               'eligibility': manifest['eligibility']}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
