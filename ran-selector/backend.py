#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
import subprocess
import yaml
import os

app = Flask(__name__)

REPO_PATH = os.path.expanduser('~/5g-kubernetes')
CONFIG_FILE = f'{REPO_PATH}/ran-selector/active-ran.yaml'

def run_command(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=REPO_PATH)
    return result.returncode, result.stdout, result.stderr

def update_github(ran_type):
    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    config['active'] = ran_type
    config['srsran']['enabled'] = (ran_type == 'srsran')
    config['oai']['enabled'] = (ran_type == 'oai')
    config['oai']['components']['cu'] = (ran_type == 'oai')
    config['oai']['components']['du'] = (ran_type == 'oai')
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    run_command('git add ran-selector/active-ran.yaml')
    run_command(f'git commit -m "feat: Switch RAN to {ran_type} via RAN Selector UI"')
    run_command('git push origin main')
    return True

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(CONFIG_FILE), 'index.html')

@app.route('/api/deploy', methods=['POST'])
def deploy():
    data = request.json
    ran_type = data.get('ran')
    if ran_type not in ['srsran', 'oai', 'none']:
        return jsonify({'error': 'Invalid RAN type'}), 400
    try:
        update_github(ran_type)
        return jsonify({
            'status': 'success',
            'message': f'{ran_type} deployment triggered via GitOps',
            'ran': ran_type
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def status():
    code, out, err = run_command(
        'kubectl get pods -n free5gc | grep -E "srsran|oai"'
    )
    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    return jsonify({
        'active': config.get('active', 'none'),
        'pods': out.strip()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=True)
