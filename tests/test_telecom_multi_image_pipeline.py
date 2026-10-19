"""schema_version=2 pipeline integration: live per-image registry verification,
fail-closed publication gating, and the honest tag-vs-digest distinction.

This chart family (examples/telecom/multi-nf-chart, modeled on the real
free5gc charts independently inspected this session) concatenates
`repository:tag` directly in its templates -- it cannot express an immutable
`repository@sha256` reference through Helm values. Every test below that
completes a real pipeline run therefore expects `digest_pinned_execution` to
be False even on success: every declared image IS independently, live
registry-verified (content + architecture) before a release is sealed, but
the rendered Kubernetes object itself still only pins a mutable tag, so the
release is correctly recorded as not digest-pinned and the catalog/publish
gate (registry_gate) stays closed for it. Offline only: `prebuilt`/
`resolve_tag` are mocked, exactly like the existing single-image pipeline
tests mock `telecom.pipeline.prebuilt` -- no live registry, cluster or OSM
call is made anywhere in this file.
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from telecom.execution import ExecutionError, Policy
from telecom.pipeline import Pipeline
from telecom.security import Denied, Principal
from telecom.service import Service

HELM = '/usr/local/bin/helm.real' if Path('/usr/local/bin/helm.real').exists() else 'helm'
MANIFEST = json.loads((ROOT / 'examples/telecom/multi-nf.json').read_text())
CHART = ROOT / 'examples/telecom/multi-nf-chart'


class MultiImagePipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '--template=', str(self.repo)], check=True, capture_output=True)
        shutil.copytree(CHART, self.repo / 'chart')
        for args in (['add', '.'], ['-c', 'user.name=Offline', '-c', 'user.email=offline@example.invalid', 'commit', '-m', 'fixture']):
            subprocess.run(['git', '-C', str(self.repo)] + args, check=True, capture_output=True)
        self.sha = subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD']).decode().strip()
        self.source = {'kind': 'local-git', 'path': 'repo', 'commit': self.sha}
        self.policy = Policy({'local_roots': {'alice': '.'},
                               'oci_repositories': sorted({e['repository'] for e in MANIFEST['images']})})
        self.service = Service(self.root / 'runtime', self.root, helm=HELM)
        self.pipeline = Pipeline(self.service, self.policy)
        self.principal = Principal('researcher', 'alice', frozenset({'researcher', 'builder', 'operator', 'publisher'}))
        self.reviewer = Principal('reviewer', 'alice', frozenset({'reviewer'}))
        manifest = copy.deepcopy(MANIFEST)
        manifest['source'] = self.source
        self.workload = self.service.register(self.principal, manifest, 'register')
        self.digests = {(e['repository'], e['tag_pin']): e['digest'] for e in MANIFEST['images']}

    def tearDown(self):
        self.tmp.cleanup()

    def mocks(self, *, platform='linux/amd64', tag_digests=None, prebuilt_side_effect=None):
        resolved = tag_digests if tag_digests is not None else self.digests
        def resolve_tag(repository, tag_pin, policy, tenant, secret_ref):
            return resolved[(repository, tag_pin)]
        def prebuilt(repository, digest, policy, tenant, secret_ref):
            if prebuilt_side_effect is not None:
                prebuilt_side_effect(repository, digest)
            return {'digest': digest, 'platform': platform, 'verification': 'REGISTRY_CONTENT_VERIFIED', 'requests': 1}
        return (mock.patch('telecom.pipeline.resolve_tag', side_effect=resolve_tag),
                mock.patch('telecom.pipeline.prebuilt', side_effect=prebuilt))

    def run_job(self, event='queue', **mock_kwargs):
        job = self.pipeline.queue(self.principal, self.workload['id'], 'chart', event)
        resolve_patch, prebuilt_patch = self.mocks(**mock_kwargs)
        with resolve_patch, prebuilt_patch:
            return self.pipeline.run(self.principal, job['id'])

    def test_v2_pipeline_verifies_every_image_live_but_is_not_digest_pinned(self):
        result = self.run_job()
        self.assertEqual(result['state'], 'COMPLETED', result)
        release = self.service.journal.get(self.principal, 'releases', result['release_id'])
        self.assertEqual(release['schema_version'], 2)
        self.assertEqual(set(release['image_verification']), {'amf', 'smf', 'upf', 'mongodb', 'probe'})
        self.assertFalse(release['digest_pinned_execution'])
        declared = {e['identity']: e for e in MANIFEST['images']}
        for identity, evidence in release['image_verification'].items():
            self.assertEqual(evidence['verification'], 'REGISTRY_CONTENT_VERIFIED')
            self.assertFalse(evidence['digest_pinned_execution'])
            self.assertEqual(evidence['digest'], declared[identity]['digest'])
            self.assertEqual(evidence['rendered_reference_kind'], 'tag')
        self.assertFalse(release['production_eligible'])

    def test_wrong_architecture_fails_closed_no_release_persisted(self):
        result = self.run_job(platform='linux/arm64')
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(result['diagnostic']['code'], 'PLATFORM_MISMATCH')
        self.assertEqual(self.service.journal.list(self.principal, 'releases'), [])

    def test_changed_registry_tag_detected_and_blocks_release(self):
        drifted = dict(self.digests)
        drifted[('example.invalid/research/amf', 'v1.0.0')] = 'sha256:' + '9' * 64
        result = self.run_job(tag_digests=drifted)
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(result['diagnostic']['code'], 'REGISTRY_TAG_DIGEST_DRIFT')
        self.assertEqual(self.service.journal.list(self.principal, 'releases'), [])

    def test_partial_multi_image_verification_failure_blocks_entire_release(self):
        checked = []
        def fail_upf(repository, digest):
            checked.append(repository)
            if repository.endswith('/upf'):
                raise ExecutionError('registry', 'FETCH_FAILED')
        result = self.run_job(prebuilt_side_effect=fail_upf)
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertIn('example.invalid/research/upf', checked)
        # Other images were independently checked before the failure, but
        # since one failed, nothing is sealed: all-or-nothing, not partial.
        self.assertEqual(self.service.journal.list(self.principal, 'releases'), [])
        self.assertEqual(self.service.journal.list(self.principal, 'jobs')[0]['state'], 'FAILED')

    def test_dockerfile_mode_rejected_for_multi_image_manifest_at_queue(self):
        with self.assertRaises(Denied):
            self.pipeline.queue(self.principal, self.workload['id'], 'chart', 'queue-df', image_mode='dockerfile')

    def test_secret_ref_preflight_checked_against_every_declared_repository(self):
        self.policy.value['secrets'] = {'ref': {
            'tenant': 'alice', 'purpose': 'registry-bearer',
            'resources': ['example.invalid/research/amf'],  # missing smf/upf/mongodb/probe
            'file': str(self.root / 'secret.json'),
        }}
        (self.root / 'secret.json').write_text(json.dumps({'value': 'x' * 40}))
        with self.assertRaises(Denied):
            self.pipeline.queue(self.principal, self.workload['id'], 'chart', 'queue-secret', secret_ref='ref')

    def test_attempted_catalog_publication_without_digest_pinned_execution_blocked(self):
        result = self.run_job()
        self.policy.value['catalog_targets'] = {'alice': {
            'scope': 'DISPOSABLE_RESEARCH_PROJECT', 'enabled': True, 'project': 'research-alice',
            'host': 'https://osm.example', 'secret_ref': 'osm',
        }}
        with self.assertRaises(Denied):
            self.pipeline.request(self.principal, result['release_id'], 'catalog', 'request')

    def test_registry_gate_rejects_declaration_only_or_tampered_evidence(self):
        valid_evidence = {e['identity']: {'repository': e['repository'], 'digest': e['digest'],
                                           'platform': e['platform'], 'verification': 'REGISTRY_CONTENT_VERIFIED',
                                           'digest_pinned_execution': True} for e in MANIFEST['images']}
        base_release = {'schema_version': 2, 'images': MANIFEST['images'],
                         'digest_pinned_execution': True, 'image_verification': valid_evidence}
        # Sanity: complete, consistent, genuinely digest-pinned evidence passes.
        self.pipeline.registry_gate(self.principal, base_release)
        # A release that never achieved full digest-pinned execution is blocked
        # outright, even if some evidence exists -- the most common real case.
        not_pinned = copy.deepcopy(base_release)
        not_pinned['digest_pinned_execution'] = False
        with self.assertRaises(Denied):
            self.pipeline.registry_gate(self.principal, not_pinned)
        # Declaration-only/tampered: overall flag claims True but one image's
        # own record disagrees.
        tampered = copy.deepcopy(base_release)
        tampered['image_verification']['upf']['digest_pinned_execution'] = False
        with self.assertRaises(Denied):
            self.pipeline.registry_gate(self.principal, tampered)
        # Missing per-image evidence entirely for a declared image.
        incomplete = copy.deepcopy(base_release)
        del incomplete['image_verification']['mongodb']
        with self.assertRaises(Denied):
            self.pipeline.registry_gate(self.principal, incomplete)
        # Per-image evidence digest drifted from the release's own declared image.
        drifted = copy.deepcopy(base_release)
        drifted['image_verification']['smf']['digest'] = 'sha256:' + '7' * 64
        with self.assertRaises(Denied):
            self.pipeline.registry_gate(self.principal, drifted)


if __name__ == '__main__':
    unittest.main()
