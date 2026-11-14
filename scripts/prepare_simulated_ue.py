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
from osm_packages import chart_files, render as render_chart
from runtime_images import chart_values


def private_destination(path, root=ROOT):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Private UE paths must not use symlinks')
    if path.is_relative_to(root) and not path.is_relative_to(root / '.runtime'):
        raise ValueError('Private UE inputs/output must be outside Git or under .runtime')
    return path


SCENARIOS = ('cran-oai', 'vcran-oai', 'cloudran-oai', 'hcran-oai', 'fran-oai')


def selected_du(scenario, cell):
    if scenario['key'] == 'hcran-oai':
        if cell not in ('macro', 'small'):
            raise ValueError('H-CRAN UE requires an explicit macro or small cell')
        role = cell
    else:
        if cell not in (None, 'du'):
            raise ValueError('This scenario supports only the edge/default DU')
        role = 'du'
    return next(c for c in scenario['charts'] if c['kdu'] == role)


def validate_experimental_carrier(root, chart):
    # Reuse only the known split-RFSIM carrier; fail closed on future drift.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        images = Path(directory) / 'images.json'
        images.write_text(json.dumps(chart_values(root, chart)))
        docs = render_chart(Path(root)/chart['source'], chart['release_name'],
                            [Path(root)/p for p in chart['values_files']] + [images])
    text = next(d for d in docs if d['kind']=='ConfigMap')['data']['gnb.conf']
    expected = {'absoluteFrequencySSB':630048, 'dl_frequencyBand':78,
                'dl_absoluteFrequencyPointA':628776, 'dl_subcarrierSpacing':1,
                'dl_carrierBandwidth':106, 'ul_frequencyBand':78,
                'ul_subcarrierSpacing':1, 'ul_carrierBandwidth':106, 'serverport':4043}
    for key, value in expected.items():
        if not re.search(r'\b'+key+r'\s*=\s*'+str(value)+r'\s*;',text):
            raise ValueError('Experimental DU carrier is incompatible with the approved UE configuration')
    if 'serveraddr = "server"' not in text:
        raise ValueError('Experimental DU must provide an RF simulator server')


def render(subscriber, scenario, namespace, cfg, root=ROOT, cell=None):
    if scenario['key'] not in SCENARIOS or scenario['generation_status'] != 'ready':
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
    du = selected_du(scenario, cell)
    values = json.loads(chart_files(root, du)[0]['values.yaml'])
    if scenario.get('experimental'):
        if values.get('rf',{}).get('mode') != 'rfsim':
            raise ValueError('Experimental UE requires a DU RF simulator')
        validate_experimental_carrier(root, du)
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
    if scenario.get('experimental'):
        name = 'oai-nr-ue-' + scenario['key'] + '-' + du['kdu']
        replacements = {'oai-nr-ue-cran':name, 'oai-nr-ue':name+'-sa',
                        'oai-nr-ue-pod-reader':name+'-reader',
                        'oai-nr-ue-config':name+'-config',
                        'oai-nr-ue-cran-init':name+'-init',
                        'oai-nr-ue-cran-discovery-script':name+'-discovery'}
        def rename(value):
            if isinstance(value,str):return replacements.get(value,value)
            if isinstance(value,list):return [rename(v) for v in value]
            if isinstance(value,dict):return {k:rename(v) for k,v in value.items()}
            return value
        documents = rename(documents)
        for document in documents:
            if document['kind']=='Deployment':
                document['metadata'].setdefault('annotations',{})['5g-kubernetes/ue-cell'] = du['kdu']
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', required=True, choices=SCENARIOS)
    parser.add_argument('--cell', choices=['macro','small','du'], help='Required macro/small selection for H-CRAN')
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
    documents = render(subscriber, scenario, cfg['OSM_PROJECT_NAMESPACE'], cfg, cell=args.cell)
    output = private_destination(args.output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(output, yaml.safe_dump_all(documents, sort_keys=False).encode())
    print('Private simulated-UE manifest prepared at replicas zero; no resources changed')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit('ERROR: private simulated-UE preparation failed; check input permissions, profile and required fields (values suppressed)')
