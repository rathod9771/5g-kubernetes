"""Explicit onboarding of prepared immutable bytes. Never invoked by local tests."""
import argparse
import os
from osm_packages import REPO_ROOT, DEFAULT_OUTPUT, prepare, validated_snapshot, PackageError
from scenario_registry import load_registry, select_scenarios
from osm_catalog import Catalog, CatalogError

def onboard(catalog, scenarios, snapshots):
    # Bytes already validated: never reopen mutable archive paths during upload.
    for kind, field in [('knf', 'knf_package'), ('ns', 'nsd_package')]:
        for scenario, artifacts in zip(scenarios, snapshots):
            name = scenario[field]
            catalog.upload(kind, name, artifacts[name + '.tar.gz'])

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenarios', nargs='*')
    parser.add_argument('--ready', action='store_true')
    parser.add_argument('--output', type=__import__('pathlib').Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        scenarios = select_scenarios(load_registry(), args.scenarios, args.ready)
        prepare(REPO_ROOT, args.output, scenarios)
        snapshots = [validated_snapshot(REPO_ROOT, args.output, s) for s in scenarios]
        password = os.environ.get('OSM_PASSWORD')
        if not password:
            raise CatalogError('Set OSM_PASSWORD')
        catalog = Catalog(os.environ.get('OSM_HOST', 'https://gui.172.30.18.32.nip.io:30843'))
        catalog.authenticate('admin', password)
        onboard(catalog, scenarios, snapshots)
        for scenario, artifacts in zip(scenarios, snapshots):
            catalog.verify(scenario, artifacts)
        return 0
    except (PackageError, CatalogError, OSError, ValueError) as error:
        parser.exit(1, 'ERROR: ' + str(error) + '\n')

if __name__ == '__main__':
    raise SystemExit(main())
