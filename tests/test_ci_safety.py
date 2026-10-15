"""Publication must not cause production OSM mutations."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

class CISafetyTests(unittest.TestCase):
    def test_osm_workflow_is_manual_and_read_only(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/osm-onboard.yml').read_text())
        # PyYAML's YAML 1.1 parser recognizes the Actions key 'on' as True.
        triggers = workflow.get('on', workflow.get(True))
        self.assertEqual(set(triggers), {'workflow_dispatch'})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        commands = [step['run'] for job in workflow['jobs'].values()
                    for step in job['steps'] if 'run' in step]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].strip(), 'python3 scripts/osm_onboard.py --ready --dry-run')

    def test_validation_runs_offline_suite_with_bounded_job(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/helm-validate.yml').read_text())
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['offline-regression']
        self.assertEqual(job['runs-on'], 'ubuntu-latest')
        self.assertLessEqual(job['timeout-minutes'], 15)
        commands = '\n'.join(step.get('run', '') for step in job['steps'])
        self.assertIn("python3 -m unittest discover -s tests -p 'test_*.py'", commands)
        self.assertNotIn('helm install', commands)
        self.assertNotIn('osm_onboard.py', commands)
        self.assertNotIn('kubectl ', commands)
        helm = next(step for step in job['steps'] if step.get('uses', '').startswith('azure/setup-helm@'))
        self.assertEqual(helm['with']['version'], 'v3.21.0')
