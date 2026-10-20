"""Offline tests for free5gc_validation.py -- the operator-only workflow
that acquires ONLY charts/free5gc from the real, pinned free5gc-helm
upstream commit and runs it through package.prepare()'s real, unweakened
gate. These tests never touch the real network or real free5GC bytes:
each one builds a small, synthetic, local Git fixture chart and feeds it
through validate() via the SAME offline-transport-mocking pattern already
established in test_telecom_execution.py (ExecutionTests.
test_github_fetch_entry_path_with_offline_transport) -- a 'github' source
kind whose `fetch` step is intercepted and satisfied from local Git
objects instead of the network.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from telecom import free5gc_validation as fv
from telecom.execution import Policy
from telecom import execution

URL = 'https://github.com/research/free5gc-validation-fixture'


class Free5gcValidationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '--template=', str(self.repo)], check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo)] + list(args), check=True, capture_output=True).stdout

    def commit(self, message='fixture'):
        self.git('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD').decode().strip()

    def write(self, relative, content):
        path = self.repo / 'charts/free5gc' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content)
        else:
            path.write_bytes(content)

    def repo_untouched_marker(self):
        """A snapshot of the fixture repo's own HEAD + working tree status,
        to confirm validate() never writes back into the 'upstream'
        checkout it reads from."""
        head = self.git('rev-parse', 'HEAD').decode().strip()
        status = self.git('status', '--short').decode()
        return head, status

    def run_validate(self, sha, **kwargs):
        original = execution.command

        def transport(argv, **kw):
            if 'fetch' in argv:
                destination = Path(argv[argv.index('-C') + 1])
                shutil.copytree(self.repo / '.git/objects', destination / 'objects', dirs_exist_ok=True)
                return b''
            return original(argv, **kw)

        policy = Policy({'github_repositories': [URL]})
        with mock.patch.object(execution, 'command', side_effect=transport):
            with tempfile.TemporaryDirectory(prefix='validate-ws-') as workspace:
                return fv.validate(workspace, upstream_url=URL, upstream_commit=sha,
                                    chart_subtree='charts/free5gc', policy=policy, **kwargs)

    # ---- fixture builders -------------------------------------------------

    def build_clean_chart(self):
        """No dependencies, a digest-pinned image, a fully-compliant
        securityContext, no Secret/hook/cert content -- everything this
        pipeline's real checks look for is already clean."""
        self.write('Chart.yaml', 'apiVersion: v2\nname: free5gc\nversion: 1.1.7\n')
        self.write('values.yaml', 'replicaCount: 1\n')
        self.write('templates/deployment.yaml', (
            'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: clean\n'
            'spec:\n  replicas: 1\n  selector: {matchLabels: {app: clean}}\n'
            '  template:\n    metadata: {labels: {app: clean}}\n    spec:\n'
            '      automountServiceAccountToken: false\n      containers:\n'
            '      - name: c\n        image: busybox@sha256:' + 'a' * 64 + '\n'
            '        securityContext:\n          runAsNonRoot: true\n'
            '          allowPrivilegeEscalation: false\n          capabilities: {drop: [ALL]}\n'
            '        resources: {limits: {cpu: "100m", memory: "64Mi"}}\n'
        ))
        self.git('add', '-A')
        return self.commit('clean chart')

    def build_blocked_chart(self, dependency_has_version):
        """Models the real free5GC finding this round surfaced: a
        dependency declared WITHOUT a `version:` key hits
        subcharts.py's _dependencies() parser before the 'missing
        reviewed inventory' check is ever reached. Also includes a
        Secret, a mutable-tag image, a missing securityContext, a hook
        Pod, and an embedded cert/key pair -- one chart exercising every
        requirement-7 category at once."""
        dependency = 'name: sub\n' + ('    version: 1.0.0\n' if dependency_has_version else '')
        self.write('Chart.yaml', 'apiVersion: v2\nname: free5gc\nversion: 1.1.7\ndependencies:\n  - ' + dependency)
        self.write('values.yaml', 'replicaCount: 1\n')
        self.write('cert/root.key', 'FIXTURE-NOT-A-REAL-KEY\n')
        self.write('cert/root.pem', 'FIXTURE-NOT-A-REAL-CERT\n')
        self.write('templates/secret.yaml', (
            'apiVersion: v1\nkind: Secret\nmetadata:\n  name: tls\ntype: Opaque\ndata:\n  x: eQ==\n'
        ))
        self.write('templates/deployment.yaml', (
            'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: nf\n'
            'spec:\n  replicas: 1\n  selector: {matchLabels: {app: nf}}\n'
            '  template:\n    metadata: {labels: {app: nf}}\n    spec:\n'
            '      automountServiceAccountToken: false\n      containers:\n'
            '      - name: c\n        image: example/nf:v1.2.3\n'
            '        resources: {limits: {cpu: "100m", memory: "64Mi"}}\n'
        ))
        self.write('templates/hook.yaml', (
            'apiVersion: v1\nkind: Pod\nmetadata:\n  name: test-connection\n'
            '  annotations: {helm.sh/hook: test}\nspec:\n  restartPolicy: Never\n  containers:\n'
            '  - name: test\n    image: busybox\n    command: ["true"]\n'
        ))
        self.write('charts/sub/Chart.yaml', 'apiVersion: v2\nname: sub\nversion: 1.0.0\n')
        self.write('charts/sub/templates/noop.yaml', 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: noop\ndata: {}\n')
        self.git('add', '-A')
        return self.commit('blocked chart')

    # ---- positive ----------------------------------------------------------

    def test_clean_chart_passes_prepare_but_deployment_eligible_still_false(self):
        """POSITIVE PATH, and the critical fail-closed invariant: even a
        chart with NO blocking errors at all must never become
        deployment_eligible. This field is unconditional, not merely
        'true unless something fails'."""
        sha = self.build_clean_chart()
        report = self.run_validate(sha, images=[{'kind': 'network-function', 'role': 'x-fixture',
                                                  'identity': 'fixture', 'repository': 'busybox',
                                                  'tag_pin': 'latest', 'digest': 'sha256:' + 'a' * 64}])
        self.assertEqual(report['blocking_errors'], [])
        self.assertEqual(report['dependency_validation_results']['result'], 'PASSED')
        self.assertEqual(report['helm_lint_template_results']['helm_lint'], 'PASSED')
        self.assertIs(report['deployment_eligible'], False)

    def test_source_verification_fields_present_and_correct(self):
        sha = self.build_clean_chart()
        report = self.run_validate(sha)
        verification = report['source_verification']
        self.assertEqual(verification['repository'], URL)
        self.assertEqual(verification['verified_commit'], sha)
        self.assertEqual(verification['chart_path'], 'charts/free5gc')
        self.assertEqual(verification['acquisition_authority'], 'GITHUB_FETCH_VERIFIED')

    def test_upstream_checkout_never_modified(self):
        sha = self.build_clean_chart()
        before = self.repo_untouched_marker()
        self.run_validate(sha)
        after = self.repo_untouched_marker()
        self.assertEqual(before, after)

    # ---- negative: the real, precise blocker --------------------------------

    def test_version_less_dependency_reproduces_the_real_blocker(self):
        """A dependency entry with no `version:` key and no
        VERSIONLESS_DEPENDENCIES approval fails closed inside
        subcharts.py's _dependencies() parser -- package.prepare() must
        stop here, not silently skip past it, and the report must say so
        precisely."""
        sha = self.build_blocked_chart(dependency_has_version=False)
        report = self.run_validate(sha)
        self.assertEqual(len(report['blocking_errors']), 1)
        self.assertEqual(report['blocking_errors'][0]['stage'], 'package.prepare')
        self.assertIn('Unreviewed versionless Helm dependency: sub', report['blocking_errors'][0]['message'])
        self.assertEqual(report['dependency_validation_results']['result'], 'FAILED')
        self.assertIs(report['deployment_eligible'], False)

    def test_versioned_but_unreviewed_dependency_blocks_on_missing_inventory(self):
        """A dependency entry that DOES parse (has a version) still
        blocks -- on the DIFFERENT, later 'missing reviewed inventory'
        check -- never silently accepted just because parsing succeeded."""
        sha = self.build_blocked_chart(dependency_has_version=True)
        report = self.run_validate(sha)
        self.assertEqual(len(report['blocking_errors']), 1)
        self.assertIn('explicit reviewed inventory', report['blocking_errors'][0]['message'])
        self.assertIs(report['deployment_eligible'], False)

    # ---- adversarial: the static survey must not paper over anything -------

    def test_static_survey_surfaces_every_requirement_category(self):
        sha = self.build_blocked_chart(dependency_has_version=True)
        report = self.run_validate(sha)
        self.assertIn('cert/root.key', report['embedded_certificate_key_files'])
        self.assertIn('cert/root.pem', report['embedded_certificate_key_files'])
        self.assertIn('tls', report['unsupported_resource_kinds']['secret_resources'])
        self.assertIn('example/nf:v1.2.3', report['image_inventory_findings']['mutable_tag_references'])
        self.assertGreaterEqual(report['pod_security_findings']['missing_runAsNonRoot'], 1)
        self.assertTrue(any(h[2] == 'test' for h in report['helm_lint_template_results']['helm_hooks']))
        self.assertIn('sub', report['vendored_subchart_directories_without_reviewed_inventory'])

    def test_mongodb_registry_entries_never_fire_for_an_unrelated_commit(self):
        """The real, reviewed free5GC MongoDB registry entries
        (chart_adapters.py) are keyed to one exact (name, version,
        commit) identity. An unrelated fixture commit -- even one that
        happens to share the chart name/version -- must never trigger
        them: this is the same exact-identity matching every other
        adaptation in this pipeline relies on, not a looser lookup."""
        self.write('Chart.yaml', (
            'apiVersion: v2\nname: free5gc\nversion: 1.1.7\n'
            'dependencies:\n  - name: mongodb\n    condition: deployMongoDb\n'
        ))
        self.write('values.yaml', 'x: 1\n')
        self.git('add', '-A')
        sha = self.commit('same name/version as real free5gc, unrelated commit')
        report = self.run_validate(sha)
        self.assertEqual(report['adaptations_applied']['files_modified_or_removed'], [])
        self.assertEqual(report['adaptations_applied']['dependency_removals'], [])
        # The untouched top-level mongodb dependency is still correctly
        # surfaced by the static survey regardless.
        self.assertEqual(report['top_level_mongodb_dependency'], [{'name': 'mongodb', 'condition': 'deployMongoDb'}])


class Free5gcValidationExposureTests(unittest.TestCase):
    """Requirement 11: this capability must never be reachable through
    any untrusted researcher/pipeline API surface."""

    def test_not_imported_by_api_or_pipeline(self):
        for module_name in ('telecom.api', 'telecom.pipeline'):
            source = (ROOT / 'scripts' / module_name.replace('.', '/')).with_suffix('.py').read_text()
            self.assertNotIn('free5gc_validation', source)

    def test_module_defines_no_flask_blueprint_or_route(self):
        source = (ROOT / 'scripts/telecom/free5gc_validation.py').read_text()
        self.assertNotIn('Blueprint', source)
        self.assertNotIn('.route(', source)
        self.assertNotIn('flask', source.lower())


if __name__ == '__main__':
    unittest.main()
