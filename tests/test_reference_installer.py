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
