"""P2 release pipeline: change impact, packaging, image evidence, manifest and provenance.

Offline only: builds run in throwaway git copies of this repository, registry
access uses a fake opener, and the P1 OSM simulation stands in for the target.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]

import build_release  # noqa: E402
import release_images  # noqa: E402
import release_impact  # noqa: E402
import release_manifest  # noqa: E402
from scenario_registry import load_registry  # noqa: E402

REGISTRY = load_registry(ROOT)
ENTRIES = {e['key']: e for e in REGISTRY['scenarios'] + REGISTRY['installer_components']}
REPOSITORY = 'rathod9771/5g-kubernetes'
WORKFLOW = release_manifest.WORKFLOW_PATH
BRANCH = 'reproducible-installer'


def users(source):
    """Registry entries using a chart source: the independent oracle for shared charts."""
    return {k for k, e in ENTRIES.items() if any(c['source'] == source for c in e['charts'])}


def binding(component):
    return {k for k, e in ENTRIES.items() for c in e['charts'] if component in c.get('image_bindings', {}).values()}


def ci_env(head, branch=BRANCH, event='push', runner='github-hosted', workflow=WORKFLOW, **extra):
    ref = 'refs/heads/' + branch
    return dict({'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': runner, 'GITHUB_REPOSITORY': REPOSITORY,
                 'GITHUB_WORKFLOW_REF': REPOSITORY + '/' + workflow + '@' + ref, 'GITHUB_SHA': head,
                 'GITHUB_REF': ref, 'GITHUB_EVENT_NAME': event, 'GITHUB_RUN_ID': '424242',
                 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SERVER_URL': 'https://github.com'}, **extra)


def clean_env(extra=None):
    """Never inherit the real runner's GITHUB_* identity into fixture builds."""
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'HELM_BIN', 'LANG', 'TMPDIR')}
    env.update(extra or {})
    return env


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=test@invalid',
                           '-c', 'commit.gpgsign=false', *args], capture_output=True, check=True, text=True).stdout.strip()


class FixtureRepo:
    """A throwaway committed copy of the current tree on the release branch."""
    base_dir = None

    @classmethod
    def base(cls):
        if cls.base_dir is None:
            cls.temp = tempfile.TemporaryDirectory(prefix='release-fixture-')
            repo = Path(cls.temp.name) / 'base'
            files = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-co', '--exclude-standard', '-z'],
                                   capture_output=True, check=True).stdout.decode().split('\0')
            for name in filter(None, files):
                source = ROOT / name
                if source.is_file() and not source.is_symlink():
                    (repo / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, repo / name)
            git(repo, 'init', '-q', '-b', BRANCH)
            git(repo, 'add', '-A')
            git(repo, 'commit', '-q', '-m', 'fixture base')
            cls.base_dir = repo
        return cls.base_dir

    @classmethod
    def clone(cls, directory):
        target = Path(directory) / 'repo'
        subprocess.run(['git', 'clone', '-q', '--branch', BRANCH, str(cls.base()), str(target)], check=True,
                       capture_output=True)
        return target, git(target, 'rev-parse', 'HEAD')


def commit(repo, changes, message='change'):
    for name, content in changes.items():
        path = Path(repo) / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


def registry_edit(repo, key, **fields):
    data = json.loads((Path(repo) / 'config/scenarios.json').read_text())
    for section in ('scenarios', 'installer_components'):
        for entry in data.get(section, []):
            if entry['key'] == key:
                entry.update(fields)
    return json.dumps(data, indent=2) + '\n'


def cli(repo, base, head, output, env, *extra):
    return subprocess.run([sys.executable, str(Path(repo) / 'scripts/build_release.py'), '--base', base, '--head', head,
                           '--output', str(output), *extra], capture_output=True, text=True, env=clean_env(env),
                          cwd=str(repo), timeout=600)


def read_release(output):
    output = Path(output)
    return (output / release_manifest.MANIFEST_NAME).read_bytes(), lambda name: (output / name).read_bytes()


