"""Independent safety expectations. Network/lifecycle calls are always mocked."""
import contextlib
import copy
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'ran-selector')]
import osm_packages as packages
from scenario_registry import load_registry, select_scenarios, repository_path
from osm_catalog import Catalog, CatalogError
from osm_onboard import onboard
from local_safety import atomic_write


class HelmSemantics(unittest.TestCase):
    def resolve(self, base, profiles):
        files = {'Chart.yaml': b'apiVersion: v2\nname: fixture\nversion: 0.1.0\n',
                 'values.yaml': base}
        return packages.helm_effective_values(files, profiles)

    def test_independent_expected_values(self):
        cases = [
            (b'x: {a: 1, b: 2}\n', [b'x: {b: 3}\n'], {'x': {'a': 1, 'b': 3}}),
            (b'x: [1, 2]\n', [b'x: [3]\n'], {'x': [3]}),
            (b'enabled: true\n', [b'enabled: false\n'], {'enabled': False}),
            (b'x: {a: 1, b: 2}\n', [b'x: {a: null}\n'], {'x': {'b': 2}}),
            (b'x: {a: 1}\n', [b'x: null\n'], {}),
            (b'x: {a: 1, b: 2}\n', [b'x: null\n', b'x: {c: 3}\n'], {'x': {'a': 1, 'b': 2, 'c': 3}}),
            (b'x: old\n', [b'x: {a: 2}\n'], {'x': {'a': 2}}),
            (b'x: {a: 2}\n', [b'x: false\n'], {'x': False}),
            (b'{}\n', [b'x: on\ny: off\n'], {'x': True, 'true': False}),
            (b'{}\n', [b'1: value\n'], {'1': 'value'}),
            (b'x: {a: 1}\n', [b'x: {b: 2}\n', b'x: {a: 4}\n'], {'x': {'a': 4, 'b': 2}}),
        ]
        for base, profiles, expected in cases:
            with self.subTest(base=base, profiles=profiles):
                result = self.resolve(base, profiles)
                self.assertEqual(result, expected)
                # Baking must preserve resolved values on the next Helm load.
                self.assertEqual(self.resolve(packages.json_bytes(result), []), expected)

    def test_helmignore_is_applied_by_helm(self):
        files = {'Chart.yaml': b'apiVersion: v2\nname: fixture\nversion: 0.1.0\n',
                 'values.yaml': b'{}', '.helmignore': b'README.md\n',
                 'README.md': b'not packaged', 'templates/config.yaml': b'apiVersion: v1\n'}
        result = packages.helm_packaged_files(files)
        self.assertNotIn('README.md', result)
        self.assertIn('templates/config.yaml', result)

    def test_sensitive_unexpected_and_traversal_inputs_rejected(self):
        for name in ['.env', '.env.production', '.git/config', '__pycache__/x.pyc',
                     'credentials.yaml', 'templates/key.pem', 'values.yaml.swp',
                     'unexpected.txt', '../values.yaml', '/absolute']:
            with self.subTest(name=name), self.assertRaises(packages.PackageError):
                packages.safe_chart_inputs({name: b'synthetic'})
        with self.assertRaises(packages.PackageError):
            packages.archive_bytes('fixture', {'fixture/../escape': b'x'})


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'repo'
        self.output = Path(self.temporary.name) / 'output'
        shutil.copytree(ROOT / 'helm', self.root / 'helm')
        (self.root / 'config').mkdir()
        shutil.copy2(ROOT / 'config/scenarios.json', self.root / 'config/scenarios.json')
        registry = load_registry(self.root)
        self.scenarios = select_scenarios(registry, ['cran-srsran', 'fran'])
        for scenario in self.scenarios:
            legacy = scenario['reviewed_legacy']['path']
            shutil.copytree(ROOT / legacy, self.root / legacy)
            shutil.copy2(ROOT / (legacy + '.tar.gz'), self.root / (legacy + '.tar.gz'))

    def test_fresh_clone_preparation_and_independent_structure(self):
        self.assertFalse(self.output.exists())
        target = packages.prepare(self.root, self.output, self.scenarios[:1])
        self.assertEqual(packages.published_output(self.output), target)
        expected = packages.validated_snapshot(self.root, self.output, self.scenarios[0])
        vnfd = json.loads(expected['cran_srsran_knf/cran_srsran_knf_vnfd.yaml'])['vnfd']
        self.assertEqual(vnfd['id'], 'cran_srsran_knf')
        self.assertEqual(vnfd['product-name'], 'cran_srsran_knf')
        self.assertEqual(vnfd['kdu'], [{'name': 'cu', 'helm-chart': 'cu'}, {'name': 'du', 'helm-chart': 'du'}])
        nsd = json.loads(expected['cran_srsran_ns/cran_srsran_ns_nsd.yaml'])['nsd']['nsd'][0]
        self.assertEqual(nsd['vnfd-id'], ['cran_srsran_knf'])
        self.assertEqual(nsd['df'][0]['vnf-profile'][0]['id'], 'srsran')
        for kdu in ['cu', 'du']:
            self.assertTrue((target / f'cran_srsran_knf/helm-chart-v3s/{kdu}/Chart.yaml').is_file())
        provenance = json.loads(expected['cran-srsran.provenance.json'])
        self.assertEqual(len(provenance['registry_sha256']), 64)
        self.assertIn('osm_packages.py', provenance['generator']['implementation_sha256'])
        self.assertEqual(set(provenance['toolchain']), {'helm', 'python', 'zlib', 'pyyaml', 'compression', 'archive'})
        self.assertEqual(len(provenance['descriptor_sha256']), 2)

    def test_interruption_before_pointer_preserves_valid_old_snapshot_and_recovers(self):
        old = packages.prepare(self.root, self.output, self.scenarios[:1])
        source = self.root / 'helm/cran-srsran/cu/templates/cu.yaml'
        source.write_text(source.read_text() + '\n# reviewed source edit\n')
        with mock.patch.object(packages, 'atomic_write', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                packages.prepare(self.root, self.output, self.scenarios[:1])
        self.assertEqual(packages.published_output(self.output), old)
        with self.assertRaises(packages.PackageError):
            packages.validate_artifacts(self.root, self.output, self.scenarios[0])
        new = packages.prepare(self.root, self.output, self.scenarios[:1])
        self.assertNotEqual(old, new)
        packages.validated_snapshot(self.root, self.output, self.scenarios[0])
        self.assertTrue(old.exists())

    def test_concurrent_process_publications_keep_both_scenarios(self):
        queue = multiprocessing.Queue()
        def worker(scenario):
            try:
                packages.prepare(self.root, self.output, [scenario])
                queue.put(None)
            except Exception as error:
                queue.put(repr(error))
        processes = [multiprocessing.Process(target=worker, args=(scenario,)) for scenario in self.scenarios]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([queue.get(timeout=2) for _ in processes], [None, None])
        for scenario in self.scenarios:
            packages.validated_snapshot(self.root, self.output, scenario)
        queue.close()

    def test_upload_uses_validated_bytes_after_path_replacement(self):
        packages.prepare(self.root, self.output, self.scenarios[:1])
        scenario = self.scenarios[0]
        artifacts = packages.validated_snapshot(self.root, self.output, scenario)
        target = packages.published_output(self.output) / 'cran_srsran_knf.tar.gz'
        target.chmod(0o600)
        target.write_bytes(b'replaced')
        catalog = mock.Mock()
        onboard(catalog, [scenario], [artifacts])
        self.assertEqual(catalog.upload.call_args_list[0].args,
                         ('knf', 'cran_srsran_knf', artifacts['cran_srsran_knf.tar.gz']))
        self.assertNotEqual(catalog.upload.call_args_list[0].args[2], b'replaced')
        with self.assertRaises(packages.PackageError):
            packages.validated_snapshot(self.root, self.output, scenario)

    def test_symlink_paths_and_source_change_during_capture(self):
        link = self.root / 'helm/link'
        link.symlink_to(self.root / 'helm/cran-srsran/cu', target_is_directory=True)
        with self.assertRaises(ValueError):
            repository_path(self.root, 'helm/link')
        with self.assertRaises(ValueError):
            repository_path(self.root, '../escape')
        self.output.symlink_to(self.root / 'helm', target_is_directory=True)
        with self.assertRaises(packages.PackageError):
            packages.safe_output(self.root, self.output)


class CatalogTests(unittest.TestCase):
    def scenario(self):
        return {'knf_package': 'fixture_knf', 'nsd_package': 'fixture_ns'}

    def test_matching_catalog_is_required_for_both_archives(self):
        catalog = Catalog('https://example.invalid', 'synthetic-token')
        uuid = '11111111-1111-1111-1111-111111111111'
        answers = [b'- id: fixture_knf\n  product-name: fixture_knf\n  _id: ' + uuid.encode(), b'knf',
                   b'- id: fixture_ns\n  _id: ' + uuid.encode(), b'ns']
        with mock.patch.object(catalog, 'request', side_effect=answers) as request:
            self.assertEqual(catalog.verify(self.scenario(), {'fixture_knf.tar.gz': b'knf', 'fixture_ns.tar.gz': b'ns'}), {'knf': uuid, 'ns': uuid})
            self.assertEqual(request.call_count, 4)

    def test_missing_ambiguous_stale_or_unavailable_catalog_fails_closed(self):
        for listing in [b'[]', b'error: denied', b'- id: fixture_knf\n- id: fixture_knf']:
            catalog = Catalog('https://example.invalid')
            with mock.patch.object(catalog, 'request', return_value=listing), self.assertRaises(CatalogError):
                catalog.verify(self.scenario(), {})
        catalog = Catalog('https://example.invalid')
        listing = b'- id: fixture_knf\n  product-name: fixture_knf\n  _id: 11111111-1111-1111-1111-111111111111'
        with mock.patch.object(catalog, 'request', side_effect=[listing, b'stale']), self.assertRaises(CatalogError):
            catalog.verify(self.scenario(), {'fixture_knf.tar.gz': b'expected'})

    def test_catalog_lookup_error_does_not_create_package(self):
        catalog = Catalog('https://example.invalid')
        with mock.patch.object(catalog, 'request', side_effect=CatalogError('unavailable')) as request:
            with self.assertRaises(CatalogError):
                catalog.upload('knf', 'fixture_knf', b'bytes')
            self.assertEqual(request.call_count, 1)


class BackendSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('safety_backend', ROOT / 'ran-selector/backend.py')
        cls.backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.backend)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / 'active.yaml'
        self.state.write_text('osm:\n  active_instance_id: old-instance\n  active_scenario: cran-srsran\n')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        b = self.backend
        self.stack.enter_context(mock.patch.object(b, 'CONFIG_FILE', str(self.state)))
        self.stack.enter_context(mock.patch.object(b, 'validated_snapshot', return_value={}))
        self.stack.enter_context(mock.patch.object(b.osm_client, 'get_token', return_value='synthetic'))
        self.catalog = self.stack.enter_context(mock.patch.object(b, 'Catalog'))
        self.catalog.return_value.verify.return_value = {'ns': '11111111-1111-1111-1111-111111111111'}
        self.terminate = self.stack.enter_context(mock.patch.object(b.osm_client, 'terminate_ns', return_value='terminate-op'))
        self.wait = self.stack.enter_context(mock.patch.object(b.osm_client, 'wait_for_op', return_value='COMPLETED'))
        self.delete = self.stack.enter_context(mock.patch.object(b.osm_client, 'delete_ns_instance', return_value=True))
        self.absent = self.stack.enter_context(mock.patch.object(b.osm_client, 'ns_instance_absent', return_value=True))
        self.instantiate = self.stack.enter_context(mock.patch.object(b.osm_client, 'instantiate_ns', return_value=('new-instance', 'instantiate-op')))
        self.teardown = self.stack.enter_context(mock.patch.object(b, '_wait_pods_gone', return_value=True))
        self.commands = self.stack.enter_context(mock.patch.object(b, 'run', side_effect=AssertionError('unexpected shell')))
        self.kubectl = self.stack.enter_context(mock.patch.object(b, '_kubectl_json', side_effect=AssertionError('unexpected cluster access')))

    def post(self, key='cran-oai'):
        return self.backend.app.test_client().post('/api/deploy', json={'ran': key})

    def test_injection_payload_rejected_without_subprocess(self):
        with mock.patch.object(self.backend.subprocess, 'run', side_effect=AssertionError('payload executed')):
            for endpoint in ['logs', 'runtime', 'events']:
                response = self.backend.app.test_client().get('/api/' + endpoint + '/' + quote('x;printf audit_marker;#', safe=''))
                self.assertEqual(response.status_code, 400)
            self.kubectl.assert_not_called()
            self.commands.assert_not_called()

    def test_malformed_json_and_types_rejected_before_lifecycle(self):
        client = self.backend.app.test_client()
        for value in [None, [], 'cran-oai', {}, {'ran': []}, {'ran': {}}, {'ran': 1}]:
            self.assertEqual(client.post('/api/deploy', json=value).status_code, 400)
        self.assertEqual(client.post('/api/deploy', data='{broken', content_type='application/json').status_code, 400)
        self.terminate.assert_not_called()

    def test_catalog_preflight_precedes_every_destructive_call(self):
        self.catalog.return_value.verify.side_effect = CatalogError('missing or stale')
        self.assertEqual(self.post().status_code, 409)
        self.terminate.assert_not_called()
        self.delete.assert_not_called()
        self.instantiate.assert_not_called()

    def test_failed_termination_stops_replacement(self):
        self.wait.return_value = 'FAILED'
        self.assertEqual(self.post().status_code, 500)
        self.delete.assert_not_called()
        self.instantiate.assert_not_called()
        self.assertIn('old-instance', self.state.read_text())

    def test_failed_deletion_and_unconfirmed_teardown_stop_replacement(self):
        self.delete.return_value = False
        self.assertEqual(self.post().status_code, 500)
        self.instantiate.assert_not_called()
        self.delete.return_value = True
        self.absent.return_value = False
        self.assertEqual(self.post().status_code, 500)
        self.instantiate.assert_not_called()
        self.absent.return_value = True
        self.teardown.return_value = False
        self.assertEqual(self.post().status_code, 500)
        self.instantiate.assert_not_called()

    def test_state_is_atomic_and_failed_poll_retains_pending_identity(self):
        self.wait.side_effect = ['COMPLETED', 'FAILED']
        self.assertEqual(self.post().status_code, 500)
        config = packages.yaml.safe_load(self.state.read_text())
        self.assertEqual(config['active'], 'none')
        self.assertEqual(config['osm']['pending_instance']['id'], 'new-instance')
        self.assertNotIn('active_instance_id', config['osm'])
        self.wait.side_effect = None
        self.assertEqual(self.post().status_code, 500)
        self.assertEqual(self.instantiate.call_count, 1)

    def test_atomic_replace_failure_preserves_previous_state(self):
        before = self.state.read_bytes()
        with mock.patch('local_safety.os.replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                atomic_write(self.state, b'new state')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(list(self.state.parent.glob('.state-*')), [])

    def test_concurrent_requests_serialize_and_reread_state(self):
        counter = 0
        active = 0
        peak = 0
        def instantiate(*args, **kwargs):
            nonlocal counter, active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.05)
            counter += 1
            active -= 1
            return 'instance-' + str(counter), 'operation'
        self.instantiate.side_effect = instantiate
        results = []
        threads = [threading.Thread(target=lambda key=key: results.append(self.post(key).status_code))
                   for key in ['cran-oai', 'cloudran-srsran']]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results, [200, 200])
        self.assertEqual(peak, 1)
        self.assertEqual([call.args[0] for call in self.terminate.call_args_list], ['old-instance', 'instance-1'])
        self.assertEqual(packages.yaml.safe_load(self.state.read_text())['osm']['active_instance_id'], 'instance-2')
        self.commands.assert_not_called()

    def test_nonrunning_resources_and_query_failure_block_teardown(self):
        # Exercise the actual teardown predicate, not the patched polling wrapper.
        self.kubectl.side_effect = None
        self.kubectl.return_value = {'items': [{'kind': 'Pod', 'metadata': {'name': 'cran-srsran-cu-pending'}, 'status': {'phase': 'Pending'}}]}
        self.assertTrue(self.backend._pods_present(['cran-srsran-cu']))
        self.kubectl.return_value = None
        with self.assertRaises(RuntimeError):
            self.backend._pods_present(['cran-srsran-cu'])



