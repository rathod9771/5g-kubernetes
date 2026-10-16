"""Research-only installation plan. No host, service or infrastructure mutations."""
import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

SANDBOX_ACTORS = {'researcher': ['researcher', 'builder', 'reader'],
                   'reviewer': ['reviewer', 'reader'],
                   'operator': ['operator', 'publisher', 'verifier', 'reader']}
SANDBOX_TENANT = 'platform-research'
SANDBOX_PORT = 5099
# The single source of truth for the interpreter the systemd unit launches
# with; the installer's pre-start dependency check imports this rather than
# repeating the literal, so the two can never drift apart.
RESEARCH_PYTHON = '/usr/bin/python3'


def ensure_sandbox(directory, repo_root):
    """Owner-only auth/runtime/policy fixtures for the research-only dashboard.

    Idempotent: an existing auth or policy file is never rewritten -- doing so
    would silently revoke already-issued researcher credentials or widen an
    operator-reviewed policy. Returns (paths, created_tokens); created_tokens
    is empty whenever the sandbox was already provisioned by an earlier run.
    The execution policy starts empty: no catalog/deployment target is
    enabled, so real catalog publication or live OSM lifecycle operations
    stay structurally denied until an operator separately approves and edits it.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = directory / 'runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    auth_path = directory / 'identities.json'
    policy_path = directory / 'execution-policy.json'
    created = {}
    try:
        fd = os.open(auth_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        entries = []
        for subject, roles in SANDBOX_ACTORS.items():
            token = secrets.token_hex(32)
            created[subject] = token
            entries.append({'subject': subject, 'tenant': SANDBOX_TENANT, 'roles': roles,
                             'sha256': hashlib.sha256(token.encode()).hexdigest()})
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(entries))
    try:
        fd = os.open(policy_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps({}))
    paths = {'TELECOM_AUTH_FILE': str(auth_path), 'TELECOM_RUNTIME_DIR': str(runtime),
             'TELECOM_WORKSPACE': str(Path(repo_root).resolve()), 'TELECOM_EXECUTION_POLICY': str(policy_path),
             'HELM_BIN': 'helm'}
    return paths, created


def render_research_service(repo_root, paths, user=None):
    """Loopback-only systemd unit text for the research-only dashboard."""
    user = user or getpass.getuser()
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*\$?', user):
        raise ValueError('Invalid service user name')

    def quote(value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Control characters are forbidden in service configuration')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

    launcher = Path(repo_root).resolve() / 'docs/telecom/p6/research_only.py'
    command = ':' + RESEARCH_PYTHON + ' ' + quote(str(launcher)) + ' --port ' + str(SANDBOX_PORT)
    lines = ['[Unit]', 'Description=5G telecom researcher dashboard (loopback-only, isolated sandbox)',
             'After=network-online.target', 'Wants=network-online.target', '',
             '[Service]', 'Type=simple', 'User=' + user,
             'WorkingDirectory=' + str(Path(repo_root).resolve()).replace('%', '%%')]
    for key, value in sorted(paths.items()):
        lines.append('Environment=' + key + '=' + quote(value))
    lines += ['ExecStart=' + command, 'Restart=always', 'RestartSec=10', '',
              '[Install]', 'WantedBy=multi-user.target', '']
    return '\n'.join(lines)


def plan():
    return {'mode':'research-only','execution':'PLAN_ONLY',
            'reuse':['Kubernetes','Rancher','OSM v19','Prometheus','Grafana'],
            'reference_workloads':False,'automatic_installation':False,
            'required_files':['TELECOM_AUTH_FILE','TELECOM_EXECUTION_POLICY'],
            'private_directories':['TELECOM_RUNTIME_DIR','TELECOM_WORKSPACE'],
            'start':'python3 docs/telecom/p6/research_only.py --port 5099',
            'bind':'127.0.0.1','production_service_restart':False,
            'buildkit_required_for_prebuilt_images':False,
            'platform_only_profile':{'excludes_reference_ran_core':True,
                'installer_entry_point':'./install.sh --platform-only-plan',
                'installer_mutation':False,'description':'Prints this plan and exits; never provisions or removes reference RAN/Core workloads.'},
            'lifecycle':['initial-instantiation: explicit independent approval',
                         'update: OFFLINE_TESTED guarded adapter; live execution NOT_CERTIFIED by default',
                         'rollback: OFFLINE_TESTED guarded adapter, limited to the immediately prior deployed release; live execution NOT_CERTIFIED by default']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['research-only'],required=True)
    parser.add_argument('--reuse-existing',action='store_true',required=True)
    parser.add_argument('--plan',action='store_true',required=True)
    parser.parse_args();print(json.dumps(plan(),indent=2))

if __name__=='__main__':main()
