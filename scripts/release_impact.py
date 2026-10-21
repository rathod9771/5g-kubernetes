"""Deterministic change-impact analysis for original RAN/Core OSM packages.

Every changed path is resolved through the authoritative scenario registry
(chart sources, value profiles, image bindings, reviewed legacy packages), never
by directory-name matching. A path that cannot be classified forces a full
rebuild; affected scenarios are never silently omitted. The analysis never
decides which RAN is active: that happens on the target under the lifecycle lock.
"""
import json
import re
import subprocess

from runtime_images import LOCK_PATH

REGISTRY_PATH = 'config/scenarios.json'
# osm_packages.expected_artifacts() hashes these into every package's provenance.
GENERATOR = ('scripts/osm_packages.py', 'scripts/scenario_registry.py', 'scripts/local_safety.py',
             'scripts/runtime_images.py')
# Container build definitions present in this repository, by image component.
IMAGE_BUILD = {'scripts/build_srsran_from_source.sh': 'srsran', 'scripts/srsran_image.py': 'srsran'}
# Separate researcher CI/CD framework: never an original RAN/Core change.
RESEARCHER = ('scripts/telecom/', 'tests/test_telecom_', 'config/telecom/', 'docs/telecom/',
              '.github/workflows/telecom')
# Original-orchestrator files that are not OSM package inputs (dashboard, installer,
# lifecycle, monitoring, docs, tests, workflows). Anything else is unknown.
NON_PACKAGE_PREFIXES = ('docs/', 'tests/', 'ran-selector/', 'scripts/assurance/', 'scripts/installer/',
                        'scripts/experiments/', 'monitoring/', 'layer3-autonomous/', 'deploy/', 'usrp-gnb/',
                        '.github/')
NON_PACKAGE_FILES = frozenset((
    'README.md', 'INSTALLATION.md', '.gitignore', 'install.sh', 'uninstall.sh', 'deploy.sh', 'orchestrator.sh',
    'status.sh', 'bench-all-ran.sh', 'config/global.env.example', 'config/assurance-event.schema.json',
    'config/assurance-policies.json', 'osm-packages/README.md',
    'scripts/lifecycle.py', 'scripts/lifecycle_osm.py', 'scripts/lifecycle_health.py',
    'scripts/release_impact.py', 'scripts/release_images.py', 'scripts/release_manifest.py',
    'scripts/build_release.py', 'scripts/release_verify.py', 'scripts/release_attestation.py',
    'scripts/release_fetch.py', 'config/release-verification.json', 'scripts/osm_catalog.py', 'scripts/osm_onboard.py', 'scripts/osm-onboard.sh',
    'scripts/osm_tls.py', 'scripts/runtime_config.py', 'scripts/runtime_render.py', 'scripts/runtime_state.py',
    'scripts/service_entry.py', 'scripts/state_schema.py', 'scripts/ran_health.py', 'scripts/validate_oran_oai.py',
    'scripts/validate.sh', 'scripts/recover_experimental_ran.py', 'scripts/prepare_simulated_ue.py',
    'scripts/image_metadata.py', 'scripts/preflight.sh', 'scripts/common.sh', 'scripts/instantiate-with-retry.sh',
    'scripts/enable-oran-oai-e2.sh', 'scripts/deploy-oran-oai-rfsim.sh', 'scripts/build-cudu-batch.sh',
    'scripts/build-private-installer.sh', 'scripts/build-ran-batch.sh', 'scripts/build-ran-nsds.sh',
    'scripts/rename-ran-packages.sh',
))
SHA = re.compile(r'[0-9a-f]{40}')


class ImpactError(ValueError):
    pass


def _entries(registry):
    return {e['key']: e for e in registry['scenarios'] + registry.get('installer_components', [])}


def _under(path, directory):
    return path.startswith(directory.rstrip('/') + '/')


def _json(text, label):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def registry_changes(base_text, head_text):
    """Changed registry entry keys, or None when only a full rebuild is safe."""
    base, head = _json(base_text, 'base'), _json(head_text, 'head')
    if base is None or head is None:
        return None
    sections = ('scenarios', 'installer_components')
    if {k: v for k, v in base.items() if k not in sections} != {k: v for k, v in head.items() if k not in sections}:
        return None
    try:
        old = {e['key']: e for s in sections for e in base.get(s, [])}
        new = {e['key']: e for s in sections for e in head.get(s, [])}
    except (TypeError, KeyError):
        return None
    changed = {k for k in new if old.get(k) != new[k]}
    return changed, set(old) - set(new)


def image_lock_changes(base_text, head_text):
    """Changed ran_images components, or None when only a full rebuild is safe."""
    base, head = _json(base_text, 'base'), _json(head_text, 'head')
    if base is None or head is None:
        return None
    if {k: v for k, v in base.items() if k != 'ran_images'} != {k: v for k, v in head.items() if k != 'ran_images'}:
        return None
    old, new = base.get('ran_images'), head.get('ran_images')
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    if {k: v for k, v in old.items() if k != 'components'} != {k: v for k, v in new.items() if k != 'components'}:
        return None
    old, new = old.get('components', {}), new.get('components', {})
    return {k for k in set(old) | set(new) if old.get(k) != new.get(k)}