class AdditionalAdversarialTests(unittest.TestCase):
    setUp = PublicationTests.setUp
    def test_orphaned_partial_directory_is_not_a_publication_and_retry_recovers(self):
        releases = self.output / 'releases'
        orphan = releases / '.pending-interrupted' / 'cran_srsran_knf'
        orphan.mkdir(parents=True)
        (orphan / 'partial.yaml').write_bytes(b'partial')
        with self.assertRaises(packages.PackageError):
            packages.validated_snapshot(self.root, self.output, self.scenarios[0])
        packages.prepare(self.root, self.output, self.scenarios[:1])
        artifacts = packages.validated_snapshot(self.root, self.output, self.scenarios[0])
        self.assertFalse(any('partial.yaml' in name for name in artifacts))
        self.assertTrue(orphan.exists())

    def test_source_tree_change_during_capture_is_rejected(self):
        original = packages.read_tree
        count = 0
        def changing(directory):
            nonlocal count
            result = original(directory)
            if str(directory).endswith('helm/cran-srsran/cu'):
                count += 1
                if count == 2:
                    return result | {'templates/added.yaml': b'changed'}
            return result
        with tempfile.TemporaryDirectory() as destination:
            with mock.patch.object(packages, 'read_tree', side_effect=changing):
                with self.assertRaisesRegex(packages.PackageError, 'tree changed'):
                    packages.source_snapshot(self.root, self.scenarios[:1], destination)

    def test_nested_input_symlink_and_inline_credentials_are_rejected(self):
        chart = self.root / 'helm/cran-srsran/cu'
        (chart / 'templates/link.yaml').symlink_to(chart / 'values.yaml')
        with self.assertRaises(packages.PackageError):
            packages.prepare(self.root, self.output, self.scenarios[:1])
        for files in [
                {'values.yaml': b'auth: {password: synthetic}\n'},
                {'values-other.yaml': b'apiKey: synthetic\n'},
                {'templates/secrets.yaml': b'synthetic'},
                {'templates/config.yaml': b'-----BEGIN PRIVATE KEY-----synthetic'},
                {'templates/.#config.yaml': b'synthetic'}]:
            with self.subTest(files=files), self.assertRaises(packages.PackageError):
                packages.safe_chart_inputs(files)