class ImpactTests(unittest.TestCase):
    def impact(self, paths, base=None, head=None):
        return release_impact.analyze(paths, REGISTRY, base, head)

    def test_cu_only_change_resolves_every_scenario_sharing_the_chart(self):
        result = self.impact(['helm/cran-srsran/cu/templates/cu.yaml'])
        self.assertEqual(set(result['ran_scenarios']), users('helm/cran-srsran/cu'))
        self.assertIn('fran-srsran', result['ran_scenarios'])
        self.assertEqual((result['status'], result['core_components']), ('determined', []))

    def test_du_only_change_includes_profiles_using_the_chart_differently(self):
        result = self.impact(['helm/cran-oai/du/templates/du.yaml'])
        self.assertEqual(set(result['ran_scenarios']), users('helm/cran-oai/du'))
        self.assertEqual(set(result['ran_scenarios']), {'cran-oai', 'cloudran-oai', 'vcran-oai', 'hcran-oai', 'fran-oai'})
        self.assertNotIn('oran-oai', result['ran_scenarios'])   # separate chart despite the similar name

    def test_shared_chart_across_implementations(self):
        result = self.impact(['helm/fog-compute/templates/deployment.yaml'])
        self.assertEqual(set(result['ran_scenarios']), {'fran-oai', 'fran-srsran'})

    def test_values_profile_inside_a_shared_chart_affects_all_its_users(self):
        # The generator hashes the whole chart directory, so a profile file changes every user's provenance.
        result = self.impact(['helm/cran-oai/du/values-hcran-macro.yaml'])
        self.assertEqual(set(result['ran_scenarios']), users('helm/cran-oai/du'))

    def test_multiple_affected_scenarios(self):
        result = self.impact(['helm/cran-oai/cu/templates/cu.yaml', 'helm/cran-srsran/du/templates/du.yaml',
                              'helm/fran-edge/templates/x.yaml'])
        self.assertEqual(set(result['ran_scenarios']),
                         users('helm/cran-oai/cu') | users('helm/cran-srsran/du') | users('helm/fran-edge'))

    def test_core_only_change(self):
        result = self.impact(['osm-packages/open5gs_knf/helm-chart-v3s/open5gs/values.yaml'])
        self.assertEqual((result['ran_scenarios'], result['core_components']), ([], ['open5gs']))

    def test_unrelated_and_researcher_changes_have_no_package_impact(self):
        paths = ['docs/troubleshooting.md', 'ran-selector/backend.py', 'tests/test_osm_packages.py', 'README.md',
                 'scripts/lifecycle.py', 'scripts/telecom/subcharts.py', 'tests/test_telecom_multi_image.py',
                 'helm/ueransim/values.yaml', 'osm-packages/cran_oai_ns.tar.gz']
        result = self.impact(paths)
        self.assertEqual((result['status'], result['ran_scenarios'], result['core_components']), ('determined', [], []))
        self.assertEqual(result['paths']['scripts/telecom/subcharts.py']['class'], 'researcher-cicd')
        self.assertEqual(result['paths']['helm/ueransim/values.yaml']['class'], 'helm-not-in-registry')

    def test_unknown_paths_force_a_full_rebuild(self):
        for path in ('newdir/tool.py', 'scripts/new_generator.py', 'config/new-policy.json'):
            with self.subTest(path=path):
                result = self.impact([path])
                self.assertEqual(result['status'], 'full-rebuild')
                self.assertEqual(set(result['buildable']),
                                 {k for k, e in ENTRIES.items() if e['generation_status'] == 'ready' and k != 'none'})
                self.assertIn('oran-srsran', result['blocked'])

    def test_generator_change_and_missing_base_force_a_full_rebuild(self):
        self.assertEqual(self.impact(['scripts/osm_packages.py'])['status'], 'full-rebuild')
        self.assertEqual(self.impact(None)['status'], 'full-rebuild')

    def test_registry_entry_level_changes(self):
        base = (ROOT / 'config/scenarios.json').read_text()
        head = registry_edit(ROOT, 'cran-srsran', display_name='changed')
        result = self.impact(['config/scenarios.json'], lambda p: base, lambda p: head)
        self.assertEqual((result['status'], result['ran_scenarios']), ('determined', ['cran-srsran']))
        data = json.loads(base)
        data['aliases']['x'] = 'cran-oai'
        self.assertEqual(self.impact(['config/scenarios.json'], lambda p: base,
                                     lambda p: json.dumps(data))['status'], 'full-rebuild')
        self.assertEqual(self.impact(['config/scenarios.json'], lambda p: '{broken', lambda p: head)['status'],
                         'full-rebuild')
        data = json.loads(base)
        data['scenarios'] = [s for s in data['scenarios'] if s['key'] != 'vcran-oai']
        result = self.impact(['config/scenarios.json'], lambda p: base, lambda p: json.dumps(data))
        self.assertEqual((result['removed_entries'], result['ran_scenarios']), (['vcran-oai'], []))

    def test_image_lock_component_level_changes(self):
        base = (ROOT / 'config/reference-versions.json').read_text()
        data = json.loads(base)
        data['ran_images']['components']['oai-du']['tag'] = '2026.w30'
        result = self.impact(['config/reference-versions.json'], lambda p: base, lambda p: json.dumps(data))
        self.assertEqual(set(result['ran_scenarios']), binding('oai-du'))
        data = json.loads(base)
        data['osm'] = {'changed': data['osm']}
        self.assertEqual(self.impact(['config/reference-versions.json'], lambda p: base,
                                     lambda p: json.dumps(data))['status'], 'full-rebuild')

    def test_container_build_definitions(self):
        result = self.impact(['scripts/build_srsran_from_source.sh'])
        self.assertEqual(set(result['ran_scenarios']), binding('srsran'))

    def test_blocked_scenarios_are_reported_not_built(self):
        result = self.impact(['helm/srsran-oran/cu/templates/x.yaml', 'osm-packages/ims_knf/x'])
        self.assertEqual(result['buildable'], [])
        self.assertIn('oran-srsran', result['blocked'])
        self.assertEqual(result['paths']['osm-packages/ims_knf/x']['class'], 'blocked-component')

    def test_unsafe_paths_are_rejected(self):
        for path in ('../escape', '/etc/passwd', ''):
            with self.subTest(path=path), self.assertRaises(release_impact.ImpactError):
                self.impact([path])

    def test_every_tracked_file_is_classified(self):
        files = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-co', '--exclude-standard'],
                               capture_output=True, check=True, text=True).stdout.split()
        unknown = [f for f in files if release_impact.classify(f, REGISTRY)[0] == 'unknown']
        self.assertEqual(unknown, [], 'new files need an explicit impact class')

    def test_every_generator_input_resolves_to_its_scenario(self):
        """Mirror osm_packages.source_snapshot: each read input must map back to the entry."""
        for key, entry in ENTRIES.items():
            if entry['generation_status'] != 'ready' or key == 'none':
                continue
            inputs = [c['source'] + '/' + p.relative_to(ROOT / c['source']).as_posix()
                      for c in entry['charts'] for p in (ROOT / c['source']).rglob('*') if p.is_file()]
            inputs += [v for c in entry['charts'] for v in c['values_files']]
            review = entry['reviewed_legacy']
            inputs += [review['path'] + '/' + p.relative_to(ROOT / review['path']).as_posix()
                       for p in (ROOT / review['path']).rglob('*') if p.is_file()] + [review['path'] + '.tar.gz']
            for path in inputs:
                with self.subTest(key=key, path=path):
                    self.assertIn(key, self.impact([path])['ran_scenarios'] + self.impact([path])['core_components'])


