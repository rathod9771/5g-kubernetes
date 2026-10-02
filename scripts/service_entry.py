"""Launch a service from a private validated snapshot, without shell evaluation."""
import os
from pathlib import Path
import sys
from runtime_config import ConfigError, load_config


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('dashboard', 'watcher', 'rancher-forward'):
        raise ConfigError('Invalid service launch request')
    kind, snapshot = sys.argv[1:]
    os.environ['P0_RUNTIME_CONFIG'] = snapshot
    cfg = load_config(require=('watcher',) if kind == 'watcher' else ())
    # Snapshot is authoritative. Strip inherited secret variables from subprocess environments.
    from runtime_config import SECRETS
    for name in SECRETS:
        os.environ.pop(name, None)
    if kind == 'rancher-forward':
        os.execvp('kubectl', ['kubectl', '--kubeconfig', cfg['KUBECONFIG_PATH'],
                  'port-forward', '-n', 'cattle-system', 'svc/rancher',
                  cfg['RANCHER_PORT'] + ':443', '--address', '0.0.0.0'])
    root = Path(cfg['REPO_ROOT'])
    binary, script = ((root / 'ran-selector/venv/bin/python3', root / 'ran-selector/backend.py')
                      if kind == 'dashboard' else (Path('/usr/bin/python3'), root / 'layer3-autonomous/watcher.py'))
    os.execv(str(binary), [str(binary), str(script)])


if __name__ == '__main__':
    try:
        main()
    except (ConfigError, OSError):
        sys.exit('ERROR: Service configuration/launch validation failed')