class ClientTransportTests(unittest.TestCase):
    def test_credentials_are_serialized_and_tls_verification_is_default(self):
        import ssl
        import osm_catalog
        password = 'quote: value\nsecond line'
        catalog = Catalog('https://example.invalid')
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'id: synthetic-token\n'
        with mock.patch.object(osm_catalog.urllib.request, 'build_opener') as build_opener:
            build_opener.return_value.open.return_value = response
            catalog.authenticate('admin', password)
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(packages.yaml.safe_load(request.data)['password'], password)
        self.assertEqual(build_opener.call_args.args[0]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertNotIn(password, request.full_url)

    def test_redirects_cannot_forward_credentials(self):
        from osm_catalog import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid"))

    def test_chart_metadata_cannot_escape_packaging_directory(self):
        for field in ['name', 'version']:
            metadata = {'apiVersion': 'v2', 'name': 'fixture', 'version': '1.0.0'}
            metadata[field] = '../../escape'
            files = {'Chart.yaml': packages.json_bytes(metadata), 'values.yaml': b'{}'}
            with self.subTest(field=field), mock.patch.object(packages, 'helm_run') as helm:
                with self.assertRaises(packages.PackageError):
                    packages.helm_packaged_files(files)
                helm.assert_not_called()

    def test_client_refreshes_expired_auth_once_without_shell(self):
        import osm_client
        from osm_catalog import AuthorizationError
        with mock.patch.object(osm_client, 'get_token', side_effect=['old', 'new']) as token, mock.patch.object(osm_client, 'Catalog') as factory:
            factory.return_value.request.side_effect = [AuthorizationError('expired'), b'valid']
            self.assertEqual(osm_client._request('GET', '/test'), b'valid')
            self.assertEqual(token.call_args_list, [mock.call(), mock.call(force=True)])
            self.assertEqual(factory.return_value.token, 'new')

    def test_missing_default_credentials_fail_before_network(self):
        import osm_client
        with mock.patch.object(osm_client, 'OSM_PASS', None), mock.patch.dict(osm_client._token_cache, {'token': None}), mock.patch.object(osm_client, 'Catalog') as factory:
            with self.assertRaises(CatalogError):
                osm_client.get_token()
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