class ImageEvidenceTests(unittest.TestCase):
    def evidence(self, keys):
        return release_images.assess(ROOT, [ENTRIES[k] for k in keys],
                                     lambda chart: __import__('osm_packages').read_tree(ROOT / chart['source']))

    def test_missing_oai_digests_and_local_srsran_are_not_portable(self):
        evidence = self.evidence(['cran-oai', 'cran-srsran'])
        components = evidence['components']
        self.assertEqual((components['oai-cu']['status'], components['oai-cu']['digest']), ('MISSING_DIGEST', None))
        self.assertEqual(components['srsran']['status'], 'LOCAL_ONLY')
        self.assertEqual(components['srsran']['build']['status'], 'UNSUPPORTED')
        self.assertFalse(release_images.complete(evidence))

    def test_core_chart_images_with_mutable_tags(self):
        images = self.evidence(['open5gs'])['scenarios']['open5gs']['chart_images']
        self.assertTrue(images)
        self.assertTrue(all(i['status'] == 'MUTABLE_TAG' for i in images if not i['digest']))

    def test_pinned_digest_still_requires_registry_verification(self):
        evidence = self.evidence(['fran'])
        self.assertEqual(evidence['components']['fran-edge']['status'], 'PINNED_DIGEST')
        self.assertFalse(release_images.complete(evidence))
        evidence['components']['fran-edge']['registry_verification'] = 'VERIFIED'
        self.assertTrue(release_images.complete(evidence))


class EligibilityTests(unittest.TestCase):
    def manifest(self, channel):
        return {'channel': channel, 'ci_validation': {'status': 'passed'}, 'packages': [{'scenario': 'fran'}],
                'change_impact': {'blocked': {}}}

    def test_only_release_channel_can_be_publication_eligible(self):
        self.assertTrue(release_manifest.eligibility(self.manifest('release'), True)['publication_allowed'])
        for channel in ('development', 'pull-request', 'manual', 'local'):
            with self.subTest(channel=channel):
                verdict = release_manifest.eligibility(self.manifest(channel), True)
                self.assertFalse(verdict['publication_allowed'])
                self.assertIn('not an approved release branch', ' '.join(verdict['blockers']))

    def test_each_condition_blocks_and_deployment_is_never_allowed(self):
        cases = [(lambda m: m['ci_validation'].update(status='not-run'), True),
                 (lambda m: m.update(packages=[]), True),
                 (lambda m: m['change_impact'].update(blocked={'oran-srsran': 'blocked'}), True),
                 (lambda m: None, False)]
        for change, images in cases:
            manifest = self.manifest('release')
            change(manifest)
            verdict = release_manifest.eligibility(manifest, images)
            self.assertFalse(verdict['publication_allowed'])
            self.assertFalse(verdict['automatic_deployment_allowed'])
            self.assertEqual(verdict['provenance'], 'UNVERIFIED')


class FakeResponse(io.BytesIO):
    def __init__(self, body, kind='application/json'):
        super().__init__(body)
        self.headers = {'Content-Type': kind}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeRegistry:
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        for prefix, body in self.routes.items():
            if request.full_url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return FakeResponse(body)
        raise OSError('unexpected URL')


class RegistryVerificationTests(unittest.TestCase):
    def record(self, body, repository='docker.io/library/nginx'):
        return {'repository': repository, 'digest': 'sha256:' + hashlib.sha256(body).hexdigest()}

    def registry(self, manifest, extra=None):
        return FakeRegistry(dict({'https://auth.docker.io/': json.dumps({'token': 'anonymous'}).encode(),
                                  'https://registry-1.docker.io/v2/library/nginx/manifests/': manifest}, **(extra or {})))

    def test_index_with_amd64_is_verified_by_hash_and_platform(self):
        index = json.dumps({'mediaType': release_images.INDEX_TYPES[0], 'manifests': [
            {'platform': {'os': 'linux', 'architecture': 'arm64'}}, {'platform': {'os': 'linux', 'architecture': 'amd64'}}]}).encode()
        opener = self.registry(index)
        self.assertEqual(release_images.verify_registry(self.record(index), opener), 'VERIFIED')
        self.assertTrue(all(u.startswith('https://') for u in opener.urls))

    def test_digest_mismatch_platform_and_failures(self):
        index = json.dumps({'mediaType': release_images.INDEX_TYPES[0],
                            'manifests': [{'platform': {'os': 'linux', 'architecture': 'arm64'}}]}).encode()
        self.assertEqual(release_images.verify_registry(self.record(index), self.registry(index)), 'PLATFORM_MISSING')
        self.assertEqual(release_images.verify_registry(self.record(b'other'), self.registry(index)), 'DIGEST_MISMATCH')
        self.assertEqual(release_images.verify_registry(self.record(index), self.registry(OSError('offline'))),
                         'VERIFICATION_FAILED')

    def test_single_manifest_checks_config_blob(self):
        config = json.dumps({'os': 'linux', 'architecture': 'amd64'}).encode()
        manifest = json.dumps({'mediaType': release_images.MANIFEST_TYPES[0],
                               'config': {'digest': 'sha256:' + hashlib.sha256(config).hexdigest()}}).encode()
        opener = self.registry(manifest, {'https://registry-1.docker.io/v2/library/nginx/blobs/': config})
        self.assertEqual(release_images.verify_registry(self.record(manifest), opener), 'VERIFIED')

    def test_unsupported_registries_and_missing_digest(self):
        self.assertEqual(release_images.verify_registry({'repository': 'localhost/5g-kubernetes/srsran',
                                                         'digest': 'sha256:' + '0' * 64}), 'UNSUPPORTED_REGISTRY')
        self.assertEqual(release_images.verify_registry({'repository': 'ghcr.io/x/y', 'digest': 'sha256:' + '0' * 64}),
                         'UNSUPPORTED_REGISTRY')
        self.assertEqual(release_images.verify_registry({'repository': 'oaisoftwarealliance/oai-gnb', 'digest': None}),
                         'NO_DIGEST')


