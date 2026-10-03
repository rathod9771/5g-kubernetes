"""Static installer contracts; no host, Kubernetes or OSM lifecycle calls."""
import json
import io
import subprocess
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from installer.reference import versions, osm_values, api_ingress, adapt_osm_chart
from installer.private_input import private_archive, import_decision, restore_command
from installer.install import Runner, Installer, resume_config, preflight
from runtime_config import ConfigError
from osm_packages import safe_chart_inputs, sha256, PackageError
from scenario_registry import load_registry, select_scenarios, dashboard_scenarios


class ReferenceInstallerTests(unittest.TestCase):
    def test_version_lock(self):
        lock = versions()
        expected = {'kubernetes':'1.29.15','flannel':'0.28.9','longhorn':'1.11.3',
                    'cert_manager':'1.14.5','rancher':'2.15.1','istio':'1.30.5','osm':'19.0.0'}
        for key, value in expected.items():
            self.assertEqual(lock[key], value)
        for digest in lock['images'].values():
            self.assertRegex(digest, r'^sha256:[0-9a-f]{64}$')

    def test_osm_values_have_pinned_images_and_no_private_inputs(self):
        v = osm_values({'OSM_BASE_DOMAIN':'example.invalid'}, versions())
        self.assertIn('@sha256:', v['nbi']['image']['tag'])
        self.assertTrue(v['mongodb']['image']['digest'].startswith('sha256:'))
        self.assertFalse(v['global']['gitops']['enabled'])
        self.assertNotIn('password', json.dumps(v).lower())

    def test_api_ingress(self):
        d = api_ingress({'OSM_HOST':'https://gui.example.invalid:30843','OSM_NAMESPACE':'osm'})
        self.assertEqual(d['spec']['rules'][0]['host'],'gui.example.invalid')
        p=d['spec']['rules'][0]['http']['paths'][0]
        self.assertEqual((p['path'],p['backend']['service']['name']),('/osm','nbi'))
        self.assertEqual(d['spec']['tls'][0]['secretName'],'ngui-cert')

    def test_certificate_patch_exact_match_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'templates/nbi';p.mkdir(parents=True)
            f=p/'nbi-certificate.yaml';f.write_text('  usages:\n    - "client auth"\n')
            self.assertEqual(adapt_osm_chart(t),['templates/nbi/nbi-certificate.yaml'])
            self.assertIn('server auth',f.read_text())
            f.write_text('different upstream content')
            with self.assertRaises(ValueError): adapt_osm_chart(t)

    def test_private_input_required(self):
        with self.assertRaisesRegex(ConfigError,'PRIVATE RUNTIME INPUT REQUIRED'): private_archive('')

    def test_private_input_permissions_symlink_and_format(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'private.gz';p.write_bytes(b'\x1f\x8btest');p.chmod(0o600)
            self.assertEqual(private_archive(p),b'\x1f\x8btest')
            p.chmod(0o644)
            with self.assertRaises(ConfigError): private_archive(p)
            p.chmod(0o600);q=Path(t)/'link';q.symlink_to(p)
            with self.assertRaises(ConfigError): private_archive(q)
            p.write_bytes(b'plain')
            with self.assertRaises(ConfigError): private_archive(p)

    def test_restore_is_subscriber_only_no_drop_no_authentication_argv(self):
        c=restore_command('/portable/config','fresh-namespace')
        self.assertIn('--nsInclude=open5gs.subscribers',c)
        self.assertNotIn('--drop',c)
        self.assertNotIn('--password',c)
        self.assertIn('-i',c)

    def test_private_restore_receipt_and_partial_failure(self):
        binding={'pvc_uid':'synthetic'}
        self.assertEqual(import_decision(None,binding,'digest',0,1),'restore')
        with self.assertRaises(ConfigError): import_decision(None,binding,'digest',1,0)
        record={'binding':binding,'input_sha256':'digest','complete':True}
        self.assertEqual(import_decision(record,binding,'digest',1,0),'verify')
        with self.assertRaises(ConfigError): import_decision(record,binding,'other',1,0)
        with self.assertRaises(ConfigError): import_decision(record,binding,'digest',0,0)

    def test_core_registry_does_not_become_dashboard_scenario(self):
        registry=load_registry()
        self.assertEqual(len(select_scenarios(registry,ready=True)),7)
        self.assertNotIn('open5gs',dashboard_scenarios(registry))
        core=select_scenarios(registry,['open5gs'])[0]
        self.assertEqual(core['charts'][0]['source'],'osm-packages/open5gs_knf/helm-chart-v3s/open5gs')
        self.assertEqual(len(core['charts'][0]['reviewed_inputs']),314)

    def test_reviewed_comment_exception_does_not_allow_key(self):
        files={'values.yaml':b'  ## -----BEGIN RSA PRIVATE KEY-----\n'}
        safe_chart_inputs(files,{k:sha256(v) for k,v in files.items()})
        files={'values.yaml':b'-----BEGIN RSA PRIVATE KEY-----\nactualmaterial'}
        with self.assertRaises(PackageError): safe_chart_inputs(files,{k:sha256(v) for k,v in files.items()})
        files={'values.yaml':b'  ## -----BEGIN RSA PRIVATE KEY-----\n'}
        with self.assertRaises(PackageError): safe_chart_inputs(files)

    def test_runner_failure_does_not_leak_stdin_stdout_stderr(self):
        with mock.patch('subprocess.run') as run:
            run.return_value.returncode=1
            run.return_value.stderr=b'private sentinel'
            with self.assertRaises(ConfigError) as error:
                Runner().run(['program','argument'],data=b'private sentinel')
            self.assertNotIn('sentinel',str(error.exception))
            self.assertEqual(run.call_args.args[0],['program','argument'])

    def test_no_source_installation_at_import(self):
        self.assertTrue(callable(Installer.run))
        source=(ROOT/'install.sh').read_text()
        self.assertIn('set -euo pipefail',source)
        self.assertNotIn('|| true',source)

    def test_installer_dependency_order_without_lifecycle_execution(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t})
            names=['kubernetes','infrastructure','osm','core','subscribers','baseline','monitoring','services']
            calls=[]
            installer.snapshot=lambda: None
            for name in names:
                setattr(installer,name,lambda name=name:calls.append(name))
            with mock.patch('builtins.print'): installer.run()
            self.assertEqual(calls,names)

    def test_interrupted_core_creation_is_not_retried_or_adopted(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t,'OSM_PROJECT_NAMESPACE':'synthetic'})
            installer.context={'cluster_uid':'synthetic'}
            installer.packages=mock.Mock(return_value=[{'ns':'synthetic'}])
            installer.entries=mock.Mock(return_value=[{'name':'core-persistent','id':'synthetic'}])
            installer.catalog=mock.Mock()
            (Path(t)/'core-instance.json').write_text(json.dumps({'context':installer.context,'id':'synthetic','stage':'created'}))
            with self.assertRaisesRegex(ConfigError,'Interrupted core'): installer.core()
            installer.catalog.request.assert_not_called()

    def test_resume_does_not_replace_explicit_inputs(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/'installer-config.json').touch()
            fields=['OSM_PROJECT_ID','OSM_VIM_ACCOUNT_ID','OSM_K8S_CLUSTER_ID','OSM_PROJECT_NAMESPACE','OSM_CA_CERT_PATH']
            cfg={'RUNTIME_DIR':t,**dict.fromkeys(fields,''),'OSM_PROJECT_ID':'explicit','OSM_PASSWORD':'synthetic'}
            with mock.patch('installer.install.read_snapshot',return_value=dict.fromkeys(fields,'discovered')):
                result=resume_config(cfg)
            self.assertEqual(result['OSM_PROJECT_ID'],'explicit')
            self.assertEqual(result['OSM_CA_CERT_PATH'],'discovered')
            self.assertEqual(result['OSM_PASSWORD'],'synthetic')

    def test_osm_dependency_lock(self):
        expected={'mysql':'9.12.3','mongodb':'18.1.9','kafka':'30.1.6','grafana':'7.3.0','prometheus':'25.11.0','airflow':'1.9.0'}
        dependencies=versions()['osm_dependencies']
        self.assertEqual({d['name']:d['version'] for d in dependencies},expected)
        for dependency in dependencies:
            self.assertRegex(dependency['sha256'],r'^[0-9a-f]{64}$')

    def test_osm_api_readiness_retries_transport_but_not_wrong_credentials(self):
        from osm_catalog import CatalogError, AuthorizationError
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t,'OSM_USER':'synthetic'})
            installer.catalog=mock.Mock()
            installer.catalog.authenticate.side_effect=[CatalogError('not ready'),None]
            with mock.patch('installer.install.time.sleep'): installer.authenticate_ready('synthetic')
            self.assertEqual(installer.catalog.authenticate.call_count,2)
            installer.catalog.authenticate.reset_mock()
            installer.catalog.authenticate.side_effect=AuthorizationError('wrong credentials')
            with self.assertRaises(AuthorizationError): installer.authenticate_ready('synthetic')
            self.assertEqual(installer.catalog.authenticate.call_count,1)

    def test_private_input_gate_precedes_all_preflight_commands(self):
        with mock.patch('subprocess.run') as run:
            with self.assertRaisesRegex(ConfigError,'PRIVATE RUNTIME INPUT REQUIRED'):
                preflight({'SUBSCRIBER_DATABASE_INPUT':''})
            run.assert_not_called()

    def test_osm_hook_compatible_install_still_waits_for_workloads(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t})
            installer.apply=mock.Mock()
            installer.helm=mock.Mock(return_value=mock.Mock(stdout=b'[]'))
            installer.kjson=mock.Mock(side_effect=[{'items':[{'metadata':{'name':'nbi'}}]},{'items':[]},{'items':[]}])
            installer.kubectl=mock.Mock()
            installer.release('osm','osm','pinned-chart','19.0.0',wait=False)
            command=installer.helm.call_args_list[1].args
            self.assertNotIn('--wait',command)
            self.assertIn('19.0.0',command)
            installer.kubectl.assert_called_once_with('rollout','status','deployment/nbi','-n','osm','--timeout=600s')

    def test_missing_core_workloads_cannot_be_ready(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t})
            installer.kjson=mock.Mock(return_value={'items':[]})
            installer.kubectl=mock.Mock()
            with self.assertRaisesRegex(ConfigError,'missing required chart workloads') as error:
                installer.core_workload_readiness('synthetic')
            for nf in ['amf','smf','upf']:
                self.assertIn(nf,str(error.exception))
            installer.kubectl.assert_not_called()

    def test_scaled_to_zero_core_cannot_be_ready(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t})
            deployments=[{'metadata':{'name':nf,'labels':{'app.kubernetes.io/name':nf}},'spec':{'replicas':0 if nf=='amf' else 1}} for nf in ['amf','smf','upf','ausf','bsf','nrf','nssf','pcf','udm','udr','webui']]
            installer.kjson=mock.Mock(return_value={'items':deployments})
            installer.kubectl=mock.Mock()
            with self.assertRaisesRegex(ConfigError,'scaled to zero'):installer.core_workload_readiness('synthetic')
            installer.kubectl.assert_not_called()

    def test_unreachable_existing_kubeconfig_never_initializes_cluster(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'kubeconfig';p.touch()
            installer=Installer({'RUNTIME_DIR':t,'KUBECONFIG_PATH':str(p)})
            installer.kubectl=mock.Mock(return_value=mock.Mock(returncode=1))
            installer.runner=mock.Mock()
            with self.assertRaisesRegex(ConfigError,'do not overwrite'):installer.kubernetes()
            installer.runner.run.assert_not_called()

    def test_active_dashboard_environment_is_not_reinstalled(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t,'DASHBOARD_URL':'http://example.invalid'})
            installer.runner=mock.Mock()
            installer.runner.run.return_value.returncode=0
            with mock.patch('installer.install.check_installed_service'), mock.patch('urllib.request.urlopen',return_value=io.BytesIO(b'{}')):
                installer.services()
            self.assertEqual({c.args[0][0] for c in installer.runner.run.call_args_list},{'systemctl'})

    @unittest.skipIf(os.geteuid()==0,'installer requires invoking user')
    def test_fresh_shell_bootstrap_order_without_real_commands(self):
        with tempfile.TemporaryDirectory(prefix='fresh path ') as t:
            root=Path(t);(root/'scripts/installer').mkdir(parents=True);(root/'bin').mkdir()
            (root/'install.sh').write_bytes((ROOT/'install.sh').read_bytes())
            (root/'scripts/common.sh').write_text('load_config() { echo config >> "$EVENT_LOG"; }\n')
            (root/'scripts/installer/host.sh').write_text('echo host >> "$EVENT_LOG"\n')
            for name,body in {
                'python3':'if [ "$1" = -c ]; then exit 1; fi\nprintf "python:%s\\n" "$*" >> "$EVENT_LOG"\n',
                'sudo':'printf "sudo:%s\\n" "$*" >> "$EVENT_LOG"\n',
                'ip':'exit 0\n'}.items():
                p=root/'bin'/name;p.write_text('#!/bin/sh\n'+body);p.chmod(0o700)
            log=root/'events';env={**os.environ,'PATH':str(root/'bin')+':'+os.environ['PATH'],'EVENT_LOG':str(log)}
            result=subprocess.run(['bash',str(root/'install.sh')],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            events=log.read_text().splitlines()
            self.assertEqual(events[:3],['sudo:apt-get update','sudo:apt-get install -y python3 python3-yaml iproute2','config'])
            self.assertTrue(events[3].endswith('--preflight'))
            self.assertEqual(events[4],'host')
            self.assertTrue(events[5].endswith('/scripts/installer/install.py'))

    def test_transient_helm_fetch_retries_then_succeeds(self):
        failure=subprocess.CompletedProcess(['helm'],1,b'',b'read: connection reset by peer')
        success=subprocess.CompletedProcess(['helm'],0,b'chart',b'')
        with mock.patch('subprocess.run',side_effect=[failure,success]) as run, mock.patch('installer.install.time.sleep') as sleep:
            result=Runner().run(['helm','pull','ingress-nginx'],remote_fetch=True,context='ingress-nginx chart ingress-nginx/4.10.1')
        self.assertEqual(result.stdout,b'chart');self.assertEqual(run.call_count,2);sleep.assert_called_once_with(2)

    def test_helm_fetch_exhaustion_reports_context_without_secrets(self):
        failure=subprocess.CompletedProcess(['helm'],1,b'PRIVATE_SUBSCRIBER_SENTINEL',b'password=PRIVATE_SENTINEL read: connection reset by peer')
        runner=Runner();runner.stage='infrastructure'
        with mock.patch('subprocess.run',return_value=failure) as run, mock.patch('installer.install.time.sleep') as sleep:
            with self.assertRaises(ConfigError) as error:
                runner.run(['helm','pull','ingress-nginx'],remote_fetch=True,context='ingress-nginx chart ingress-nginx/4.10.1')
        self.assertEqual(run.call_count,4);self.assertEqual([c.args[0] for c in sleep.call_args_list],[2,4,8])
        message=str(error.exception)
        for public in ['infrastructure','ingress-nginx','4.10.1','4 attempt','connection reset by peer']:self.assertIn(public,message)
        self.assertNotIn('PRIVATE',message);self.assertNotIn('password=',message)

    def test_deterministic_failure_is_not_retried(self):
        failure=subprocess.CompletedProcess(['helm'],1,b'',b'chart not found; private-token=PRIVATE_SENTINEL')
        with mock.patch('subprocess.run',return_value=failure) as run, mock.patch('installer.install.time.sleep') as sleep:
            with self.assertRaises(ConfigError) as error:
                Runner().run(['helm','pull','ingress-nginx'],remote_fetch=True,context='ingress-nginx/4.10.1')
        self.assertEqual(run.call_count,1);sleep.assert_not_called();self.assertNotIn('PRIVATE',str(error.exception))

    def test_helm_install_and_lint_never_retry_network_looking_errors(self):
        failure=subprocess.CompletedProcess(['helm'],1,b'',b'connection reset by peer')
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t,'KUBECONFIG_PATH':'synthetic'})
            for command in [('upgrade','--install','ingress-nginx','local-chart'),('lint','local-chart')]:
                with mock.patch('subprocess.run',return_value=failure) as run, mock.patch('installer.install.time.sleep') as sleep:
                    with self.assertRaises(ConfigError):installer.helm(*command)
                self.assertEqual(run.call_count,1);sleep.assert_not_called()

    def test_generic_runner_failure_names_stage_and_hides_output(self):
        runner=Runner();runner.stage='Open5GS'
        failure=subprocess.CompletedProcess(['kubectl'],1,b'PRIVATE_SENTINEL',b'PRIVATE_SENTINEL')
        with mock.patch('subprocess.run',return_value=failure):
            with self.assertRaises(ConfigError) as error:runner.run(['kubectl','exec'],data=b'PRIVATE_SENTINEL')
        self.assertIn('Open5GS: kubectl',str(error.exception));self.assertNotIn('PRIVATE',str(error.exception))

    def test_release_prefetches_exact_chart_before_single_install(self):
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t})
            installer.apply=mock.Mock();installer.kjson=mock.Mock(return_value={'items':[]})
            commands=[]
            def helm(*args,**kwargs):
                commands.append((args,kwargs))
                if args[0]=='pull':
                    destination=args[args.index('--destination')+1]
                    (Path(destination)/'ingress-nginx-4.10.1.tgz').write_bytes(b'synthetic archive')
                return subprocess.CompletedProcess(['helm'],0,b'[]',b'')
            installer.helm=helm
            installer.release('ingress-nginx','ingress-nginx','ingress-nginx','4.10.1',repo='https://charts.invalid')
            self.assertEqual([c[0][0] for c in commands],['list','pull','upgrade'])
            self.assertIn('4.10.1',commands[1][0]);self.assertIn('4.10.1',commands[2][0])
            self.assertTrue(commands[2][0][3].endswith('ingress-nginx-4.10.1.tgz'))
            self.assertNotIn('--repo',commands[2][0])
            self.assertIn('ingress-nginx/4.10.1',commands[2][1]['context'])

    def test_fetch_timeout_is_bounded_and_partial_output_is_private(self):
        timeout=subprocess.TimeoutExpired(['helm'],120,output=b'PRIVATE_SENTINEL',stderr=b'PRIVATE_SENTINEL')
        with mock.patch('subprocess.run',side_effect=timeout) as run, mock.patch('installer.install.time.sleep'):
            with self.assertRaises(ConfigError) as error:
                Runner().run(['helm','pull','ingress-nginx'],remote_fetch=True,context='ingress-nginx/4.10.1')
        self.assertEqual(run.call_count,4);self.assertIn('4 attempt',str(error.exception));self.assertNotIn('PRIVATE',str(error.exception))
        self.assertEqual(run.call_args.kwargs['timeout'],120)

    def test_repository_and_dependency_network_operations_retry(self):
        failure=subprocess.CompletedProcess(['helm'],1,b'',b'i/o timeout')
        success=subprocess.CompletedProcess(['helm'],0,b'',b'')
        with tempfile.TemporaryDirectory() as t:
            installer=Installer({'RUNTIME_DIR':t,'KUBECONFIG_PATH':'synthetic'})
            for command in [('repo','add','test','https://charts.invalid'),('repo','update'),('dependency','build','local-chart')]:
                with mock.patch('subprocess.run',side_effect=[failure,success]) as run, mock.patch('installer.install.time.sleep'):
                    installer.helm(*command,context='OSM chart 19.0.0 dependencies')
                self.assertEqual(run.call_count,2)


class OsmAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.remote = self.base / 'remote'
        self.remote.mkdir()
        self.git(self.remote, 'init')
        self.git(self.remote, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.remote, 'config', 'user.name', 'Fixture')
        (self.remote / 'source.txt').write_text('pinned source')
        self.git(self.remote, 'add', 'source.txt')
        self.git(self.remote, 'commit', '-m', 'fixture')
        self.commit = self.git(self.remote, 'rev-parse', 'HEAD').stdout.decode().strip()
        self.installer = Installer({'RUNTIME_DIR': str(self.base / 'runtime')})
        self.installer.lock = dict(self.installer.lock, osm_source_url=str(self.remote), osm_source_commit=self.commit)
        self.source = self.installer.directory / 'osm-devops'

    def git(self, path, *args):
        return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True)

    def assert_clean(self):
        self.assertFalse(list(self.installer.directory.glob('.osm-acquisition-*')))

    def test_clone_long_timeout_and_transactional_promote(self):
        original = self.installer.runner.run
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ['git', 'clone']:
                self.assertFalse(self.source.exists())
                self.assertEqual(kwargs['timeout'], 300)
                self.assertNotEqual(Path(args[-1]), self.source)
            return original(args, **kwargs)
        self.installer.runner.run = run
        self.assertEqual(self.installer.acquire_osm_source(), self.source)
        self.assertEqual((self.source / 'source.txt').read_text(), 'pinned source')
        self.assert_clean()

    def test_clone_timeout_and_transient_failure_then_success(self):
        original = self.installer.runner.run
        attempts = []
        def run(args, **kwargs):
            if args[:2] == ['git', 'clone']:
                attempts.append(args)
                self.assertFalse(Path(args[-1]).exists())
                if len(attempts) < 3:
                    (Path(args[-1]) / '.git').mkdir(parents=True)
                    raise ConfigError('OSM source clone: command timeout' if len(attempts) == 1 else 'OSM source clone: connection reset by peer')
            return original(args, **kwargs)
        self.installer.runner.run = run
        with mock.patch('installer.install.time.sleep') as sleep:
            self.installer.acquire_osm_source()
        self.assertEqual(len(attempts), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])
        self.assert_clean()

    def test_interrupted_git_only_cache_is_rebuilt(self):
        self.source.parent.mkdir()
        subprocess.run(['git', 'clone', '--no-checkout', str(self.remote), str(self.source)], check=True, capture_output=True)
        self.assertEqual({p.name for p in self.source.iterdir()}, {'.git'})
        self.installer.acquire_osm_source()
        self.assertTrue((self.source / 'source.txt').exists())
        self.assert_clean()

    def test_valid_cached_checkout_reused_without_clone_fetch_checkout(self):
        self.installer.acquire_osm_source()
        original = self.installer.runner.run
        with mock.patch.object(self.installer.runner, 'run', wraps=original) as run:
            self.installer.acquire_osm_source()
        for call in run.call_args_list:
            self.assertFalse(set(call.args[0]) & {'clone', 'fetch', 'checkout'})

    def test_missing_pinned_commit_fetches_in_temporary_copy(self):
        self.installer.acquire_osm_source()
        (self.remote / 'source.txt').write_text('new pinned source')
        self.git(self.remote, 'commit', '-am', 'second')
        commit = self.git(self.remote, 'rev-parse', 'HEAD').stdout.decode().strip()
        self.installer.lock['osm_source_commit'] = commit
        original = self.installer.runner.run
        with mock.patch.object(self.installer.runner, 'run', wraps=original) as run:
            self.installer.acquire_osm_source()
        fetches = [c for c in run.call_args_list if 'fetch' in c.args[0]]
        self.assertEqual(len(fetches), 1)
        self.assertEqual(fetches[0].kwargs['fetch_timeout'], 300)
        self.assertNotEqual(fetches[0].args[0][2], str(self.source))
        self.assertEqual((self.source / 'source.txt').read_text(), 'new pinned source')
        self.assert_clean()

    def test_retry_exhaustion_and_cleanup(self):
        original = self.installer.runner.run
        attempts = []
        def run(args, **kwargs):
            if args[:2] == ['git', 'clone']:
                attempts.append(args)
                (Path(args[-1]) / '.git').mkdir(parents=True)
                raise ConfigError('OSM source clone: command timeout (private output suppressed)')
            return original(args, **kwargs)
        self.installer.runner.run = run
        with mock.patch('installer.install.time.sleep') as sleep:
            with self.assertRaisesRegex(ConfigError, 'attempt 4/4'):
                self.installer.acquire_osm_source()
        self.assertEqual(len(attempts), 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8])
        self.assertFalse(self.source.exists())
        self.assert_clean()

    def test_invalid_origin_and_modified_source_preserved(self):
        self.installer.acquire_osm_source()
        (self.source / 'source.txt').write_text('user source')
        with self.assertRaisesRegex(ConfigError, 'local modifications'):
            self.installer.acquire_osm_source()
        self.assertEqual((self.source / 'source.txt').read_text(), 'user source')
        self.git(self.source, 'remote', 'set-url', 'origin', 'https://example.invalid/private')
        with self.assertRaisesRegex(ConfigError, 'identity mismatch'):
            self.installer.acquire_osm_source()

    def test_promotion_failure_rolls_back_existing_cache(self):
        self.installer.acquire_osm_source()
        (self.remote / 'source.txt').write_text('second')
        self.git(self.remote, 'commit', '-am', 'second')
        self.installer.lock['osm_source_commit'] = self.git(self.remote, 'rev-parse', 'HEAD').stdout.decode().strip()
        replace = os.replace
        def fail(candidate, destination):
            if Path(candidate).name == 'checkout':
                raise OSError('synthetic promotion failure')
            return replace(candidate, destination)
        with mock.patch('installer.install.os.replace', side_effect=fail):
            with self.assertRaises(OSError): self.installer.acquire_osm_source()
        self.assertEqual((self.source / 'source.txt').read_text(), 'pinned source')
        self.assert_clean()

    def test_git_fetch_timeout_retry_has_safe_diagnostics(self):
        failure = subprocess.TimeoutExpired(['git'], 300, output=b'PRIVATE', stderr=b'PRIVATE')
        with mock.patch('subprocess.run', side_effect=failure) as run, mock.patch('installer.install.time.sleep'):
            with self.assertRaises(ConfigError) as error:
                Runner().run(['git', 'fetch'], timeout=300, fetch_timeout=300, remote_fetch=True, context='OSM remote Git fetch')
        self.assertEqual(run.call_count, 4)
        self.assertEqual(run.call_args.kwargs['timeout'], 300)
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_unavailable_commit_preserves_previous_cache(self):
        self.installer.acquire_osm_source()
        self.installer.lock['osm_source_commit'] = '0' * 40
        with self.assertRaisesRegex(ConfigError, 'pinned commit'):
            self.installer.acquire_osm_source()
        self.assertEqual((self.source / 'source.txt').read_text(), 'pinned source')
        self.assert_clean()

    def test_checkout_failure_never_promotes_partial_source(self):
        original = self.installer.runner.run
        def run(args, **kwargs):
            if 'checkout' in args[3:]:
                raise ConfigError('OSM source checkout failure (pinned commit): unclassified failure')
            return original(args, **kwargs)
        self.installer.runner.run = run
        with self.assertRaisesRegex(ConfigError, 'checkout failure'):
            self.installer.acquire_osm_source()
        self.assertFalse(self.source.exists())
        self.assert_clean()

    def test_deterministic_clone_failure_does_not_retry_or_leak(self):
        failure = subprocess.CompletedProcess(['git'], 128, b'PRIVATE', b'authentication unauthorized PRIVATE')
        with mock.patch('subprocess.run', return_value=failure) as run, mock.patch('installer.install.time.sleep') as sleep:
            with self.assertRaises(ConfigError) as error:
                self.installer.acquire_osm_source()
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertIn('remote Git', str(error.exception))
        self.assert_clean()