def classify(path, registry):
    """Return (class, affected entry keys, full_rebuild)."""
    entries = _entries(registry)
    if any(path.startswith(p) for p in RESEARCHER):
        return 'researcher-cicd', set(), False
    if path == REGISTRY_PATH:
        return 'registry', set(), False          # resolved by entry-level diff
    if path == LOCK_PATH:
        return 'image-lock', set(), False        # resolved by component-level diff
    if path in GENERATOR:
        return 'package-generator', set(), True
    if path in IMAGE_BUILD:
        component = IMAGE_BUILD[path]
        return 'container-build', {k for k, e in entries.items() for c in e['charts']
                                   if component in c.get('image_bindings', {}).values()}, False
    affected = set()
    for key, entry in entries.items():
        for chart in entry['charts']:
            if _under(path, chart['source']) or path in chart['values_files']:
                affected.add(key)
        review = entry.get('reviewed_legacy')
        if review and (_under(path, review['path']) or path == review['path'] + '.tar.gz'):
            affected.add(key)
    if affected:
        return 'package-input', affected, False
    blocked = {c[f] for c in registry.get('blocked_components', []) for f in ('knf_package', 'nsd_package') if c.get(f)}
    if any(_under(path, 'osm-packages/' + p) or path == 'osm-packages/' + p + '.tar.gz' for p in blocked):
        return 'blocked-component', set(), False
    if _under(path, 'osm-packages'):
        # Historical committed archives (for example legacy NSDs); the generator
        # rebuilds NSDs from the registry and reads only reviewed legacy KNF trees.
        return 'legacy-package-not-input', set(), False
    if _under(path, 'helm'):
        return 'helm-not-in-registry', set(), False
    if path in NON_PACKAGE_FILES or any(path.startswith(p) for p in NON_PACKAGE_PREFIXES):
        return 'non-package', set(), False
    return 'unknown', set(), True


def analyze(paths, registry, read_base=None, read_head=None):
    """Impact of changed paths. read_base/read_head(path) return text or None (absent).

    paths=None means the change set itself is unknown: full rebuild.
    """
    entries = _entries(registry)
    result = {'status': 'determined', 'changed_paths': [], 'paths': {}, 'full_rebuild_reasons': [],
              'removed_entries': []}
    affected = set()
    if paths is None:
        result['full_rebuild_reasons'].append('change set unavailable (no comparable base commit)')
    else:
        for path in sorted(set(paths)):
            if not isinstance(path, str) or not path or path.startswith('/') or '..' in path.split('/'):
                raise ImpactError('Unsafe changed path: ' + repr(path))
            kind, keys, full = classify(path, registry)
            if kind == 'registry' or kind == 'image-lock':
                if read_base is None or read_head is None:
                    keys, full = set(), True
                else:
                    diff = (registry_changes if kind == 'registry' else image_lock_changes)(read_base(path), read_head(path))
                    if diff is None:
                        keys, full = set(), True
                    elif kind == 'registry':
                        keys, removed = diff
                        result['removed_entries'].extend(sorted(removed))
                    else:
                        keys = {k for k, e in entries.items() for c in e['charts']
                                if diff & set(c.get('image_bindings', {}).values())}
            if full:
                result['full_rebuild_reasons'].append(kind + ': ' + path)
            affected |= keys
            result['paths'][path] = {'class': kind, 'affects': sorted(keys), 'full_rebuild': full}
        result['changed_paths'] = sorted(set(paths))
    if result['full_rebuild_reasons']:
        result['status'] = 'full-rebuild'
        affected = set(entries)
    affected &= set(entries)                     # removed entries cannot be built
    core = {e['key'] for e in registry.get('installer_components', [])}
    result['ran_scenarios'] = sorted(k for k in affected if k not in core and k != 'none')
    result['core_components'] = sorted(k for k in affected if k in core)
    result['blocked'] = {k: entries[k].get('blocked_reason') or 'not ready'
                         for k in sorted(affected) if k != 'none' and entries[k]['generation_status'] != 'ready'}
    result['buildable'] = sorted(k for k in affected if k != 'none' and entries[k]['generation_status'] == 'ready')
    return result


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, timeout=60).stdout


def git_changes(root, base, head):
    """Paths changed between two exact commits, or None when no comparable base exists."""
    if not SHA.fullmatch(head or ''):
        raise ImpactError('Head must be a full commit SHA')
    if not base or not SHA.fullmatch(base) or set(base) == {'0'}:
        return None
    try:
        git(root, 'cat-file', '-e', base + '^{commit}')
    except subprocess.CalledProcessError:
        return None
    output = git(root, 'diff', '--name-only', '--no-renames', '-z', base, head)
    return [p for p in output.decode().split('\0') if p]


def git_reader(root, revision):
    def read(path):
        try:
            return git(root, 'show', revision + ':' + path).decode()
        except subprocess.CalledProcessError:
            return None
    return read
