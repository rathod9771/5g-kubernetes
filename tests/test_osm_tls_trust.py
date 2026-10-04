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
from runtime_config import load_config, ConfigError

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