class ReleaseBuildTests(unittest.TestCase):
    """Real deterministic package generation in throwaway git copies."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='release-builds-')
        cls.dir = Path(cls.temp.name)
        # One CI-identity release-branch build of a small change, shared by the verifier tests.
        cls.repo, cls.base = FixtureRepo.clone(cls.dir / 'shared')
        cls.head = commit(cls.repo, {'helm/fran-edge/templates/z-note.yaml': '# release fixture\n'})
        cls.output = cls.dir / 'release'
        result = cli(cls.repo, cls.base, cls.head, cls.output, ci_env(cls.head), '--ci-validation', 'passed')
        assert result.returncode == 0, result.stderr
        cls.manifest_bytes, read = read_release(cls.output)
        cls.read = staticmethod(read)
        cls.manifest = json.loads(cls.manifest_bytes)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def expected(self, **changes):
        return dict({'repository': REPOSITORY, 'branches': (BRANCH,), 'commit': self.head, 'workflow_path': WORKFLOW},
                    **changes)

    def verify(self, manifest_bytes=None, read=None, attestation=None, **expected):
        return release_manifest.verify(manifest_bytes or self.manifest_bytes, read or self.read,
                                       self.expected(**expected), REGISTRY, attestation)

    def tampered(self, change):
        manifest = copy.deepcopy(self.manifest)
        change(manifest)
        return release_manifest.render(manifest)

    def proof(self, manifest_bytes=None, **changes):
        data = manifest_bytes or self.manifest_bytes
        return lambda manifest, expected: dict({'verified': True, 'subject_sha256': hashlib.sha256(data).hexdigest(),
                                                'repository': REPOSITORY, 'commit': self.head,
                                                'ref': 'refs/heads/' + BRANCH, 'workflow_path': WORKFLOW,
                                                'runner_environment': 'github-hosted'}, **changes)

    # --- content of a release-branch candidate ------------------------------------------------------
    def test_release_branch_candidate_contents(self):
        m = self.manifest
        self.assertEqual((m['schema'], m['schema_version'], m['channel']), (release_manifest.SCHEMA, 1, 'release'))
        self.assertEqual((m['repository'], m['source']['commit'], m['source']['base_commit']), (REPOSITORY, self.head, self.base))
        self.assertEqual((m['workflow']['run_id'], m['workflow']['run_attempt'], m['workflow']['runner_environment']),
                         (424242, 1, 'github-hosted'))
        self.assertEqual(m['change_impact']['ran_scenarios'], ['fran'])
        self.assertEqual([p['scenario'] for p in m['packages']], ['fran'])
        self.assertEqual(m['packages'][0]['knf']['descriptor_id'], 'fran_knf')   # OSM descriptor id unchanged
        self.assertEqual(set(m['artifacts']), {'packages/fran_knf.tar.gz', 'packages/fran_ns.tar.gz',
                                               'provenance/fran.provenance.json'})
        self.assertFalse(m['eligibility']['automatic_deployment_allowed'])
        self.assertEqual(m['eligibility']['provenance'], 'UNVERIFIED')
        self.assertFalse(m['eligibility']['publication_allowed'])               # registry digest not verified
        sums = (self.output / 'SHA256SUMS').read_text().splitlines()
        self.assertEqual(len(sums), 4)
        for line in sums:
            digest, name = line.split('  ')
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), digest)

    def test_provenance_inputs_are_covered_by_impact_analysis(self):
        provenance = json.loads(self.read('provenance/fran.provenance.json'))
        for path in provenance['source_inputs']:
            path = path.split('#', 1)[0]
            with self.subTest(path=path):
                result = release_impact.analyze([path], REGISTRY, lambda p: None, lambda p: None)
                self.assertTrue('fran' in result['ran_scenarios'] or result['status'] == 'full-rebuild')
        for name in provenance['generator']['implementation_sha256']:
            self.assertIn('scripts/' + name, release_impact.GENERATOR)

    # --- consumer verification ------------------------------------------------------------------------
    def test_valid_manifest_without_attestation_is_unverified(self):
        verdict = self.verify()
        self.assertEqual((verdict['provenance'], verdict['publication_eligible'], verdict['deployment_eligible']),
                         ('UNVERIFIED', False, False))

    def test_attestation_binding_is_required_and_checked(self):
        verdict = self.verify(attestation=self.proof())
        self.assertEqual(verdict['provenance'], 'VERIFIED')
        self.assertFalse(verdict['publication_eligible'])                     # images still blocked
        for change in ({'subject_sha256': '0' * 64}, {'commit': 'c' * 40}, {'workflow_path': '.github/workflows/x.yml'},
                       {'ref': 'refs/heads/main'}, {'runner_environment': 'self-hosted'}, {'verified': False},
                       {'repository': 'attacker/5g-kubernetes'}):
            with self.subTest(change=change), self.assertRaises(release_manifest.ManifestError):
                self.verify(attestation=self.proof(**change))

    def test_altered_package_bytes_are_rejected(self):
        def read(name):
            data = self.read(name)
            return data + b'\0' if name.endswith('fran_knf.tar.gz') else data
        with self.assertRaisesRegex(release_manifest.ManifestError, 'altered'):
            self.verify(read=read)
        with self.assertRaisesRegex(release_manifest.ManifestError, 'altered or missing'):
            self.verify(read=lambda name: None if name.endswith('.json') else self.read(name))

    def test_source_commit_workflow_branch_and_repository_mismatches(self):
        for expected in ({'commit': 'd' * 40}, {'workflow_path': '.github/workflows/helm-validate.yml'},
                         {'branches': ('main',)}, {'repository': 'other/repo'}):
            with self.subTest(expected=expected), self.assertRaises(release_manifest.ManifestError):
                self.verify(**expected)
        for change in (lambda m: m['source'].update(ref='refs/heads/main'),
                       lambda m: m['workflow'].update(ref=REPOSITORY + '/' + WORKFLOW + '@refs/heads/other'),
                       lambda m: m['workflow'].update(runner_environment='self-hosted'),
                       lambda m: m.update(channel='development'),
                       lambda m: m['ci_validation'].update(status='not-run')):
            with self.subTest(change=change), self.assertRaises(release_manifest.ManifestError):
                self.verify(self.tampered(change))

    def test_incomplete_or_inconsistent_packages_are_rejected(self):
        for change in (lambda m: m.update(packages=[]),
                       lambda m: m['change_impact'].update(buildable=['fran', 'cran-oai']),
                       lambda m: m['artifacts'].pop('packages/fran_ns.tar.gz'),
                       lambda m: m['packages'][0]['knf'].update(descriptor_id='fran_knf-v2'),
                       lambda m: m['packages'][0].update(scenario='oran-srsran'),
                       lambda m: m['images']['scenarios'].pop('fran'),
                       lambda m: m['images']['components'].pop('fran-edge'),
                       lambda m: m['eligibility'].update(automatic_deployment_allowed=True),
                       lambda m: m['eligibility'].update(publication_allowed=True, blockers=[])):
            with self.subTest(change=change), self.assertRaises(release_manifest.ManifestError):
                self.verify(self.tampered(change))

    def test_malformed_and_credential_bearing_manifests(self):
        text = self.manifest_bytes.decode()
        duplicate = text.replace('"channel": "release",', '"channel": "release",\n  "channel": "release",', 1)
        for data in (duplicate.encode(), text.replace('"run_attempt": 1', '"run_attempt": NaN').encode(), b'\xff'):
            with self.assertRaises(release_manifest.ManifestError):
                self.verify(data)
        for change in (lambda m: m['target'].update(osm_password='x'),
                       lambda m: m['target'].update(environment='-----BEGIN RSA PRIVATE KEY-----')):
            manifest = copy.deepcopy(self.manifest)
            change(manifest)
            # Bypass render(), which refuses this too: the verifier must catch it on its own.
            raw = json.dumps(manifest, sort_keys=True).encode()
            with self.assertRaisesRegex(release_manifest.ManifestError, 'credential-like material'):
                self.verify(raw)
        with self.assertRaises(release_manifest.ManifestError):
            release_manifest.render({'kubeconfig': 'apiVersion: v1'})

    # --- builds ---------------------------------------------------------------------------------------
    def test_repeated_builds_are_deterministic(self):
        second = self.dir / 'repeat'
        result = cli(self.repo, self.base, self.head, second, ci_env(self.head), '--ci-validation', 'passed')
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in self.manifest['artifacts']:
            self.assertEqual((second / name).read_bytes(), self.read(name))
        other = json.loads((second / release_manifest.MANIFEST_NAME).read_bytes())
        self.assertEqual({k: v for k, v in other.items() if k != 'built_at'},
                         {k: v for k, v in self.manifest.items() if k != 'built_at'})

    def test_concurrent_builds_never_share_output_and_agree(self):
        outputs = [self.dir / 'concurrent-a', self.dir / 'concurrent-b', self.dir / 'concurrent-a']
        results = [None] * 3

        def run(index):
            results[index] = cli(self.repo, self.base, self.head, outputs[index], ci_env(self.head))
        threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(600)
        codes = sorted(r.returncode for r in results)
        self.assertEqual(codes, [0, 0, 1])                    # the duplicate output claim loses atomically
        self.assertIn('already exists', ''.join(r.stderr for r in results))
        for name in self.manifest['artifacts']:
            self.assertEqual((outputs[0] / name).read_bytes(), (outputs[1] / name).read_bytes())

    def test_wrong_commit_dirty_tree_self_hosted_and_wrong_workflow_are_refused(self):
        cases = [(self.head, ci_env('e' * 40)), ('f' * 40, ci_env('f' * 40)),
                 (self.head, ci_env(self.head, runner='self-hosted')),
                 (self.head, ci_env(self.head, workflow='.github/workflows/helm-validate.yml'))]
        for index, (head, env) in enumerate(cases):
            with self.subTest(index=index):
                output = self.dir / ('refused-%d' % index)
                result = cli(self.repo, self.base, head, output, env)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(output.exists())
        (self.repo / 'untracked.txt').write_text('debris')
        try:
            result = cli(self.repo, self.base, self.head, self.dir / 'dirty', ci_env(self.head))
            self.assertIn('not clean', result.stderr)
        finally:
            (self.repo / 'untracked.txt').unlink()

    def test_feature_branch_and_pull_request_builds_are_never_eligible(self):
        for env, expected in ((ci_env(self.head, branch='feature/p2'), 'development'),
                              (ci_env(self.head, event='pull_request'), 'pull-request'),
                              (ci_env(self.head, event='workflow_dispatch'), 'manual'), ({}, 'local')):
            with self.subTest(channel=expected):
                output = self.dir / ('channel-' + expected)
                if env and env['GITHUB_EVENT_NAME'] != 'push':
                    env = dict(env, GITHUB_WORKFLOW_REF=REPOSITORY + '/' + WORKFLOW + '@refs/heads/' + BRANCH)
                result = cli(self.repo, self.base, self.head, output, env, '--ci-validation', 'passed')
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest_bytes, read = read_release(output)
                manifest = json.loads(manifest_bytes)
                self.assertEqual(manifest['channel'], expected)
                self.assertFalse(manifest['eligibility']['publication_allowed'])
                with self.assertRaises(release_manifest.ManifestError):
                    release_manifest.verify(manifest_bytes, read, self.expected(), REGISTRY, self.proof(manifest_bytes))

    def test_package_generation_failure_publishes_nothing(self):
        directory = self.dir / 'broken'
        repo, base = FixtureRepo.clone(directory)
        head = commit(repo, {'helm/fran-edge/templates/broken.yaml': '{{ if }}\n'})
        output = directory / 'out'
        result = cli(repo, base, head, output, ci_env(head))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())
        head = commit(repo, {'config/scenarios.json': registry_edit(repo, 'fran', charts=[
            dict(ENTRIES['fran']['charts'][0], source='helm/does-not-exist')])})
        result = cli(repo, git(repo, 'rev-parse', 'HEAD~1'), head, directory / 'out2', ci_env(head))
        self.assertEqual(result.returncode, 1)
        self.assertFalse((directory / 'out2').exists())

    def test_docs_only_change_produces_no_packages(self):
        directory = self.dir / 'docs'
        repo, base = FixtureRepo.clone(directory)
        head = commit(repo, {'docs/troubleshooting.md': 'changed\n', 'scripts/telecom/x.py': 'researcher\n'})
        result = cli(repo, base, head, directory / 'out', ci_env(head), '--ci-validation', 'passed')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((directory / 'out' / release_manifest.MANIFEST_NAME).read_bytes())
        self.assertEqual((manifest['packages'], manifest['change_impact']['status']), ([], 'determined'))
        self.assertIn('no packages were generated', manifest['eligibility']['blockers'])

    def test_no_network_and_only_git_or_helm_processes(self):
        directory = self.dir / 'isolated'
        events, active = [], [True]

        def hook(event, args):
            if not active[0]:
                return
            if event in ('socket.connect', 'socket.getaddrinfo', 'urllib.Request'):
                events.append(event)
                raise RuntimeError('network forbidden: ' + event)
            if event == 'subprocess.Popen':
                program = os.path.basename(str(args[1][0] if isinstance(args[1], (list, tuple)) else args[1]))
                if program not in ('git', 'helm', 'helm.real', os.path.basename(os.environ.get('HELM_BIN', 'helm'))):
                    events.append(program)
                    raise RuntimeError('process forbidden: ' + program)
        sys.addaudithook(hook)       # hooks cannot be removed; it is disabled after this build
        try:
            manifest = build_release.build_release(self.repo, self.base, self.head, directory, ci_env(self.head),
                                                   ci_validation='passed', clock=lambda: 0)
        finally:
            active[0] = False
        self.assertEqual(events, [])
        self.assertEqual([p['scenario'] for p in manifest['packages']], ['fran'])

    def test_credentials_in_the_environment_never_reach_artifacts(self):
        sentinel = 'S3NTINEL-SECRET-VALUE'
        output = self.dir / 'credentials'
        env = ci_env(self.head, OSM_PASSWORD=sentinel, GITHUB_TOKEN=sentinel, KUBECONFIG='/' + sentinel)
        result = cli(self.repo, self.base, self.head, output, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        for file in output.rglob('*'):
            if file.is_file():
                data = file.read_bytes()
                if file.suffix == '.gz':
                    import gzip
                    data += gzip.decompress(data)
                self.assertNotIn(sentinel.encode(), data, str(file))
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        self.assertEqual(release_manifest.credential_findings(json.loads((output / 'release-manifest.json').read_bytes())), [])


class ActiveInactiveContractTests(unittest.TestCase):
    """Task 6 contract through P1: GitHub never decides the active RAN; the target does, under the lock."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='release-contract-')
        directory = Path(cls.temp.name)
        cls.repo, base = FixtureRepo.clone(directory)
        cls.builds = {}
        def evidence(root, entries, reader):
            # Future state only: every bound image pinned by digest and registry-verified.
            real = release_images.assess(root, entries, reader)
            for record in real['components'].values():
                record.update(status='PINNED_DIGEST', digest='sha256:' + '1' * 64, portable=True,
                              registry_verification='VERIFIED')
            return real
        for key in ('cran-srsran', 'cran-oai'):
            previous = git(cls.repo, 'rev-parse', 'HEAD')
            head = commit(cls.repo, {'config/scenarios.json': registry_edit(cls.repo, key, display_name=key + ' (release)')})
            with mock.patch.object(build_release, 'assess', side_effect=evidence):
                manifest = build_release.build_release(cls.repo, previous, head, directory / key, ci_env(head),
                                                       ci_validation='passed', clock=lambda: 0)
            cls.builds[key] = (head, manifest)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import test_lifecycle_coordination as harness
        self.harness = harness.LifecycleHarness('setUp')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def release(self, key):
        head, manifest = self.builds[key]
        self.assertEqual(manifest['change_impact']['ran_scenarios'], [key])
        self.assertTrue(manifest['eligibility']['publication_allowed'], manifest['eligibility']['blockers'])
        manifest_bytes, read = read_release(Path(self.temp.name) / key)
        expected = {'repository': REPOSITORY, 'branches': (BRANCH,), 'commit': head, 'workflow_path': WORKFLOW}
        proof = lambda data, exp: {'verified': True, 'subject_sha256': hashlib.sha256(data).hexdigest(),  # noqa: E731
                                   'repository': REPOSITORY, 'commit': head, 'ref': 'refs/heads/' + BRANCH,
                                   'workflow_path': WORKFLOW, 'runner_environment': 'github-hosted'}
        unverified = release_manifest.verify(manifest_bytes, read, expected, REGISTRY)
        with self.assertRaises(release_manifest.ManifestError):
            release_manifest.lifecycle_release(manifest_bytes, unverified, read, 'hp-lab')
        verdict = release_manifest.verify(manifest_bytes, read, expected, REGISTRY, proof)
        return release_manifest.lifecycle_release(manifest_bytes, verdict, read, 'hp-lab')

    def process(self, key):
        import lifecycle
        release, blobs = self.release(key)
        h = self.harness
        return lifecycle.process_release(h.osm, h.observer, REGISTRY, release, blobs, h.state_path,
                                         __import__('test_lifecycle_coordination').CONTEXT, h.policy, clock=h.clock)

    def test_inactive_ran_change_is_catalog_only(self):
        result = self.process('cran-srsran')          # active RAN in the harness is cran-oai
        self.assertEqual(result['action'], 'catalog-only')
        self.assertEqual(self.harness.actions(), [])
        self.harness.assert_no_replacement()
        self.assertEqual(self.harness.state()['active'], 'cran-oai')

    def test_verdict_is_bound_to_the_verified_manifest(self):
        verdicts = {}
        for key in ('cran-srsran', 'cran-oai'):
            head = self.builds[key][0]
            manifest_bytes, read = read_release(Path(self.temp.name) / key)
            proof = lambda data, exp, head=head: {  # noqa: E731
                'verified': True, 'subject_sha256': hashlib.sha256(data).hexdigest(), 'repository': REPOSITORY,
                'commit': head, 'ref': 'refs/heads/' + BRANCH, 'workflow_path': WORKFLOW,
                'runner_environment': 'github-hosted'}
            expected = {'repository': REPOSITORY, 'branches': (BRANCH,), 'commit': head, 'workflow_path': WORKFLOW}
            verdicts[key] = (release_manifest.verify(manifest_bytes, read, expected, REGISTRY, proof), manifest_bytes, read)
        verdict = verdicts['cran-srsran'][0]
        _, other_bytes, other_read = verdicts['cran-oai']
        self.assertEqual((verdict['provenance'], verdict['publication_eligible']), ('VERIFIED', True))
        with self.assertRaisesRegex(release_manifest.ManifestError, 'different manifest'):
            release_manifest.lifecycle_release(other_bytes, verdict, other_read, 'hp-lab')

    def test_active_ran_change_upgrades_the_same_scenario(self):
        result = self.process('cran-oai')
        self.assertEqual((result['action'], result['scenario']), ('upgraded', 'cran-oai'))
        self.assertEqual({ns for _, _, ns in self.harness.actions()}, {self.harness.active_id})
        self.harness.assert_no_replacement()

    def test_active_ran_change_waits_for_the_p4_gate(self):
        import lifecycle
        with mock.patch.object(lifecycle, 'P4_KDU_UPGRADE_APPROVAL', None):   # the shipped value
            result = self.process('cran-oai')
        self.assertEqual((result['action'], result['scenario']), ('approval-required', 'cran-oai'))
        self.assertEqual(self.harness.actions(), [])
        self.harness.assert_no_replacement()

    # --- pre-attestation publication decision (workflow gate) ----------------------------------
    def decision(self, key, change=None, read_change=None, **expected):
        head, _ = self.builds[key]
        manifest_bytes, read = read_release(Path(self.temp.name) / key)
        if change:
            manifest = json.loads(manifest_bytes)
            change(manifest)
            manifest_bytes = release_manifest.render(manifest)
        exp = dict({'repository': REPOSITORY, 'branches': (BRANCH,), 'commit': head, 'workflow_path': WORKFLOW}, **expected)
        return release_manifest.publication_decision(manifest_bytes, read_change(read) if read_change else read, exp, REGISTRY)

    def test_complete_release_candidate_is_publication_allowed(self):
        self.assertEqual(self.decision('cran-oai'), {'publication_allowed': True, 'blockers': []})

    def test_incomplete_or_blocked_candidates_are_never_publication_allowed(self):
        def unverified_image(m):
            next(iter(m['images']['components'].values()))['registry_verification'] = 'UNVERIFIED'
        def local_image(m):
            next(iter(m['images']['components'].values())).update(status='LOCAL_ONLY', digest=None)
        def blocked(m):
            m['change_impact']['blocked']['oran-srsran'] = 'not reconciled'
        def blocker_recorded(m):
            m['eligibility']['blockers'] = ['builder blocker']
        def claim_false(m):
            m['eligibility']['publication_allowed'] = False
        def deploy_claim(m):
            m['eligibility']['automatic_deployment_allowed'] = True
        def manual(m):
            m['channel'] = 'manual'
        def ci_failed(m):
            m['ci_validation']['status'] = 'not-run'
        def self_hosted(m):
            m['workflow']['runner_environment'] = 'self-hosted'
        def no_packages(m):
            m['packages'], m['artifacts'] = [], {}
        def no_eligibility(m):
            del m['eligibility']
        cases = [unverified_image, local_image, blocked, blocker_recorded, claim_false, deploy_claim, manual, ci_failed,
                 self_hosted, no_packages, no_eligibility]
        for change in cases:
            with self.subTest(change=change.__name__):
                decision = self.decision('cran-oai', change)
                self.assertIs(decision['publication_allowed'], False)
                self.assertTrue(decision['blockers'])
        with self.subTest('altered package bytes'):
            altered = lambda read: lambda name: read(name) + (b'x' if name.startswith('packages/') else b'')  # noqa: E731
            self.assertFalse(self.decision('cran-oai', read_change=altered)['publication_allowed'])
        for expected in ({'commit': 'f' * 40}, {'branches': ('main',)}, {'repository': 'other/repo'}):
            with self.subTest(expected=expected):
                self.assertFalse(self.decision('cran-oai', **expected)['publication_allowed'])

    def test_decision_cli_exit_status_fails_closed(self):
        head, _ = self.builds['cran-oai']
        args = ['publication-decision', '--release', str(Path(self.temp.name) / 'cran-oai'), '--repository', REPOSITORY,
                '--commit', head, '--branch', BRANCH]
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(release_manifest.main(args), 0)
        self.assertIs(json.loads(out.getvalue())['publication_allowed'], True)
        for bad in (['--commit', 'f' * 40], ['--release', str(Path(self.temp.name) / 'missing')]):
            changed = list(args)
            changed[changed.index(bad[0]) + 1] = bad[1]
            with self.subTest(bad=bad), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(release_manifest.main(changed), 3)
            self.assertIs(json.loads(out.getvalue())['publication_allowed'], False)


