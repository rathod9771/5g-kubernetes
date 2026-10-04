"""Render the existing split-OAI RF simulator UE path, at replicas zero.

Writes one private local manifest; never applies it or prints subscriber values.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

import yaml
from local_safety import atomic_write
from runtime_config import ROOT, load_config, private_text
from runtime_images import policy, reference
from scenario_registry import load_registry, select_scenarios
from osm_packages import chart_files


def private_destination(path, root=ROOT):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Private UE paths must not use symlinks')
    if path.is_relative_to(root) and not path.is_relative_to(root / '.runtime'):
        raise ValueError('Private UE inputs/output must be outside Git or under .runtime')
    return path


def render(subscriber, scenario, namespace, cfg, root=ROOT):
    if scenario['key'] not in ('cran-oai', 'vcran-oai', 'cloudran-oai') or scenario['generation_status'] != 'ready':
        raise ValueError('Simulated split UE requires a ready OAI scenario')
    if cfg['DEPLOYMENT_PROFILE'] != 'rfsim' or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', namespace):
        raise ValueError('Simulated UE requires rfsim and an explicit valid workload namespace')
    if not isinstance(subscriber, dict) or set(subscriber) != {'imsi', 'key', 'opc'}:
        raise ValueError('Private subscriber JSON requires exactly imsi, key and opc')
    if not all(isinstance(v, str) for v in subscriber.values()):
        raise ValueError('Subscriber input fields must be strings')
    if not re.fullmatch(r'[0-9]{15}', subscriber['imsi']) or not subscriber['imsi'].startswith(cfg['MCC'] + cfg['MNC']):
        raise ValueError('Subscriber IMSI must match the configured PLMN')
    if any(not re.fullmatch(r'[0-9a-fA-F]{32}', subscriber[k]) for k in ('key', 'opc')):
        raise ValueError('Subscriber key/opc must be 32 hexadecimal digits')
    if (cfg['DNN'], cfg['SST'], cfg['SD']) != ('internet', '1', '010203'):
        raise ValueError('Final simulator reference requires DNN internet / SST 1 / SD 010203')
    images = policy(root)['components']
    directory = Path(root) / 'deploy/oai-nr-ue'
    deployment = yaml.safe_load((directory / 'deployment-cran.yaml').read_text())
    pod = deployment['spec']['template']['spec']
    pod['initContainers'][0]['image'] = reference(images['ue-discovery'])
    pod['containers'][0]['image'] = reference(images['oai-ue'])
    du = next(c for c in scenario['charts'] if c['kdu'] == 'du')
    values = json.loads(chart_files(root, du)[0]['values.yaml'])
    for env in pod['initContainers'][0]['env']:
        if env['name'] == 'GNB_LABEL_SELECTOR':
            env['value'] = 'app=' + values['fullname']
    documents = list(yaml.safe_load_all((directory / 'rbac.yaml').read_text().replace('PLACEHOLDER_NAMESPACE', namespace)))
    config = ('uicc0 = {\n' + ''.join(f'  {k} = "{subscriber[k]}";\n' for k in ('imsi', 'key', 'opc')) +
              '  dnn = "internet";\n  nssai_sst = 1;\n  nssai_sd = 0x010203;\n};\n')
    documents.append(dict(apiVersion='v1', kind='Secret', metadata=dict(name='oai-nr-ue-config', namespace=namespace),
                          type='Opaque', stringData={'ue.conf': config}))
    for name, key, source in [('oai-nr-ue-cran-init', 'ue-init-cran.sh', 'ue-init-cran.sh'),
                              ('oai-nr-ue-cran-discovery-script', 'discover_gnb.py', 'discover_gnb_cran.py')]:
        documents.append(dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name=name, namespace=namespace),
                              data={key: (directory / source).read_text()}))
    deployment['metadata']['namespace'] = namespace
    documents.append(deployment)
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', required=True, choices=['cran-oai', 'vcran-oai', 'cloudran-oai'])
    parser.add_argument('--subscriber-input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cfg = load_config()
    source = private_destination(args.subscriber_input)
    text, mode = private_text(source, required=True, strict=True)
    if mode & 0o077 or source.stat().st_uid != os.getuid():
        raise ValueError('Subscriber input must be owner-only and owned by the invoking user')
    subscriber = json.loads(text)
    scenario = select_scenarios(load_registry(), [args.scenario])[0]
    documents = render(subscriber, scenario, cfg['OSM_PROJECT_NAMESPACE'], cfg)
    output = private_destination(args.output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(output, yaml.safe_dump_all(documents, sort_keys=False).encode())
    print('Private simulated-UE manifest prepared at replicas zero; no resources changed')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit('ERROR: private simulated-UE preparation failed; check input permissions, profile and required fields (values suppressed)')