class OsmReferencePatchTests(unittest.TestCase):
    setUp = OsmAcquisitionTests.setUp
    git = OsmAcquisitionTests.git

    def fixture(self):
        import hashlib
        from installer.reference import apply_osm_source_patch
        source = self.installer.acquire_osm_source()
        (source / 'source.txt').write_text('reference patched source')
        patch = self.base / 'reference.patch'
        patch.write_bytes(self.git(source, 'diff', '--binary', '--full-index').stdout)
        self.git(source, 'add', 'source.txt')
        tree = self.git(source, 'write-tree').stdout.decode().strip()
        self.git(source, 'reset', '--hard', self.commit)
        lock = dict(self.installer.lock, osm_source_patch={
            'path': str(patch), 'sha256': hashlib.sha256(patch.read_bytes()).hexdigest(), 'tree': tree})
        return source, patch, lock, apply_osm_source_patch

    def test_clean_apply_and_already_applied_are_identical(self):
        source, patch, lock, apply = self.fixture()
        for _ in range(2):
            self.assertEqual(apply(source, lock, Runner()), lock['osm_source_patch']['tree'])
            self.assertEqual((source / 'source.txt').read_text(), 'reference patched source')
        self.assertEqual(self.git(source, 'rev-parse', 'HEAD').stdout.decode().strip(), self.commit)

    def test_wrong_base_rejected_before_patch(self):
        source, patch, lock, apply = self.fixture()
        lock['osm_source_commit'] = '0' * 40
        with self.assertRaisesRegex(ConfigError, 'wrong public base'): apply(source, lock, Runner())
        self.assertEqual((source / 'source.txt').read_text(), 'pinned source')

    def test_patch_checksum_failure_is_safe(self):
        source, patch, lock, apply = self.fixture()
        patch.write_bytes(b'PRIVATE_INVALID_PATCH_SENTINEL')
        with self.assertRaisesRegex(ConfigError, 'checksum mismatch') as error:
            apply(source, lock, Runner())
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertEqual((source / 'source.txt').read_text(), 'pinned source')

    def test_patch_failure_does_not_change_source(self):
        import hashlib
        source, patch, lock, apply = self.fixture()
        patch.write_bytes(patch.read_bytes().replace(b'-pinned source', b'-absent original'))
        lock['osm_source_patch']['sha256'] = hashlib.sha256(patch.read_bytes()).hexdigest()
        with self.assertRaises(ConfigError): apply(source, lock, Runner())
        self.assertEqual((source / 'source.txt').read_text(), 'pinned source')
        self.assertFalse(self.git(source, 'status', '--porcelain').stdout)

    def test_unexpected_tree_or_working_content_rejected(self):
        source, patch, lock, apply = self.fixture()
        lock['osm_source_patch']['tree'] = '0' * 40
        with self.assertRaisesRegex(ConfigError, 'tree mismatch'): apply(source, lock, Runner())
        (source / 'source.txt').write_text('user change')
        with self.assertRaisesRegex(ConfigError, 'unexpected working-tree'): apply(source, lock, Runner())

    def test_real_patch_provenance_and_public_base_are_locked(self):
        import hashlib
        lock = versions()
        self.assertEqual(lock['osm_source_commit'], '019473f83210197b833c03e2efdda0fb290b9eed')
        spec = lock['osm_source_patch']
        self.assertEqual(spec['tree'], '42ddcbf52f27bcc967e6b72496349fffb73c97b3')
        self.assertEqual(hashlib.sha256((ROOT / spec['path']).read_bytes()).hexdigest(), spec['sha256'])
        self.assertEqual(spec['reference_commit'], '3804ad93d2626137ca5295a7ecdae29d301a6274')

    def test_ignored_private_files_are_rejected_without_printing_content(self):
        source, patch, lock, apply = self.fixture()
        (source / '.git/info/exclude').write_text('private-input\n')
        (source / 'private-input').write_text('PRIVATE_SENTINEL')
        with self.assertRaisesRegex(ConfigError, 'unexpected working-tree') as error:
            apply(source, lock, Runner())
        self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
