"""Deterministic OSM packages for the P4 KDU upgrade experiment (three revisions).

One KNF (p4kdu_knf) with two Helm KDUs, cu and du, and one NSD (p4kdu_ns). The
descriptors are byte-identical in every revision, so OSM v19 accepts each new
revision of the in-use KNF; only the charts change:

  revision 1  cu 0.1.0 r1        du 0.1.0 r1
  revision 2  cu 0.2.0 r2        du 0.2.0 r2
  revision 3  cu 0.3.0 r3 (slow) du 0.3.0 r3 (never ready: helm --atomic must roll back)

Archives use the repository's reproducible USTAR/gzip format.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from osm_packages import archive_bytes  # noqa: E402

KNF, NSD = 'p4kdu_knf', 'p4kdu_ns'
VNF_PROFILE = 'p4kdu'               # becomes the member-vnf-index of the KNF
KDUS = ('cu', 'du')
CHART_SOURCE = HERE / 'chart'
REVISIONS = {
    1: {'cu': {'version': '0.1.0', 'marker': 'r1'}, 'du': {'version': '0.1.0', 'marker': 'r1'}},
    2: {'cu': {'version': '0.2.0', 'marker': 'r2'}, 'du': {'version': '0.2.0', 'marker': 'r2'}},
    3: {'cu': {'version': '0.3.0', 'marker': 'r3', 'readyDelaySeconds': 45},
        'du': {'version': '0.3.0', 'marker': 'r3', 'broken': True}},
}

VNFD = {'vnfd': {
    'id': KNF, 'product-name': KNF, 'provider': '5g-kubernetes-p4', 'version': '1.0',
    'description': 'P4 isolated OSM v19 KDU upgrade experiment: dummy CU and DU Helm KDUs',
    'df': [{'id': 'default-df'}],
    'ext-cpd': [{'id': 'mgmt-ext', 'k8s-cluster-net': 'net1'}],
    'k8s-cluster': {'nets': [{'id': 'net1'}]},
    'kdu': [{'name': kdu, 'helm-chart': kdu} for kdu in KDUS],
    'mgmt-cp': 'mgmt-ext',
}}
NSD_DOC = {'nsd': {'nsd': [{
    'id': NSD, 'name': NSD, 'designer': '5g-kubernetes-p4', 'version': '1.0',
    'description': 'P4 isolated OSM v19 KDU upgrade experiment',
    'vnfd-id': [KNF],
    'virtual-link-desc': [{'id': 'net1', 'mgmt-network': True}],
    'df': [{'id': 'default-df', 'vnf-profile': [{
        'id': VNF_PROFILE, 'vnfd-id': KNF,
        'virtual-link-connectivity': [{
            'constituent-cpd-id': [{'constituent-base-element-id': VNF_PROFILE, 'constituent-cpd-id': 'mgmt-ext'}],
            'virtual-link-profile-id': 'net1'}]}]}],
}]}}


def _yaml(value):
    return yaml.safe_dump(value, sort_keys=True, default_flow_style=False).encode()


def chart_files(kdu, settings):
    """One KDU chart: the source templates with this revision's name, version and values."""
    chart = yaml.safe_load((CHART_SOURCE / 'Chart.yaml').read_text())
    chart.update(name='p4kdu-' + kdu, version=settings['version'], appVersion=settings['marker'])
    values = yaml.safe_load((CHART_SOURCE / 'values.yaml').read_text())
    values.update(component=kdu, **{k: v for k, v in settings.items() if k != 'version'})
    root = KNF + '/helm-chart-v3s/' + kdu + '/'
    files = {root + 'Chart.yaml': _yaml(chart), root + 'values.yaml': _yaml(values)}
    for template in sorted((CHART_SOURCE / 'templates').iterdir()):
        files[root + 'templates/' + template.name] = template.read_bytes()
    return files


def build(revision):
    """{archive name: bytes} for one revision."""
    if revision not in REVISIONS:
        raise ValueError('Unknown P4 package revision: ' + str(revision))
    files = {KNF + '/' + KNF + '_vnfd.yaml': _yaml(VNFD)}
    for kdu in KDUS:
        files.update(chart_files(kdu, REVISIONS[revision][kdu]))
    return {KNF + '.tar.gz': archive_bytes(KNF, files),
            NSD + '.tar.gz': archive_bytes(NSD, {NSD + '/' + NSD + '_nsd.yaml': _yaml(NSD_DOC)})}


def write_all(output):
    output = Path(output)
    manifest = {'knf': KNF, 'nsd': NSD, 'revisions': {}}
    for revision in sorted(REVISIONS):
        directory = output / ('rev' + str(revision))
        directory.mkdir(parents=True, exist_ok=True)
        archives = build(revision)
        for name, data in archives.items():
            (directory / name).write_bytes(data)
        manifest['revisions'][str(revision)] = {
            'archives': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(archives.items())},
            'charts': REVISIONS[revision]}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(write_all(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
