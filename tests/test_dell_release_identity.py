"""Guard tests for Dell workflow identity; no OSM/Kubernetes network calls."""
import importlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
builder = importlib.import_module('build_release')
manifest = importlib.import_module('release_manifest')

REPO = 'rathod9771/5g-kubernetes'
DELL = '.github/workflows/dell-release-candidate.yml'
HP = '.github/workflows/release-candidate.yml'

class DellReleaseIdentityTests(unittest.TestCase):
    def github_env(self, workflow):
        return {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'GITHUB_REPOSITORY': REPO, 'GITHUB_WORKFLOW_REF': REPO+'/'+workflow+'@refs/heads/p5/dell-release-candidate',
                'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/p5/dell-release-candidate',
                'GITHUB_RUN_ID': '123456', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_SERVER_URL': 'https://github.com'}
    def test_hp_default_is_unchanged(self):
        self.assertEqual(builder.WORKFLOW_PATH, HP)
        self.assertEqual(manifest.WORKFLOW_PATH, HP)
        self.assertEqual(builder.DEFAULT_RELEASE_BRANCHES, ('reproducible-installer',))
    def test_dell_path_pinned(self):
        self.assertEqual(builder.DELL_WORKFLOW_PATH, DELL)
        self.assertEqual(manifest.DELL_WORKFLOW_PATH, DELL)
    def test_exact_dell_workflow_identity(self):
        value = builder.identity(ROOT, self.github_env(DELL), workflow_path=DELL)
        self.assertEqual(value['workflow']['path'], DELL)
        self.assertEqual(value['workflow']['runner_environment'], 'github-hosted')
    def test_dell_not_accepted_under_hp_mode(self):
        with self.assertRaises(builder.ReleaseError):
            builder.identity(ROOT, self.github_env(DELL))
    def test_hp_not_accepted_under_dell_mode(self):
        with self.assertRaises(builder.ReleaseError):
            builder.identity(ROOT, self.github_env(HP), workflow_path=DELL)
    def test_wrong_workflow_path_rejected(self):
        with self.assertRaises(builder.ReleaseError):
            builder.identity(ROOT, self.github_env('attacker.yml'), workflow_path=DELL)
    def test_not_self_hosted(self):
        env = self.github_env(DELL)
        env['RUNNER_ENVIRONMENT'] = 'self-hosted'
        with self.assertRaises(builder.ReleaseError):
            builder.identity(ROOT, env, workflow_path=DELL)
    def test_dell_verifier_policy_is_separate(self):
        hp = json.loads((ROOT/'config/release-verification.json').read_text())
        dell = json.loads((ROOT/'config/dell-release-verification.json').read_text())
        self.assertEqual(hp['branch'], 'reproducible-installer')
        self.assertEqual(hp['target']['environment'], 'hp-lab')
        self.assertEqual(hp['workflow_path'], HP)
        self.assertEqual(dell['branch'], 'p5/dell-release-candidate')
        self.assertEqual(dell['workflow_path'], DELL)
        self.assertEqual(dell['target'], {'environment':'dell-precision-3680-research','architecture':'linux/amd64'})
        self.assertEqual(dell['required_jobs'], ['build','attest'])
    def test_p4_approval_stays_closed(self):
        source=(ROOT/'scripts/lifecycle.py').read_text()
        self.assertIn('P4_KDU_UPGRADE_APPROVAL = None',source)
    def test_workflow_absent_deployment_actions(self):
        txt=(ROOT/DELL).read_text()
        self.assertIn('runs-on: ubuntu-latest',txt)
        self.assertIn('id-token: write',txt)
        self.assertIn('attestations: write',txt)
        self.assertNotIn('runs-on: self-hosted',txt)
        self.assertNotIn('kubectl apply',txt)
        self.assertNotIn('helm upgrade',txt)
        self.assertNotIn('osm ns-create',txt)

    def test_dell_release_base_selection_is_bounded(self):
        workflow = (ROOT / DELL).read_text()

        self.assertIn('id: base', workflow)
        self.assertIn('git merge-base --is-ancestor', workflow)
        self.assertIn('--base "${{ steps.base.outputs.base }}"', workflow)
        self.assertNotIn("--base ''", workflow)

if __name__ == '__main__':
    unittest.main()
