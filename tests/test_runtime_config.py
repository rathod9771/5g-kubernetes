"""Portable configuration contracts with synthetic data. No live services used."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'ran-selector'))
import runtime_config as rc
import runtime_render as rr
from osm_catalog import Catalog, CatalogError

PROJECT = '22222222-2222-2222-2222-222222222222'
VIM = '33333333-3333-3333-3333-333333333333'
OTHER = '44444444-4444-4444-4444-444444444444'

class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'checkout with spaces ; literal $chars'
        (self.root / 'config').mkdir(parents=True)
        shutil.copytree(ROOT / 'helm', self.root / 'helm')
        shutil.copy2(ROOT / 'config/scenarios.json', self.root / 'config/scenarios.json')
        self.home = self.base / 'home alice'
        self.home.mkdir()

    def load(self, env=None, **kwargs):
        return rc.load_config(self.root, environ=env or {}, home=self.home, **kwargs)

    def write(self, text, mode=0o600):
        path = self.root / 'config/global.env'
        path.write_text(text)
        path.chmod(mode)
        return path

    @staticmethod
    def network(interface='eth-test', address='192.0.2.10'):
        def query(args):
            if 'address' in args:
                return [{'ifname': 'lo', 'addr_info': [{'family': 'inet', 'local': '127.0.0.1', 'prefixlen': 8}]},
                        {'ifname': interface, 'addr_info': [{'family': 'inet', 'local': address, 'prefixlen': 24}]}]
            return [{'dev': interface, 'prefsrc': address}]
        return query

    def test_two_users_homes_and_relocations(self):
        first = self.load()
        other = self.base / 'home bob'
        other.mkdir()
        alternate = self.base / 'different repo'
        shutil.copytree(self.root, alternate)
        second = rc.load_config(alternate, environ={}, home=other)
        self.assertEqual(first['KUBECONFIG_PATH'], str(self.home / '.kube/config'))
        self.assertEqual(second['KUBECONFIG_PATH'], str(other / '.kube/config'))
        self.assertEqual(second['REPO_ROOT'], str(alternate))
        self.assertEqual(second['ACTIVE_STATE_PATH'], str(alternate / '.runtime/active-ran.yaml'))

    def test_route_selects_matching_address_not_first_interface(self):
        for interface, address in [('eth-test', '192.0.2.10'), ('wlan-test', '198.51.100.7')]:
            cfg = self.load(network=True, query=self.network(interface, address))
            self.assertEqual((cfg['HOST_IP'], cfg['HOST_INTERFACE']), (address, interface))
            self.assertEqual(cfg['OSM_HOST'], f'https://gui.{address}.nip.io:30843')

    def test_configured_address_selects_its_interface(self):
        cfg = self.load({'HOST_IP': '192.0.2.10'}, network=True, query=self.network())
        self.assertEqual(cfg['HOST_INTERFACE'], 'eth-test')

    def test_missing_mismatched_and_ambiguous_interfaces_fail(self):
        for env in [{'HOST_INTERFACE': 'absent'}, {'HOST_IP': '198.51.100.7'}, {'HOST_IP': '192.0.2.10', 'HOST_INTERFACE': 'lo'}]:
            with self.subTest(env=env), self.assertRaises(rc.ConfigError):
                self.load(env, network=True, query=self.network())
        def ambiguous(args):
            if 'address' in args:
                return [{'ifname': n, 'addr_info': [{'family': 'inet', 'local': '192.0.2.10', 'prefixlen': 24}]} for n in ['a', 'b']]
            return []
        with self.assertRaises(rc.ConfigError):
            self.load({'HOST_IP': '192.0.2.10'}, network=True, query=ambiguous)

    def test_no_route_no_reference_fallback(self):
        with self.assertRaises(rc.ConfigError):
            self.load(network=True, query=lambda args: [])
        cfg = self.load()
        for field in ['HOST_IP', 'HOST_INTERFACE', 'OSM_HOST', 'OSM_VIM_ACCOUNT_ID', 'OSM_PROJECT_ID', 'OSM_PROJECT_NAMESPACE']:
            self.assertEqual(cfg[field], '')

    def test_literal_paths_metacharacters_never_execute(self):
        sentinel = self.base / 'SHOULD_NOT_EXIST'
        value = str(self.base / ('literal $(touch ' + sentinel.name + ') ; & `whoami`'))
        self.write('KUBECONFIG_PATH="' + value + '"\n')
        with mock.patch.object(rc.subprocess, 'run', side_effect=AssertionError('shell executed')):
            cfg = self.load()
        self.assertEqual(cfg['KUBECONFIG_PATH'], value)
        self.assertFalse(sentinel.exists())

    def test_only_explicit_home_expansion(self):
        self.write('KUBECONFIG_PATH="$HOME/custom config"\n')
        self.assertEqual(self.load()['KUBECONFIG_PATH'], str(self.home / 'custom config'))

    def test_precedence_file_then_environment(self):
        self.write('DASHBOARD_PORT=8092\nOSM_USER=file-user\n')
        cfg = self.load({'OSM_USER': 'environment-user'})
        self.assertEqual(cfg['OSM_USER'], 'environment-user')
        self.assertEqual(cfg['DASHBOARD_PORT'], '8092')

    def test_malformed_and_duplicate_keys_fail_without_echoing_values(self):
        for text in ['not assignment', 'UNKNOWN=synthetic-secret', 'OSM_USER=x\nOSM_USER=y', 'OSM_PASSWORD="synthetic-secret']:
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(rc.ConfigError) as error:
                    self.load()
                self.assertNotIn('synthetic-secret', str(error.exception))

    def test_secrets_must_be_private_and_absent_secrets_fail(self):
        self.write('OSM_PASSWORD=synthetic-secret\n', 0o644)
        with self.assertRaisesRegex(rc.ConfigError, '0600'):
            self.load()
        self.write('OSM_HOST=https://example.invalid\nOSM_USER=fixture\nOSM_PROJECT=project\n')
        with self.assertRaisesRegex(rc.ConfigError, 'OSM_PASSWORD'):
            self.load(require=('osm',))
        with self.assertRaisesRegex(rc.ConfigError, 'GRAFANA_ADMIN_PASSWORD'):
            self.load(require=('install',))

    def test_invalid_ip_cidr_ports_profile_uuid_and_url(self):
        cases = {'HOST_IP': 'bad', 'POD_CIDR': '10.1.2.3/16', 'DASHBOARD_PORT': '65536',
                 'GRAFANA_NODEPORT': '1234', 'DEPLOYMENT_PROFILE': 'unknown', 'OSM_PROJECT_ID': 'not-uuid',
                 'OSM_HOST': 'https://user:synthetic-secret@example.invalid', 'OSM_PROJECT_NAMESPACE': 'x;id',
                 'LAYER3_BLER_THRESHOLD': 'nan', 'LAYER3_FAILOVER_SCENARIO': 'unknown',
                 'FLEXRIC_ADDRESS': 'bad', 'BENCH_IPERF_PORT': 'invalid'}
        for key, value in cases.items():
            with self.subTest(key=key), self.assertRaises(rc.ConfigError):
                self.load({key: value})

    def test_overlapping_ranges_and_duplicate_ports(self):
        for env in [{'SERVICE_CIDR': '10.244.0.0/16'}, {'DASHBOARD_PORT': '30843'}]:
            with self.assertRaisesRegex(rc.ConfigError, '[Cc]onflict'):
                self.load(env)

    def test_missing_paths_symlinks_traversal_and_wrong_checkout(self):
        with self.assertRaisesRegex(rc.ConfigError, 'existing'):
            self.load(require=('kubernetes',))
        link = self.base / 'linked'
        link.symlink_to(self.home, target_is_directory=True)
        for env in [{'KUBECONFIG_PATH': str(link / 'config')}, {'RUNTIME_DIR': 'helm/private'},
                    {'REPO_ROOT': str(self.home)}, {'KUBECONFIG_PATH': '../escape'}]:
            with self.subTest(env=env), self.assertRaises(rc.ConfigError):
                self.load(env)

    def test_profile_semantics_do_not_silently_override_charts(self):
        for env in [{'MCC': '001'}, {'DNN': 'alternate'}, {'UE_CIDR': '10.50.0.0/16'}]:
            with self.assertRaisesRegex(rc.ConfigError, 'profile'):
                self.load(env)

    def test_watcher_requires_explicit_ready_target(self):
        for env in [{}, {'LAYER3_FAILOVER_SCENARIO': 'hcran-oai'}]:
            with self.assertRaisesRegex(rc.ConfigError, 'explicitly configured ready'):
                self.load(env, require=('watcher',))
        self.assertEqual(self.load({'LAYER3_FAILOVER_SCENARIO': 'cran-oai'}, require=('watcher',))['LAYER3_FAILOVER_SCENARIO'], 'cran-oai')

    def test_rf_dependency_validation(self):
        with self.assertRaisesRegex(rc.ConfigError, 'USRP requires'):
            self.load({'DEPLOYMENT_PROFILE': 'usrp'}, require=('rf',))

    def test_private_values_no_secret_in_path_or_service_unit(self):
        secret = 'synthetic-secret-with-quote: " value'
        cfg = self.load({'RANCHER_BOOTSTRAP_PASSWORD': secret, 'GRAFANA_ADMIN_PASSWORD': secret})
        import yaml
        for kind in ['rancher', 'grafana']:
            path = rr.private_values(kind, cfg)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(secret, str(path))
            data = yaml.safe_load(path.read_text())
            actual = data['bootstrapPassword'] if kind == 'rancher' else data['grafana']['adminPassword']
            self.assertEqual(actual, secret)
        unit = rr.render_service('dashboard', cfg, user='alice')
        self.assertNotIn(secret, unit)
        self.assertIn('User=alice', unit)
        self.assertIn(':/usr/bin/python3 ', unit)
        self.assertIn(str(self.root) + '/scripts/service_entry.py', unit)

    def test_render_monitoring_manifest_with_independent_expected_fields(self):
        import yaml
        cfg = self.load({'OSM_PROJECT_NAMESPACE': 'new-project', 'PROMETHEUS_NODEPORT': '31090', 'HOST_IP': '192.0.2.10'})
        source = ROOT / 'monitoring/ran-exporter/rbac.yaml'
        docs = list(yaml.safe_load_all(rr.render_manifest(source, cfg)))
        self.assertEqual([d['metadata']['namespace'] for d in docs], ['new-project'] * 3)
        self.assertEqual(docs[-1]['subjects'][0]['namespace'], 'new-project')
        service = yaml.safe_load(rr.render_manifest(ROOT / 'monitoring/prometheus-nodeport.yaml', cfg))
        self.assertEqual(service['spec']['ports'][0]['nodePort'], 31090)

    def test_unresolved_placeholder_fails(self):
        with self.assertRaises(rc.ConfigError):
            rr.render_manifest(ROOT / 'monitoring/ran-exporter/deployment.yaml', self.load())

    def test_safe_empty_state_initialization_and_no_overwrite(self):
        import yaml
        cfg = self.load()
        path = Path(cfg['ACTIVE_STATE_PATH'])
        context = {'project_id': PROJECT, 'vim_id': VIM, 'namespace': PROJECT, 'cluster_uid': OTHER, 'namespace_uid': VIM,
                   'osm_host': 'https://osm.invalid', 'repo_root': str(self.root),
                   'workload_kubeconfig': cfg['OSM_KUBECONFIG_PATH'], 'deployment_profile': 'rfsim'}
        with self.assertRaises(rc.ConfigError):
            rr.initialize_state(path, {'project_id': PROJECT}, [])
        self.assertFalse(path.exists())
        for listing in [None, {}, [{'id': OTHER}]]:
            with self.assertRaises(rc.ConfigError):
                rr.initialize_state(path, context, listing)
        rr.initialize_state(path, context, [])
        state = yaml.safe_load(path.read_text())
        self.assertEqual(state, {'active': 'none', 'osm': {}, 'context': context})
        before = path.read_bytes()
        with self.assertRaises(rc.ConfigError):
            rr.initialize_state(path, context, [])
        self.assertEqual(path.read_bytes(), before)

    def test_runtime_state_symlink_and_world_writable_directory_fail(self):
        state = self.root / '.runtime'
        state.mkdir(mode=0o700)
        (state / 'active-ran.yaml').symlink_to(self.root / 'config/scenarios.json')
        with self.assertRaisesRegex(rc.ConfigError, 'symlink'):
            self.load()
        (state / 'active-ran.yaml').unlink()
        state.chmod(0o777)
        with self.assertRaisesRegex(rc.ConfigError, 'write permission'):
            self.load()

    def test_service_paths_dollar_and_percent_are_context_escaped(self):
        self.assertEqual(rr.systemd_quote('/literal $name/%name'), '"/literal $name/%%name"')
        self.assertEqual(rr.systemd_quote('/literal $name/%name', executable=True), '"/literal $name/%%name"')
        cfg = self.load({'RANCHER_PORT': '8444'})
        self.assertIn('rancher-forward', rr.render_service('rancher-forward', cfg, user='alice'))
        self.assertEqual(rr.service_configuration('rancher-forward', cfg)['RANCHER_PORT'], '8444')

    def test_renderer_rejects_arbitrary_configuration_files(self):
        with self.assertRaisesRegex(rc.ConfigError, 'monitoring templates'):
            rr.render_manifest(self.root / 'config/global.env', self.load())

    def test_example_contract_matches_loader_defaults(self):
        values = rc.read_env(ROOT / 'config/global.env.example')
        self.assertEqual(set(values), set(rc.DEFAULTS))
        self.assertEqual(values, rc.DEFAULTS)

    def test_shell_loader_literal_exports_and_errors(self):
        # Relocated helper scripts with private synthetic configuration; ip is stubbed.
        (self.root / 'scripts').mkdir()
        for name in ['common.sh', 'runtime_config.py', 'scenario_registry.py', 'local_safety.py']:
            shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        binary = self.base / 'bin'
        binary.mkdir()
        ip = binary / 'ip'
        ip.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(' +
                      repr(self.network()(['ip','address'])) + ' if "address" in sys.argv else ' +
                      repr(self.network()(['ip','route'])) + '))\n')
        ip.chmod(0o700)
        literal = str(self.base / 'kube $(touch SHOULD_NOT_EXIST); `id`')
        self.write('KUBECONFIG_PATH="' + literal + '"\n')
        env = {**os.environ, 'REPO_ROOT': str(self.root), 'HOME': str(self.home),
               'PATH': str(binary) + ':' + os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE': '1'}
        for key in rc.DEFAULTS:
            if key not in ['REPO_ROOT']:
                env.pop(key, None)
        command = 'source "$REPO_ROOT/scripts/common.sh"; load_config; printf "%s" "$KUBECONFIG_PATH"'
        result = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, literal)
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())
        self.write('HOST_IP=malformed\n')
        result = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

class DiscoveryTests(unittest.TestCase):
    def cfg(self, **overrides):
        return {**rc.DEFAULTS, 'OSM_PROJECT': 'expected', 'OSM_VIM_NAME': 'expected-vim', **overrides}

    def catalog(self, projects=None, vims=None):
        catalog = mock.Mock()
        projects = [{'_id': PROJECT, 'name': 'expected'}] if projects is None else projects
        vims = [{'_id': VIM, 'name': 'expected-vim'}] if vims is None else vims
        catalog.request.side_effect = [json.dumps(projects).encode(), json.dumps(vims).encode()]
        return catalog

    @staticmethod
    def namespaces(name=PROJECT):
        return lambda: {'items': [{'metadata': {'name': name, 'uid': VIM}},
                                  {'metadata': {'name': 'kube-system', 'uid': OTHER}}]}

    def test_unique_project_vim_and_namespace(self):
        context = rc.discover_context(self.cfg(), self.catalog(), self.namespaces())
        self.assertEqual(context['project_id'], PROJECT)
        self.assertEqual(context['vim_id'], VIM)
        self.assertEqual(context['namespace'], PROJECT)
        self.assertEqual(context['cluster_uid'], OTHER)
        self.assertEqual(context['namespace_uid'], VIM)

    def test_missing_ambiguous_projects_and_vims(self):
        for kind in ['project', 'VIM']:
            name = 'expected' if kind == 'project' else 'expected-vim'
            for entries in [[], [{'_id': PROJECT, 'name': 'unexpected'}],
                            [{'_id': PROJECT, 'name': name}, {'_id': OTHER, 'name': name}]]:
                with self.subTest(kind=kind, entries=entries):
                    catalog = self.catalog(projects=entries) if kind == 'project' else self.catalog(vims=entries)
                    with self.assertRaisesRegex(rc.ConfigError, kind):
                        rc.discover_context(self.cfg(), catalog, self.namespaces())

    def test_explicit_ids_disambiguate_and_wrong_ids_fail(self):
        projects = [{'_id': PROJECT, 'name': 'expected'}, {'_id': OTHER, 'name': 'expected'}]
        vims = [{'_id': VIM, 'name': 'expected-vim'}, {'_id': OTHER, 'name': 'expected-vim'}]
        cfg = self.cfg(OSM_PROJECT_ID=PROJECT, OSM_VIM_ACCOUNT_ID=VIM)
        self.assertEqual(rc.discover_context(cfg, self.catalog(projects, vims), self.namespaces())['vim_id'], VIM)
        with self.assertRaises(rc.ConfigError):
            rc.discover_context(self.cfg(OSM_PROJECT_ID=VIM), self.catalog(projects, vims), self.namespaces())

    def test_endpoint_is_bound_to_runtime_context(self):
        context = rc.discover_context(self.cfg(OSM_HOST='https://new.invalid'), self.catalog(), self.namespaces())
        self.assertEqual(context['osm_host'], 'https://new.invalid')

    def test_explicit_osm_kubernetes_cluster_is_verified(self):
        catalog = mock.Mock()
        catalog.request.side_effect = [json.dumps([{'_id': PROJECT, 'name': 'expected'}]).encode(),
                                       json.dumps([{'_id': OTHER, 'name': 'cluster', 'credentials': {'apiVersion': 'v1', 'clusters': []}}]).encode(),
                                       json.dumps([{'_id': VIM, 'name': 'expected-vim'}]).encode()]
        with mock.patch.object(rc, 'command_json', return_value={'metadata': {'uid': OTHER}}):
            context = rc.discover_context(self.cfg(OSM_K8S_CLUSTER_ID=OTHER), catalog, self.namespaces())
        self.assertEqual(context['k8s_cluster_id'], OTHER)
        self.assertEqual(catalog.request.call_args_list[1].args, ('GET', '/admin/v1/k8sclusters'))
        for listing in [[], [{'_id': OTHER}, {'_id': OTHER}]]:
            catalog.request.side_effect = [json.dumps([{'_id': PROJECT, 'name': 'expected'}]).encode(), json.dumps(listing).encode()]
            with self.assertRaisesRegex(rc.ConfigError, 'Kubernetes cluster'):
                rc.discover_context(self.cfg(OSM_K8S_CLUSTER_ID=OTHER), catalog, self.namespaces())

    def test_no_arbitrary_namespace_selection(self):
        for listing in [{'items': []}, {'items': [{'metadata': {'name': 'default'}}]}, {'items': []}, []]:
            with self.assertRaises(rc.ConfigError):
                rc.discover_context(self.cfg(), self.catalog(), lambda: listing)
        ctx = rc.discover_context(self.cfg(OSM_PROJECT_NAMESPACE='custom'), self.catalog(), self.namespaces('custom'))
        self.assertEqual(ctx['namespace'], 'custom')

    def test_malformed_identity_and_listing_fail(self):
        for entries in [{'error': 'denied'}, [None], [{'_id': 'bad', 'name': 'expected'}]]:
            with self.assertRaises(rc.ConfigError):
                rc.unique_identity(entries, 'expected', '', 'project')

    def test_no_vim_name_or_id_fails(self):
        with self.assertRaises(rc.ConfigError):
            rc.discover_context(self.cfg(OSM_VIM_NAME=''), self.catalog(), self.namespaces())

    def test_onboarding_checks_project_without_vim_or_namespace(self):
        catalog = self.catalog()
        self.assertEqual(rc.discover_context(self.cfg(), catalog, require_vim=False), {'project_id': PROJECT})
        self.assertEqual(catalog.request.call_count, 1)

    def test_service_discovery_is_unique_and_structured(self):
        service = {'metadata': {'name': 'ric', 'namespace': 'project'},
                   'spec': {'clusterIP': '192.0.2.44', 'ports': [{'name': 'e2', 'port': 36421}]}}
        self.assertEqual(rc.discover_service({'items': [service]}, 'ric', 'project', 'e2'), ('192.0.2.44', 36421))
        for items in [[], [service, service]]:
            with self.assertRaises(rc.ConfigError):
                rc.discover_service({'items': items}, 'ric', 'project', 'e2')

    def test_runtime_session_freezes_lifecycle_configuration(self):
        import osm_client
        with osm_client.runtime_session({'OSM_HOST': 'https://frozen.invalid'}):
            with mock.patch.object(osm_client, 'load_config', side_effect=AssertionError('configuration changed')):
                self.assertEqual(osm_client.runtime_config()['OSM_HOST'], 'https://frozen.invalid')

    def test_context_failure_before_destructive_operations(self):
        spec = importlib.util.spec_from_file_location('runtime_backend', ROOT / 'ran-selector/backend.py')
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backend, 'CONFIG_FILE', str(Path(tmp) / 'state.yaml')), mock.patch.object(backend, 'validated_snapshot', return_value={}), mock.patch.object(backend, 'runtime_preflight', side_effect=rc.ConfigError('ambiguous VIM')), mock.patch.object(backend.osm_client, 'terminate_ns') as terminate:
            response = backend.app.test_client().post('/api/deploy', json={'ran': 'cran-oai'})
            self.assertEqual(response.status_code, 409)
            terminate.assert_not_called()

    def test_unbound_existing_state_cannot_terminate(self):
        import yaml
        spec = importlib.util.spec_from_file_location('runtime_backend', ROOT / 'ran-selector/backend.py')
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.yaml'
            state.write_text(yaml.safe_dump({'osm': {'active_instance_id': OTHER, 'active_scenario': 'cran-srsran'}}))
            catalog = mock.Mock()
            catalog.verify.return_value = {'ns': PROJECT}
            context = {'project_id': PROJECT, 'vim_id': VIM, 'namespace': PROJECT}
            with mock.patch.object(backend, 'CONFIG_FILE', str(state)), mock.patch.object(backend, 'validated_snapshot', return_value={}), mock.patch.object(backend, 'runtime_preflight', return_value=({'DEPLOYMENT_PROFILE': 'rfsim'}, context, catalog)), mock.patch.object(backend.osm_client, 'terminate_ns') as terminate:
                response = backend.app.test_client().post('/api/deploy', json={'ran': 'cran-oai'})
                self.assertEqual(response.status_code, 500)
                terminate.assert_not_called()

class SourceSafetyTests(unittest.TestCase):
    def test_runtime_config_and_state_are_ignored(self):
        paths = ['config/global.env', '.runtime/active-ran.yaml', '.runtime/rancher-private-values.yaml', 'build/osm-packages/example.tar.gz']
        result = subprocess.run(['git', 'check-ignore', '--no-index', *paths], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(set(result.stdout.splitlines()), set(paths))

    def test_active_code_has_no_old_reference_constants(self):
        for name in ['ran-selector/backend.py', 'ran-selector/osm_client.py', 'ran-selector/index.html', 'scripts/common.sh', 'scripts/osm_onboard.py', 'layer3-autonomous/watcher.py', 'config/global.env.example']:
            text = (ROOT / name).read_text()
            for old in ['/home/rclab', '~/5g-kubernetes', '172.30.18.32', 'enp3s0', 'c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c', 'b0481f03-f5eb-47a2-9a20-bd72430b3b13']:
                self.assertNotIn(old, text, name)

    def test_runtime_files_do_not_enter_package_or_provenance(self):
        import osm_packages as packages
        from scenario_registry import load_registry, select_scenarios
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            (root / 'config').mkdir(parents=True)
            shutil.copytree(ROOT / 'helm', root / 'helm')
            shutil.copy2(ROOT / 'config/scenarios.json', root / 'config/scenarios.json')
            scenario = select_scenarios(load_registry(root), ['fran'])[0]
            legacy = scenario['reviewed_legacy']['path']
            shutil.copytree(ROOT / legacy, root / legacy)
            shutil.copy2(ROOT / (legacy + '.tar.gz'), root / (legacy + '.tar.gz'))
            output = Path(tmp) / 'output'
            packages.prepare(root, output, [scenario])
            before = packages.validated_snapshot(root, output, scenario)
            (root / 'config/global.env').write_text('OSM_PASSWORD=synthetic-runtime-secret\n')
            (root / '.runtime').mkdir()
            (root / '.runtime/active-ran.yaml').write_text('active_instance_id: ' + OTHER)
            packages.prepare(root, output, [scenario])
            after = packages.validated_snapshot(root, output, scenario)
            self.assertEqual(before, after)
            self.assertFalse(any('global.env' in path or '.runtime' in path for path in after))
            self.assertNotIn(b'synthetic-runtime-secret', after['fran.provenance.json'])
            self.assertNotIn(OTHER.encode(), after['fran.provenance.json'])

if __name__ == '__main__':
    unittest.main()