class WorkflowSafetyTests(unittest.TestCase):
    workflow = yaml.safe_load((ROOT / '.github/workflows/release-candidate.yml').read_text())
    text = (ROOT / '.github/workflows/release-candidate.yml').read_text()

    def test_github_hosted_only_and_no_target_credentials(self):
        for name, job in self.workflow['jobs'].items():
            self.assertEqual(job['runs-on'], 'ubuntu-latest', name)
        for forbidden in ('self-hosted', 'secrets.', 'kubectl', 'osm_onboard', 'helm install', 'helm upgrade',
                          'KUBECONFIG', 'OSM_PASSWORD', 'ns_instances', 'deploy.sh'):
            self.assertNotIn(forbidden, self.text.replace('HP self-hosted runner is never targeted', ''))

    def test_triggers_permissions_and_concurrency(self):
        triggers = self.workflow.get('on', self.workflow.get(True))
        self.assertEqual(triggers['push']['branches'], ['reproducible-installer', 'feature/**'])
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        self.assertNotIn('permissions', self.workflow['jobs']['build'])
        self.assertIn('github.ref', self.workflow['concurrency']['group'])
        self.assertEqual(self.workflow['concurrency']['cancel-in-progress'], "${{ github.event_name == 'pull_request' }}")
        attest = self.workflow['jobs']['attest']
        self.assertEqual(attest['permissions'], {'contents': 'read', 'id-token': 'write', 'attestations': 'write'})
        self.assertIn("github.event_name == 'push'", attest['if'])
        self.assertIn("github.ref == 'refs/heads/reproducible-installer'", attest['if'])
        self.assertIn("needs.build.outputs.publication_allowed == 'true'", attest['if'])
        # The only command in the OIDC job is the inline, isolated re-check; it runs no repository code.
        runs = [step for step in attest['steps'] if 'run' in step]
        self.assertEqual(len(runs), 1)
        self.assertIn('python3 -I -', runs[0]['run'])
        self.assertNotIn('scripts/', runs[0]['run'])
        names = [step.get('uses', step.get('name', '')) for step in attest['steps']]
        attest_step = next(i for i, n in enumerate(names) if n.startswith('actions/attest-build-provenance@'))
        self.assertLess(attest['steps'].index(runs[0]), attest_step)

    def test_publication_decision_is_computed_from_the_built_candidate(self):
        build = self.workflow['jobs']['build']
        self.assertEqual(build['outputs']['publication_allowed'], '${{ steps.decision.outputs.publication_allowed }}')
        steps = build['steps']
        decision = next(s for s in steps if s.get('id') == 'decision')
        release = next(s for s in steps if s.get('id') == 'release')
        self.assertLess(steps.index(release), steps.index(decision))
        run = decision['run']
        self.assertIn('scripts/release_manifest.py publication-decision', run)
        self.assertIn('--commit "$GITHUB_SHA"', run)
        self.assertIn('--branch "$RELEASE_BRANCH"', run)
        self.assertNotIn('inputs.', self.text)                  # no dispatch input can influence the decision
        self.assertIn('allowed=false', run)
        self.assertEqual(run.count('allowed=true'), 1)

    def run_attest_gate(self, manifest, commit='c' * 40):
        script = next(s for s in self.workflow['jobs']['attest']['steps'] if 'run' in s)['run']
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'release').mkdir()
            (Path(directory) / 'release/release-manifest.json').write_text(json.dumps(manifest))
            env = {'PATH': os.environ['PATH'], 'EXPECTED_COMMIT': commit, 'RELEASE_BRANCH': BRANCH}
            return subprocess.run(['bash', '-c', script], cwd=directory, env=env, capture_output=True).returncode

    def test_attest_job_gate_rejects_incomplete_candidates(self):
        good = {'channel': 'release', 'source': {'commit': 'c' * 40, 'branch': BRANCH}, 'packages': [{'scenario': 'fran'}],
                'eligibility': {'publication_allowed': True, 'blockers': [], 'automatic_deployment_allowed': False}}
        self.assertEqual(self.run_attest_gate(good), 0)
        bad = [('blockers', lambda m: m['eligibility'].update(blockers=['image identity evidence incomplete'])),
               ('not allowed', lambda m: m['eligibility'].update(publication_allowed=False)),
               ('truthy string', lambda m: m['eligibility'].update(publication_allowed='true')),
               ('deployment', lambda m: m['eligibility'].update(automatic_deployment_allowed=True)),
               ('channel', lambda m: m.update(channel='manual')),
               ('commit', lambda m: m['source'].update(commit='d' * 40)),
               ('branch', lambda m: m['source'].update(branch='main')),
               ('no packages', lambda m: m.update(packages=[])),
               ('no eligibility', lambda m: m.pop('eligibility'))]
        for name, change in bad:
            manifest = copy.deepcopy(good)
            change(manifest)
            with self.subTest(name):
                self.assertNotEqual(self.run_attest_gate(manifest), 0)

    def test_exact_commit_pinned_actions_and_existing_workflow_preserved(self):
        steps = self.workflow['jobs']['build']['steps']
        checkout = steps[0]
        self.assertEqual((checkout['with']['ref'], checkout['with']['persist-credentials']), ('${{ github.sha }}', False))
        for job in self.workflow['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$')
        commands = '\n'.join(step.get('run', '') for step in steps)
        self.assertLess(commands.index('unittest discover'), commands.index('build_release.py'))
        self.assertIn('--ci-validation passed', commands)
        self.assertTrue((ROOT / '.github/workflows/helm-validate.yml').is_file())
        upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['if'], "github.event_name != 'pull_request'")


if __name__ == '__main__':
    unittest.main()
