"""Independent portability regressions. All APIs/binaries use synthetic fixtures."""
import getpass
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
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'ran-selector')]
import runtime_config as rc
import runtime_render as rr
from state_schema import validate_state
from scenario_registry import load_registry

PROJECT = '22222222-2222-2222-2222-222222222222'
VIM = '33333333-3333-3333-3333-333333333333'
CLUSTER = '44444444-4444-4444-4444-444444444444'
NAMESPACE = '55555555-5555-5555-5555-555555555555'
OTHER = '66666666-6666-6666-6666-666666666666'


def network(name='eth-test', address='192.0.2.10', prefix=24, kind=None):
    entry = {'ifname': name, 'addr_info': [{'family': 'inet', 'local': address, 'prefixlen': prefix}]}
    if kind:
        entry['linkinfo'] = {'info_kind': kind}
    return lambda args: [entry] if 'address' in args else [{'dev': name, 'prefsrc': address}]


def catalog():
    c = mock.Mock()
    c.request.side_effect = [json.dumps([{'_id': PROJECT, 'name': 'project'}]).encode(),
                             json.dumps([{'_id': VIM, 'name': 'vim'}]).encode()]
    return c


def namespaces(cluster=CLUSTER, uid=NAMESPACE):
    return {'items': [{'metadata': {'name': 'ran', 'uid': uid}},
                      {'metadata': {'name': 'kube-system', 'uid': cluster}}]}


class AdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p0b1-adversarial-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo quote's $literal ; & ü%path"
        (self.root / 'config').mkdir(parents=True)
        shutil.copytree(ROOT / 'helm', self.root / 'helm')
        shutil.copy2(ROOT / 'config/scenarios.json', self.root / 'config/scenarios.json')
        self.home = self.base / 'new-home'
        self.home.mkdir()

    def cfg(self, overrides=None, **kw):
        return rc.load_config(self.root, environ=overrides or {}, home=self.home, **kw)

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd parser not installed')
    def test_actual_systemd_accepts_all_required_path_classes(self):
        for name in ['ordinary', 'spaces only', "quote's folder", 'literal $chars ; &', 'unicode-ü', 'percent%folder']:
            root = self.base / name
            for kind, folder in [('dashboard', 'ran-selector'), ('watcher', 'layer3-autonomous'), ('rancher-forward', '')]:
                (root / folder).mkdir(parents=True, exist_ok=True)
                cfg = {**self.cfg(), 'REPO_ROOT': str(root)}
                unit = self.base / 'parser-review.service'
                unit.write_text(rr.render_service(kind, cfg, user=getpass.getuser()))
                result = subprocess.run(['systemd-analyze', 'verify', '--man=no', str(unit)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_service_snapshot_preserves_overrides_and_excludes_unit_secrets(self):
        secret = 'SYNTHETIC_SECRET_SENTINEL'
        cfg = self.cfg({'OSM_HOST': 'https://osm.invalid', 'OSM_PROJECT': 'project', 'OSM_PASSWORD': secret, 'DASHBOARD_PORT': '8092'})
        snapshot = self.base / 'private.json'
        rc.write_snapshot(snapshot, cfg)
        loaded = rc.load_config(self.root, environ={'P0_RUNTIME_CONFIG': str(snapshot), 'DASHBOARD_PORT': '8099'}, home=self.home)
        self.assertEqual(loaded['DASHBOARD_PORT'], '8092')
        self.assertEqual(loaded['OSM_PASSWORD'], secret)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret, rr.render_service('dashboard', cfg))
        with self.assertRaises(rc.ConfigError):rc.write_snapshot(self.root / 'config/leaked.json', cfg)
        self.assertFalse((self.root / 'config/leaked.json').exists())
        alternate = self.cfg({'DASHBOARD_PORT': '8093', 'OSM_PROJECT': 'other'})
        self.assertNotEqual(rr.render_service('dashboard', cfg), rr.render_service('dashboard', alternate))

    def shell_fixture(self):
        (self.root / 'scripts').mkdir()
        for name in ['common.sh', 'runtime_config.py', 'scenario_registry.py', 'local_safety.py']:
            shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        binary = self.base / 'bin'; binary.mkdir()
        ip = binary / 'ip'
        ip.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(' + repr(network()(['address'])) + ' if "address" in sys.argv else ' + repr(network()(['route'])) + '))\n')
        ip.chmod(0o700)
        env = {k: v for k, v in os.environ.items() if k not in rc.DEFAULTS and k != 'P0_RUNTIME_CONFIG'}
        env.update(REPO_ROOT=str(self.root), HOME=str(self.home), PATH=str(binary) + ':' + os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1')
        return binary, env

    def test_shell_xtrace_and_child_environment_never_contain_sentinels(self):
        _, env = self.shell_fixture()
        secret = 'SYNTHETIC_XTRACE_SENTINEL'
        config = self.root / 'config/global.env'
        config.write_text('\n'.join(k + '=' + secret for k in rc.SECRETS));config.chmod(0o600)
        # Loading credentials from inherited env is also protected and strips export attributes.
        env['OSM_PASSWORD'] = secret
        checker = self.base / 'check.py'
        checker.write_text('import sys\nsys.path.insert(0,sys.argv[1])\nfrom runtime_config import load_config\nassert load_config()["OSM_PASSWORD"]==' + repr(secret) + '\n')
        command = 'source "$REPO_ROOT/scripts/common.sh"; load_config; env; python3 "$CHECKER" "$REPO_ROOT/scripts"; case $- in *x*) : ;; *) exit 7 ;; esac'
        env['CHECKER'] = str(checker)
        result = subprocess.run(['bash', '-x', '-c', command], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_management_and_workload_commands_select_explicit_kubeconfigs(self):
        binary, env = self.shell_fixture()
        for name in ['kubectl', 'helm']:
            command = binary / name
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n');command.chmod(0o700)
        command = 'source "$REPO_ROOT/scripts/common.sh"; KUBECONFIG_PATH="/synthetic/management config"; OSM_KUBECONFIG_PATH="/synthetic/workload config"; KUBECONFIG=/wrong; p_kubectl get nodes; r_kubectl get nodes; p_helm status demo; r_helm status demo'
        result = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['--kubeconfig /synthetic/management config get nodes', '--kubeconfig /synthetic/workload config get nodes', '--kubeconfig /synthetic/management config status demo', '--kubeconfig /synthetic/workload config status demo'])

    def test_private_read_rejects_symlinks_fifo_and_wrong_owner(self):
        target = self.base / 'private';target.write_text('OSM_PASSWORD=sentinel');target.chmod(0o600)
        link = self.base / 'link';link.symlink_to(target)
        with self.assertRaises(rc.ConfigError):rc.read_env(link)
        fifo = self.base / 'fifo';os.mkfifo(fifo)
        with self.assertRaises(rc.ConfigError):rc.read_env(fifo)
        with mock.patch.object(rc.os, 'getuid', return_value=os.getuid()+1):
            with self.assertRaises(rc.ConfigError):rc.read_env(target)

    def test_private_descriptor_permission_checked_before_read(self):
        path = self.base / 'global.env';path.write_text('OSM_PASSWORD=sentinel');path.chmod(0o644)
        with mock.patch.object(rc.os, 'fdopen', side_effect=AssertionError('read before validation')):
            with self.assertRaises(rc.ConfigError):rc.read_env(path)

    def test_replacing_path_after_open_does_not_replace_validated_content(self):
        path = self.base / 'global.env';path.write_text('OSM_PASSWORD=original');path.chmod(0o600)
        replacement = self.base / 'replacement';replacement.write_text('OSM_PASSWORD=replacement');replacement.chmod(0o600)
        original = rc.os.fstat
        def replace(fd):
            result = original(fd);replacement.replace(path);return result
        with mock.patch.object(rc.os, 'fstat', side_effect=replace):
            self.assertEqual(rc.read_env(path)['OSM_PASSWORD'], 'original')

    def test_virtual_routes_require_explicit_pair(self):
        for name, kind in [('docker0','bridge'),('cni0','bridge'),('flannel.1','vxlan'),('cali123','veth'),('tun0','tun'),('wg0','wireguard'),('custom-bridge','bridge'),('container-test','macvlan'),('custom-vpn','gre')]:
            with self.subTest(name=name), self.assertRaises(rc.ConfigError):
                self.cfg(network=True, query=network(name, '172.17.0.1', kind=kind))
            explicit = self.cfg({'HOST_IP':'172.17.0.1','HOST_INTERFACE':name}, network=True, query=network(name,'172.17.0.1',kind=kind))
            self.assertEqual(explicit['HOST_INTERFACE'],name)

    def test_multiple_routes_no_ipv4_mismatch_and_missing_interface_fail(self):
        def query(args):
            if 'address' in args:
                return network('eth-test')(['address']) + network('other','198.51.100.7')(['address'])
            return [{'dev':'eth-test','prefsrc':'192.0.2.10'},{'dev':'other','prefsrc':'198.51.100.7'}]
        with self.assertRaises(rc.ConfigError):self.cfg(network=True,query=query)
        for env in [{'HOST_IP':'198.51.100.7','HOST_INTERFACE':'eth-test'},{'HOST_INTERFACE':'missing'}]:
            with self.assertRaises(rc.ConfigError):self.cfg(env,network=True,query=network())
        with self.assertRaises(rc.ConfigError):self.cfg(network=True,query=lambda args:[])

    def test_host_prefix_and_ims_overlap_fail(self):
        with self.assertRaises(rc.ConfigError):
            self.cfg({'SERVICE_CIDR':'192.168.1.0/24'},network=True,query=network(address='192.168.0.5',prefix=16))
        for field in ['SERVICE_CIDR','POD_CIDR']:
            with self.assertRaises(rc.ConfigError):self.cfg({field:'10.46.0.0/16'})
        self.cfg(network=True, query=network()) # Canonical edge/core associations remain allowed.

    def test_malformed_nested_network_and_service_data_is_sanitized(self):
        with mock.patch.object(rc, 'command_json', side_effect=network()) as query:
            rc.discover_network('', '', query=query)
            self.assertEqual(query.call_args_list[0].args[0], ['ip', '-j', '-4', 'address', 'show'])
        for payload in [[None],[{'ifname':'eth-test','addr_info':[None]}],[{'ifname':'eth-test','addr_info':{},'linkinfo':{}}], [{'ifname':'eth-test','addr_info':[{'family':'inet','local':'bad','prefixlen':24}]}]]:
            with self.assertRaises(rc.ConfigError):rc.discover_network('','',lambda args:payload)
        for payload in [None,{}, {'items':[None]}, {'items':[{'metadata':None,'spec':{}}]}, {'items':[{'metadata':{'name':'svc','namespace':'ran'},'spec':{'ports':[None]}}]}]:
            with self.assertRaises(rc.ConfigError):rc.discover_service(payload,'svc','ran','port')

    def test_cluster_and_namespace_uids_change_context_despite_matching_names(self):
        cfg = self.cfg({'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_HOST':'https://osm.invalid','OSM_PROJECT_NAMESPACE':'ran'})
        a = rc.discover_context(cfg,catalog(),lambda:namespaces())
        b = rc.discover_context(cfg,catalog(),lambda:namespaces(cluster=OTHER))
        c = rc.discover_context(cfg,catalog(),lambda:namespaces(uid=OTHER))
        self.assertNotEqual(a,b);self.assertNotEqual(a,c)
        state = {'active':'none','osm':{},'context':a}
        with self.assertRaises(rc.ConfigError):validate_state(state,load_registry(),b)
        with self.assertRaises(rc.ConfigError):validate_state(state,load_registry(),c)

    def test_missing_or_malformed_namespace_identity_fails(self):
        cfg=self.cfg({'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_HOST':'https://osm.invalid','OSM_PROJECT_NAMESPACE':'ran'})
        for listing in [{'items':[{'metadata':None}]}, {'items':[{'metadata':{'name':'ran'}}]},namespaces(uid='bad')]:
            with self.assertRaises(rc.ConfigError):rc.discover_context(cfg,catalog(),lambda:listing)

    def test_explicit_osm_cluster_must_match_actual_workload_uid(self):
        cfg=self.cfg({'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_HOST':'https://osm.invalid','OSM_PROJECT_NAMESPACE':'ran','OSM_K8S_CLUSTER_ID':OTHER})
        def selected(credentials):
            c=mock.Mock();c.request.side_effect=[json.dumps([{'_id':PROJECT,'name':'project'}]).encode(),json.dumps([{'_id':OTHER,'credentials':credentials}]).encode(),json.dumps([{'_id':VIM,'name':'vim'}]).encode()];return c
        with mock.patch.object(rc,'command_json',return_value={'metadata':{'uid':OTHER}}):
            with self.assertRaises(rc.ConfigError):rc.discover_context(cfg,selected({'apiVersion':'v1'}),lambda:namespaces())
        with self.assertRaises(rc.ConfigError):rc.discover_context(cfg,selected(None),lambda:namespaces())
        with mock.patch.object(rc,'command_json',return_value={'metadata':{'uid':CLUSTER}}):
            self.assertEqual(rc.discover_context(cfg,selected({'apiVersion':'v1'}),lambda:namespaces())['cluster_uid'],CLUSTER)

    def test_explicit_endpoint_uses_one_load_without_local_network_discovery(self):
        import osm_client
        with mock.patch.object(osm_client,'load_config',wraps=lambda **kw:self.cfg({'OSM_HOST':'https://osm.invalid','OSM_PROJECT':'project','OSM_USER':'user','OSM_PASSWORD':'synthetic'},query=lambda args: (_ for _ in ()).throw(AssertionError('unexpected discovery')),**kw)) as load:
            osm_client.runtime_config()
            self.assertEqual(load.call_count,1)

    def test_auto_endpoint_derivation_and_required_namespace(self):
        cfg=self.cfg({'OSM_PROJECT':'project','OSM_USER':'user','OSM_PASSWORD':'synthetic'},network='auto',require=('osm',),query=network())
        self.assertEqual(cfg['OSM_HOST'],'https://gui.192.0.2.10.nip.io:30843')
        with self.assertRaises(rc.ConfigError):self.cfg({'OSM_NAMESPACE':''})

    def test_watcher_validation_happens_before_installer_mutations(self):
        text=(ROOT/'install.sh').read_text()
        self.assertIn('load_config --network --require install --require watcher',text)
        with self.assertRaises(rc.ConfigError):self.cfg(require=('watcher',))
        self.cfg({'LAYER3_FAILOVER_SCENARIO':'cran-srsran'},require=('watcher',))

    def test_status_preserves_error_and_nonzero_exit(self):
        (self.root/'scripts').mkdir()
        (self.root/'scripts/common.sh').write_text('load_config() { ACTIVE_STATE_PATH=/synthetic/no-state; }\n')
        validate=self.root/'scripts/validate.sh';validate.write_text('#!/bin/sh\necho "ERROR: synthetic discovery failure" >&2\nexit 17\n');validate.chmod(0o700)
        status=self.root/'status.sh';status.write_bytes((ROOT/'status.sh').read_bytes())
        result=subprocess.run(['bash',str(status)],capture_output=True,text=True)
        self.assertEqual(result.returncode,17)
        self.assertIn('synthetic discovery failure',result.stderr)
        self.assertNotIn('Platform:',result.stdout)

    def test_benchmark_selects_only_nonadditive_ready_ran(self):
        registry=load_registry();selected=[s['key'] for s in registry['scenarios'] if s['generation_status']=='ready' and not s['additive']]
        self.assertEqual(set(selected),{'cran-srsran','cran-oai','cloudran-srsran','cloudran-oai','vcran-srsran','vcran-oai'})
        self.assertIn('and not s["additive"]',(ROOT/'bench-all-ran.sh').read_text())

    def test_malformed_additive_state_rejected_before_lifecycle(self):
        spec=importlib.util.spec_from_file_location('adversarial_backend',ROOT/'ran-selector/backend.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
        cfg=self.cfg({'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_HOST':'https://osm.invalid','OSM_PROJECT_NAMESPACE':'ran'})
        context=rc.discover_context(cfg,catalog(),lambda:namespaces())
        state=self.base/'state.yaml';state.write_text(yaml.safe_dump({'active':'none','osm':{'additive_instances':'bad'},'context':context}))
        verified=mock.Mock();verified.verify.return_value={'ns':PROJECT}
        with mock.patch.object(b,'CONFIG_FILE',str(state)),mock.patch.object(b,'validated_snapshot',return_value={}),mock.patch.object(b,'runtime_preflight',return_value=(cfg,context,verified)),mock.patch.object(b.osm_client,'instantiate_ns') as instantiate,mock.patch.object(b.osm_client,'terminate_ns') as terminate:
            response=b.app.test_client().post('/api/deploy',json={'ran':'fran'})
            self.assertNotEqual(response.status_code,200)
            instantiate.assert_not_called();terminate.assert_not_called()
        self.assertNotIn('pending_instance',yaml.safe_load(state.read_text())['osm'])

    def test_complete_state_field_types_are_validated(self):
        for osm in [{'additive_instances':[]},{'additive_instances':{'cran-oai':'id'}},{'pending_instance':None},{'pending_instance':{'scenario':'fran'}},{'active_instance_id':123},{'active_scenario':[]},{'core_instance_id':False}]:
            with self.subTest(osm=osm), self.assertRaises(rc.ConfigError):validate_state({'active':'none','osm':osm},load_registry())

    def test_service_handoff_limits_credentials_by_consumer(self):
        cfg=self.cfg({k:'synthetic-'+k for k in rc.SECRETS})
        dashboard=rr.service_configuration('dashboard',cfg)
        watcher=rr.service_configuration('watcher',cfg)
        self.assertEqual(dashboard['OSM_PASSWORD'],'synthetic-OSM_PASSWORD')
        self.assertEqual(dashboard['GRAFANA_ADMIN_PASSWORD'],'')
        self.assertEqual(dashboard['RANCHER_BOOTSTRAP_PASSWORD'],'')
        self.assertTrue(all(not watcher[k] for k in rc.SECRETS))

    def test_installed_service_drift_fails_without_writing(self):
        cfg=self.cfg()
        snapshot=Path(cfg['RUNTIME_DIR'])/'dashboard-service.json'
        rc.write_snapshot(snapshot,rr.service_configuration('dashboard',cfg))
        unit=self.base/'existing.service';unit.write_text(rr.render_service('dashboard',cfg))
        rr.check_installed_service('dashboard',cfg,unit)
        before=(unit.read_bytes(),snapshot.read_bytes())
        changed=self.cfg({'DASHBOARD_PORT':'8092'})
        with self.assertRaises(rc.ConfigError):rr.check_installed_service('dashboard',changed,unit)
        changed=self.cfg({'OSM_PASSWORD':'synthetic-new-password'})
        with self.assertRaises(rc.ConfigError):rr.check_installed_service('dashboard',changed,unit)
        self.assertEqual(before,(unit.read_bytes(),snapshot.read_bytes()))

    def test_configuration_snapshot_is_immutable(self):
        cfg=self.cfg()
        with self.assertRaises(TypeError):cfg['OSM_HOST']='https://other.invalid'

    def test_explicit_project_id_is_used_for_authentication(self):
        import osm_client
        cfg=self.cfg({'OSM_HOST':'https://osm.invalid','OSM_PROJECT':'project','OSM_PROJECT_ID':PROJECT,'OSM_USER':'user','OSM_PASSWORD':'synthetic'})
        with mock.patch.object(osm_client,'runtime_config',return_value=cfg),mock.patch.object(osm_client,'Catalog') as factory:
            factory.return_value.token='synthetic-token'
            osm_client.get_token(force=True)
            factory.return_value.authenticate.assert_called_once_with('user','synthetic',PROJECT)

    def test_empty_state_init_derives_endpoint_once_and_binds_real_identities(self):
        import contextlib,io
        kube=self.home/'.kube/config';kube.parent.mkdir();kube.write_text('synthetic')
        overrides={'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_USER':'user','OSM_PASSWORD':'synthetic','OSM_PROJECT_NAMESPACE':'ran'}
        def load(**kw):return self.cfg(overrides,query=network(),**kw)
        with mock.patch.object(rr,'load_config',side_effect=load) as loaded,mock.patch('osm_catalog.Catalog') as factory,mock.patch.object(rc,'kubernetes_json',return_value=namespaces()),mock.patch.object(sys,'argv',['runtime_render.py','init-state']),contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.request.side_effect=[json.dumps([{'_id':PROJECT,'name':'project'}]).encode(),json.dumps([{'_id':VIM,'name':'vim'}]).encode(),b'[]']
            rr.main()
            self.assertEqual(loaded.call_count,1)
            factory.assert_called_once_with('https://gui.192.0.2.10.nip.io:30843')
        state=yaml.safe_load((self.root/'.runtime/active-ran.yaml').read_text())
        self.assertEqual(state['context']['cluster_uid'],CLUSTER)
        self.assertEqual(state['context']['namespace_uid'],NAMESPACE)
        validate_state(state,load_registry())

    def test_selected_osm_cluster_credentials_cannot_execute_plugins(self):
        cfg=self.cfg({'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_HOST':'https://osm.invalid','OSM_PROJECT_NAMESPACE':'ran','OSM_K8S_CLUSTER_ID':OTHER})
        for credentials in [{'users':[{'name':'bad','user':{'exec':{'command':'unsafe'}}}]},
                            {'users':[{'name':'bad','user':{'tokenFile':'/synthetic/private'}}]},
                            {'clusters':[{'name':'bad','cluster':{'insecure-skip-tls-verify':True}}]}]:
            selected=mock.Mock();selected.request.side_effect=[json.dumps([{'_id':PROJECT,'name':'project'}]).encode(),json.dumps([{'_id':OTHER,'credentials':credentials}]).encode(),json.dumps([{'_id':VIM,'name':'vim'}]).encode()]
            with mock.patch.object(rc,'command_json',side_effect=AssertionError('untrusted credential config must not execute')):
                with self.assertRaises(rc.ConfigError):rc.discover_context(cfg,selected,lambda:namespaces())

    def test_changed_cluster_state_cannot_terminate_or_instantiate(self):
        spec=importlib.util.spec_from_file_location('context_backend',ROOT/'ran-selector/backend.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
        cfg=self.cfg({'OSM_PROJECT':'project','OSM_VIM_NAME':'vim','OSM_HOST':'https://osm.invalid','OSM_PROJECT_NAMESPACE':'ran'})
        old=rc.discover_context(cfg,catalog(),lambda:namespaces())
        new=rc.discover_context(cfg,catalog(),lambda:namespaces(cluster=OTHER))
        state=self.base/'state.yaml';state.write_text(yaml.safe_dump({'active':'cran-srsran','osm':{'active_instance_id':VIM,'active_scenario':'cran-srsran'},'context':old}))
        before=state.read_bytes();verified=mock.Mock();verified.verify.return_value={'ns':PROJECT}
        with mock.patch.object(b,'CONFIG_FILE',str(state)),mock.patch.object(b,'validated_snapshot',return_value={}),mock.patch.object(b,'runtime_preflight',return_value=(cfg,new,verified)),mock.patch.object(b.osm_client,'instantiate_ns') as instantiate,mock.patch.object(b.osm_client,'terminate_ns') as terminate:
            response=b.app.test_client().post('/api/deploy',json={'ran':'cran-oai'})
            self.assertNotEqual(response.status_code,200)
            instantiate.assert_not_called();terminate.assert_not_called()
        self.assertEqual(before,state.read_bytes())

    def test_historical_tracked_state_is_never_adopted_after_fresh_clone(self):
        (self.root/'scripts').mkdir()
        for name in ['runtime_state.py','runtime_config.py','state_schema.py','scenario_registry.py','osm_catalog.py']:
            shutil.copy2(ROOT/'scripts'/name,self.root/'scripts'/name)
        (self.root/'ran-selector').mkdir()
        (self.root/'ran-selector/active-ran.yaml').write_text('active: cran-oai\nosm:\n  active_instance_id: '+OTHER+'\n')
        env={k:v for k,v in os.environ.items() if k not in rc.DEFAULTS and k!='P0_RUNTIME_CONFIG'}
        env.update(HOME=str(self.home),PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-B',str(self.root/'scripts/runtime_state.py'),'active'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'none\n')


    def test_unbound_empty_state_cannot_be_implicitly_adopted(self):
        with self.assertRaises(rc.ConfigError):validate_state({'active':'none','osm':{}},load_registry(),{'project_id':PROJECT})

    def test_inconsistent_active_and_pending_state_rejected(self):
        for state in [{'active':'cran-oai','osm':{'active_scenario':'cran-srsran','active_instance_id':PROJECT}},
                      {'active':'none','osm':{'pending_instance':{'scenario':'fran','stage':17,'id':PROJECT,'operation':VIM}}}]:
            with self.assertRaises(rc.ConfigError):validate_state(state,load_registry())


    def test_service_launcher_uses_literal_paths_and_selected_context(self):
        import service_entry
        cfg=self.cfg({'DASHBOARD_PORT':'8092','RANCHER_PORT':'8444'})
        for kind in ('dashboard','watcher','rancher-forward'):
            with mock.patch.object(service_entry,'load_config',return_value=cfg),mock.patch.object(sys,'argv',['service_entry.py',kind,str(self.base/'snapshot.json')]),mock.patch.object(service_entry.os,'execv') as execv,mock.patch.object(service_entry.os,'execvp',side_effect=RuntimeError('mock exec boundary')) as execvp,mock.patch.dict(os.environ,{'OSM_PASSWORD':'synthetic-inherited'}):
                if kind=='rancher-forward':
                    with self.assertRaises(RuntimeError):service_entry.main()
                    argv=execvp.call_args.args[1]
                    self.assertEqual(argv[1:3],['--kubeconfig',cfg['KUBECONFIG_PATH']])
                    self.assertIn('8444:443',argv)
                else:
                    service_entry.main()
                    binary,argv=execv.call_args.args
                    self.assertEqual(argv[0],binary)
                    self.assertEqual(argv[1],str(self.root/('ran-selector/backend.py' if kind=='dashboard' else 'layer3-autonomous/watcher.py')))
                self.assertNotIn('OSM_PASSWORD',os.environ)


    def test_independent_all_ready_helm_resources_against_baseline(self):
        baseline='4db25352a4eb04e0bdf0849458a221b51991e2dc'
        if subprocess.run(['git','cat-file','-e',baseline],cwd=ROOT,capture_output=True).returncode:
            self.skipTest('Baseline unavailable in shallow checkout')
        old=self.base/'baseline';old.mkdir()
        paths=subprocess.check_output(['git','ls-tree','-r','--name-only',baseline,'helm/cran-oai','helm/cran-srsran','helm/fran-edge'],cwd=ROOT,text=True).splitlines()
        for name in paths:
            dest=old/name;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(subprocess.check_output(['git','show',baseline+':'+name],cwd=ROOT))
        def render(home,chart):
            args=['helm','template',chart['release_name'],str(home/chart['source']),'-n','review-namespace']
            for profile in chart['values_files']:args.extend(['-f',str(home/profile)])
            if home == ROOT:
                from runtime_images import chart_values
                values=self.base/'approved-images.json'
                values.write_text(json.dumps(chart_values(ROOT,chart)))
                args.extend(['-f',str(values)])
            return list(yaml.safe_load_all(subprocess.check_output(args,text=True,stderr=subprocess.PIPE)))
        # The CU checksum hashes all values, including the newly explicit
        # approved image reference. Non-image/config resource content is still
        # compared independently to the historical baseline below.
        expected_checksums={'cran-oai':('r1-49013ed9','r1-44832321'),'cloudran-oai':('r1-e7850648','r1-b0a5a416'),'vcran-oai':('r1-d6326412','r1-459864ff')}
        charts=resources=changes=0
        for scenario in load_registry()['scenarios']:
            if scenario['generation_status']!='ready':continue
            for chart in scenario['charts']:
                before,after=render(old,chart),render(ROOT,chart)
                charts+=1;resources+=len(after)
                self.assertEqual(len(before),len(after))
                for previous,current in zip(before,after):
                    if current['kind']=='Deployment':
                        before_container=previous['spec']['template']['spec']['containers'][0]
                        after_container=current['spec']['template']['spec']['containers'][0]
                        if scenario['implementation']=='srsRAN':
                            self.assertEqual(before_container['image'],'ghcr.io/herlesupreeth/docker_srsran:master')
                            self.assertEqual(after_container['image'],'localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6')
                            self.assertEqual(after_container['imagePullPolicy'],'Never')
                            before_container.update(image=after_container['image'],imagePullPolicy='Never')
                            if scenario['key']=='vcran-srsran' and chart['kdu']=='cu':
                                self.assertEqual(before_container['resources']['requests']['memory'],'256Mi')
                                self.assertEqual(before_container['resources']['limits']['memory'],'512Mi')
                                expected={'requests':{'cpu':'250m','memory':'512Mi'},'limits':{'cpu':'500m','memory':'1Gi'}}
                                self.assertEqual(after_container['resources'],expected)
                                before_container['resources']=expected

                        elif scenario['key']=='fran':
                            self.assertEqual(before_container['image'],'nginx:alpine')
                            self.assertEqual(after_container['image'],'docker.io/library/nginx@sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2')
                            before_container.update(image=after_container['image'],imagePullPolicy='IfNotPresent')
                        else:
                            self.assertEqual(after_container['image'],'oaisoftwarealliance/oai-gnb:'+('2026.w13' if chart['kdu']=='cu' else '2026.w25'))
                    if previous!=current:
                        self.assertEqual(current['kind'],'Deployment')
                        self.assertEqual(chart['kdu'],'cu')
                        before_annotations=previous['spec']['template']['metadata']['annotations']
                        after_annotations=current['spec']['template']['metadata']['annotations']
                        pair=(before_annotations.pop('checksum/config'),after_annotations.pop('checksum/config'))
                        self.assertEqual(pair,expected_checksums[scenario['key']])
                        changes+=1
                    self.assertEqual(previous,current)
        self.assertEqual((charts,resources,changes),(13,39,3))



if __name__=='__main__':unittest.main()
