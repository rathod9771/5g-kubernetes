"""Offline publication gates: no service or network calls."""
import ast,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import runtime_config as config
import service_entry

class PublicationSafetyTests(unittest.TestCase):
    def test_no_privileged_default_or_failover(self):
        self.assertTrue(all(config.DEFAULTS[k]=='' for k in config.SECRETS))
        self.assertEqual(config.DEFAULTS['LAYER3_FAILOVER_SCENARIO'],'')
    def test_launcher_rejects_watcher_before_configuration_or_network(self):
        with patch.object(sys,'argv',['service_entry','watcher','unused']),patch.object(service_entry,'load_config') as load,patch.object(service_entry,'ensure_osm_ca') as tls:
            with self.assertRaisesRegex(config.ConfigError,'disabled'):service_entry.main()
            load.assert_not_called();tls.assert_not_called()
    def test_direct_legacy_failover_cannot_call_deploy(self):
        module=ast.parse((ROOT/'layer3-autonomous/watcher.py').read_text())
        fn=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='trigger_failover')
        env={};exec(compile(ast.Module(body=[fn],type_ignores=[]),'legacy-gate','exec'),env)
        with self.assertRaises(PermissionError):env['trigger_failover'](.9)
        self.assertEqual(len(fn.body),1)
    def test_installer_does_not_enable_watcher(self):
        source=(ROOT/'scripts/installer/install.py').read_text()
        self.assertNotIn("('watcher', 'layer3-watcher')",source)
        self.assertNotIn('--require watcher',(ROOT/'install.sh').read_text())
    def test_disabled_unit_cannot_be_rendered(self):
        import runtime_render
        with self.assertRaisesRegex(config.ConfigError,'disabled'):runtime_render.render_service('watcher',{})
    def test_active_legacy_watcher_blocks_installer_preflight(self):
        from installer import install
        cfg={'SUBSCRIBER_DATABASE_INPUT':'synthetic','DEPLOYMENT_PROFILE':'rfsim','OSM_HTTPS_PORT':'30843','OSM_CLUSTER_NAME':'test','OSM_VIM_NAME':'test'}
        with patch.object(install,'private_archive'),patch.object(install.subprocess,'run') as run:
            run.return_value.returncode=0
            with self.assertRaisesRegex(config.ConfigError,'Legacy watcher is active'):install.preflight(cfg)
            self.assertEqual(run.call_count,1)
    def test_example_has_no_password_literals(self):
        values=config.read_env(ROOT/'config/global.env.example')
        self.assertTrue(all(not values.get(k) for k in config.SECRETS))

if __name__=='__main__':unittest.main()
