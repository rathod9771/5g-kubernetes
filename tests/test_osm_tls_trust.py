"""Target CA export and shared client trust, with local synthetic certificates."""
import base64
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import test_reference_installer as installer_tests
import test_pending_adoption as adoption
from runtime_config import load_config, ConfigError, write_snapshot, read_snapshot
import osm_tls
import runtime_render
import service_entry
import sys
import contextlib
import io

Installer, ROOT = installer_tests.Installer, installer_tests.ROOT


class OSMTLSTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        cert = self.directory / 'synthetic-ca.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(self.directory / 'synthetic.key'), '-out', str(cert),
                        '-days', '1', '-subj', '/CN=synthetic-test-ca', '-addext', 'basicConstraints=critical,CA:TRUE'],
                       check=True, capture_output=True)
        self.certificate = cert.read_bytes()
        self.root = self.directory / "checkout"
        (self.root / "config").mkdir(parents=True)
        (self.root / "config/scenarios.json").write_bytes((ROOT / "config/scenarios.json").read_bytes())
        shutil.copytree(ROOT / "helm", self.root / "helm")
        self.cfg = load_config(self.root, environ={'HOME':str(self.directory), 'RUNTIME_DIR':str(self.directory),
                                             'HOST_IP':'192.0.2.1', 'HOST_INTERFACE':'test0'})
        self.installer = Installer(self.cfg)
        self.installer.snapshot = mock.Mock()
        self.installer.kubectl = mock.Mock(return_value=mock.Mock(stdout=base64.b64encode(self.certificate)))

    def test_export_only_certificate_at_deterministic_private_path(self):
        path = self.installer.export_osm_ca()
        self.assertEqual(path, self.directory / 'osm-ca.crt')
        self.assertEqual(path.read_bytes(), self.certificate)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.installer.cfg['OSM_CA_CERT_PATH'], str(path.resolve()))
        self.installer.snapshot.assert_called_once()
        args, kwargs = self.installer.kubectl.call_args
        self.assertEqual(args, ('get', 'secret', 'osm-ca', '-n', 'osm', '-o', 'jsonpath={.data.tls\\.crt}'))
        self.assertNotIn('tls.key', str(args))

    def test_rerun_refreshes_safely_and_invalid_input_preserves_existing_file(self):
        path = self.installer.export_osm_ca()
        self.installer.export_osm_ca()
        self.assertEqual(path.read_bytes(), self.certificate)
        for invalid in (b'', b'not-base64!', base64.b64encode(b'not a certificate'),
                        base64.b64encode(self.certificate + b'-----BEGIN PRIVATE KEY-----\nprivate\n')):
            self.installer.kubectl.return_value.stdout = invalid
            with self.assertRaisesRegex(ConfigError, 'OSM TLS trust.*missing or invalid'):
                self.installer.export_osm_ca()
            self.assertEqual(path.read_bytes(), self.certificate)

    def test_missing_secret_failure_is_clear(self):
        self.installer.kubectl.side_effect = ConfigError('OSM TLS trust: read osm-ca tls.crt: not found')
        with self.assertRaisesRegex(ConfigError, 'OSM TLS trust.*osm-ca.*not found'):
            self.installer.export_osm_ca()
        self.assertFalse((self.directory / 'osm-ca.crt').exists())

    def test_shared_configuration_discovers_export_without_snapshot_or_env_edits(self):
        self.installer.export_osm_ca()
        cfg = load_config(self.root, environ={'HOME':str(self.directory), 'RUNTIME_DIR':str(self.directory),
                                         'HOST_IP':'192.0.2.1', 'HOST_INTERFACE':'test0', 'OSM_CA_CERT_PATH':''})
        self.assertEqual(cfg['OSM_CA_CERT_PATH'], str(self.directory / 'osm-ca.crt'))
        cfg = load_config(self.root, environ={'HOME':str(self.directory), 'RUNTIME_DIR':str(self.directory),
                                         'HOST_IP':'192.0.2.1', 'HOST_INTERFACE':'test0', 'OSM_CA_CERT_PATH':'custom.crt'})
        self.assertEqual(cfg['OSM_CA_CERT_PATH'], str(self.root / 'custom.crt'))

    def test_dashboard_client_uses_exported_ca(self):
        self.installer.export_osm_ca()
        client = adoption.backend.osm_client
        cfg = dict(self.installer.cfg, OSM_HOST='https://synthetic.invalid', OSM_USER='synthetic',
                   OSM_PASSWORD='synthetic', OSM_PROJECT='synthetic')
        with client.runtime_session(cfg), mock.patch.dict(client._token_cache), mock.patch.object(client, 'Catalog') as catalog:
            client.get_token(force=True)
            self.assertEqual(catalog.return_value.ca_file, cfg['OSM_CA_CERT_PATH'])

    def test_service_render_provisions_ca_and_persists_path(self):
        with mock.patch.object(runtime_render, 'load_config', return_value=self.cfg), mock.patch.object(osm_tls, 'read_target_certificate', return_value=base64.b64encode(self.certificate)) as read, mock.patch.object(sys, 'argv', ['runtime_render.py', 'dashboard']), contextlib.redirect_stdout(io.StringIO()):
            runtime_render.main()
        saved = read_snapshot(self.directory / 'dashboard-service.json')
        self.assertEqual(saved['OSM_CA_CERT_PATH'], str(self.directory / 'osm-ca.crt'))
        read.assert_called_once()

    def test_service_entry_repairs_blank_snapshot_before_exec(self):
        snapshot = self.directory / 'dashboard-service.json'
        write_snapshot(snapshot, self.cfg)
        with mock.patch.object(service_entry, 'load_config', return_value=dict(self.cfg)), mock.patch.object(osm_tls, 'read_target_certificate', return_value=base64.b64encode(self.certificate)), mock.patch.object(sys, 'argv', ['service_entry.py', 'dashboard', str(snapshot)]), mock.patch.object(service_entry.os, 'execv') as execute, mock.patch.dict(service_entry.os.environ):
            service_entry.main()
        execute.assert_called_once()
        self.assertEqual(read_snapshot(snapshot)['OSM_CA_CERT_PATH'], str(self.directory / 'osm-ca.crt'))

    def test_reuse_and_refresh_only_public_ca_without_network_on_reuse(self):
        path = self.installer.export_osm_ca()
        with mock.patch.object(osm_tls, 'read_target_certificate', side_effect=AssertionError('unexpected network')):
            self.assertEqual(osm_tls.ensure_osm_ca(self.installer.cfg), path)
        with mock.patch.object(osm_tls.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=base64.b64encode(self.certificate))) as run:
            osm_tls.ensure_osm_ca(self.installer.cfg, refresh=True)
        command = run.call_args.args[0]
        self.assertIn('jsonpath={.data.tls\\.crt}', command)
        self.assertNotIn('tls.key', str(command))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_explicit_ca_is_not_overwritten_and_private_keys_rejected(self):
        custom = self.directory / 'custom.crt'
        custom.write_bytes(self.certificate)
        custom.chmod(0o600)
        cfg = dict(self.cfg, OSM_CA_CERT_PATH=str(custom))
        with mock.patch.object(osm_tls, 'read_target_certificate', side_effect=AssertionError('unexpected network')):
            osm_tls.ensure_osm_ca(cfg, refresh=True)
        custom.write_bytes(self.certificate + b'-----BEGIN PRIVATE KEY-----\nprivate\n')
        with self.assertRaises(ConfigError): osm_tls.ensure_osm_ca(cfg)

    def test_cli_updates_only_ca_field_in_existing_snapshots(self):
        names = ['dashboard-service.json', 'watcher-service.json', 'rancher-forward-service.json', 'installer-config.json']
        for name in names: write_snapshot(self.directory / name, self.cfg)
        with mock.patch.object(osm_tls, 'load_config', return_value=dict(self.cfg)), mock.patch.object(osm_tls, 'read_target_certificate', return_value=base64.b64encode(self.certificate)), mock.patch.object(sys, 'argv', ['osm_tls.py']), contextlib.redirect_stdout(io.StringIO()) as output:
            osm_tls.main()
        self.assertNotIn(self.certificate.decode(), output.getvalue())
        for name in names:
            saved = read_snapshot(self.directory / name)
            self.assertEqual(saved['OSM_CA_CERT_PATH'], str(self.directory / 'osm-ca.crt'))
            before = dict(self.cfg); before['OSM_CA_CERT_PATH'] = saved['OSM_CA_CERT_PATH']
            self.assertEqual(saved, {key:before[key] for key in saved})

    def test_legacy_empty_ca_service_revision_accepts_only_managed_trust(self):
        cfg = dict(self.cfg)
        unit = self.directory / 'dashboard.service'
        unit.write_text(runtime_render.render_service('dashboard', cfg))
        self.installer.export_osm_ca()
        cfg['OSM_CA_CERT_PATH'] = str(self.directory / 'osm-ca.crt')
        write_snapshot(self.directory / 'dashboard-service.json', runtime_render.service_configuration('dashboard', cfg))
        runtime_render.check_installed_service('dashboard', cfg, unit)
        for field, value in [('DASHBOARD_PORT','8099'), ('OSM_CA_CERT_PATH',str(self.directory / 'custom.crt'))]:
            with self.subTest(field=field), self.assertRaises(ConfigError):
                runtime_render.check_installed_service('dashboard', dict(cfg, **{field:value}), unit)
        (self.directory / 'osm-ca.crt').write_bytes(b'not a certificate')
        with self.assertRaises(ConfigError): runtime_render.check_installed_service('dashboard', cfg, unit)
