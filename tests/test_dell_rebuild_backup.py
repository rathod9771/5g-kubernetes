"""Dell rebuild backup static fail-closed safety contract."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
class DellRebuildBackupContract(unittest.TestCase):
    def test_off_host_and_dell_only(self):
        s = (ROOT/'scripts/installer/dell_rebuild_backup.sh').read_text()
        for v in ('precision-3680', 'mountpoint -q', 'lsblk -s', 'grep -Fxq usb'):
            self.assertIn(v,s)
    def test_no_mutating_kubernetes_actions(self):
        s = (ROOT/'scripts/installer/dell_rebuild_backup.sh').read_text()
        for v in ('kubeadm reset','kubectl delete','helm uninstall','mkfs.','rm -rf','systemctl stop'):
            self.assertNotIn(v,s)
    def test_integrity_and_volume_warning(self):
        s = (ROOT/'scripts/installer/dell_rebuild_backup.sh').read_text()
        for v in ('tar -tzf','sha256sum -c SHA256SUMS','LONGHORN PVC CONTENTS ARE NOT INCLUDED','NO CLUSTER RESET'):
            self.assertIn(v,s)
if __name__=='__main__':
    unittest.main()
