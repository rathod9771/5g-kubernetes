"""Offline HP/Dell fresh-host admission; never contacts Kubernetes or OSM."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'scripts/installer/host_profile.py'
spec = importlib.util.spec_from_file_location('installer_host_profile', MOD)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class InstallerHostProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'etc/kubernetes').mkdir(parents=True)
        self.home = self.root / 'home/operator'
        self.home.mkdir(parents=True)

    def check(self, target='dell', **kw):
        opts = dict(root=self.root, home=self.home, euid=1001, arch='x86_64',
                    cpus=8, vendor='Dell Inc.' if target == 'dell' else 'Hewlett-Packard',
                    os_release={'ID': 'ubuntu', 'VERSION_ID': '24.04'},
                    mem_gib=31.0, disk_free_gib=150.0)
        opts.update(kw)
        return profile.check(target, **opts)

    def test_clean_dell_candidate_only(self):
        r = self.check()
        self.assertEqual(r['status'], 'CANDIDATE')
        self.assertFalse(r['cluster_installation_performed'])
        self.assertFalse(r['mutations_performed'])
        self.assertFalse(r['deployment_authorized'])

    def test_clean_hp(self):
        self.assertEqual(self.check('hp')['status'], 'CANDIDATE')

    def test_wrong_hardware_profile(self):
        self.assertIn('HARDWARE_PROFILE_MISMATCH', self.check('hp', vendor='Dell Inc.')['blockers'])

    def test_wrong_ubuntu(self):
        self.assertIn('UBUNTU_24_04_REQUIRED', self.check(os_release={'ID': 'ubuntu', 'VERSION_ID': '22.04'})['blockers'])

    def test_root_refused(self):
        self.assertIn('RUN_AS_NON_ROOT_USER', self.check(euid=0)['blockers'])

    def test_small_cpu_ram_disk(self):
        for name, val, error in (('cpus', 4, 'CPU_CAPACITY_INSUFFICIENT'),
                                 ('mem_gib', 8, 'MEMORY_CAPACITY_INSUFFICIENT'),
                                 ('disk_free_gib', 30, 'DISK_CAPACITY_INSUFFICIENT')):
            with self.subTest(name=name):
                self.assertIn(error, self.check(**{name: val})['blockers'])

    def test_existing_admin_conf_refused(self):
        (self.root / 'etc/kubernetes/admin.conf').touch()
        self.assertIn('EXISTING_CLUSTER_STATE', self.check()['blockers'])

    def test_existing_kubeconfig_refused(self):
        (self.home / '.kube').mkdir()
        (self.home / '.kube/config').touch()
        self.assertIn('EXISTING_CLUSTER_STATE', self.check()['blockers'])

    def test_existing_symlink_refused(self):
        (self.root / 'etc/kubernetes/admin.conf').symlink_to('/nonexistent')
        self.assertIn('EXISTING_CLUSTER_STATE', self.check()['blockers'])

    def test_unknown_profile_refused(self):
        with self.assertRaisesRegex(ValueError, 'unknown host profile'):
            self.check('generic')

    def test_private_bundle_profile_precedes_all_host_mutations(self):
        builder = (ROOT / 'scripts/build-private-installer.sh').read_text()
        # This file has a source-host bundle builder and a generated target
        # installer. Only the latter must order admission ahead of apt/import.
        start = builder.index("cat >> \"$OUTPUT\" <<'HEADER2'")
        end = builder.index("\nHEADER2", start)
        target = builder[start:end]
        gate = target.index('host_profile.py')
        self.assertLess(gate, target.index('sudo apt-get update'))
        self.assertLess(gate, target.index('sudo ctr -n k8s.io images import'))
        self.assertIn('exec ./install.sh --profile "$HOST_PROFILE"', target)
        self.assertIn('if [[ -e "$TARGET_REPO" || -L "$TARGET_REPO" ]]', target)

    def test_shell_preflight_before_mutation(self):
        shell = (ROOT / 'install.sh').read_text()
        self.assertLess(shell.index('host_profile.py'), shell.index('sudo apt-get update'))
        self.assertIn('--check-only', shell)


if __name__ == '__main__':
    unittest.main()
